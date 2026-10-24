"""The v4 supervisor command surface: remote price/tactic commands by webhook.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. The dispatcher is a
module-level helper; the two endpoints register on the bare app (Twilio form
callback) and on /api/v1 (in-app command execution).
"""
from __future__ import annotations
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.config.heuristics import STAGNATION_PREMIUM, SUPERVISOR_MIN_PRICE_RATIO
from src.app.security import require_webhook_token
from ..session.schema import AutopilotState, RemoteAuthCommand
from ..transport.webhook_hub import hub
from .state import (
    _extract_mold_fee,
    _legacy_route,
    _validate_session_id_path,
    session_mgr,
)


class MobileCommandBody(BaseModel):
    session_id: str = "default_session"
    command: str                        # "1", "2", "3" 或 "13.2"
    source: str = "webhook"


# 高管指令（手機/擴充）以「裸數字」覆寫價格時的最低授權下限。
# [REGRESSION] EnglishExecutiveCommandParser 會把任何落在 [0.1, 50000] 的裸數字
# 解讀成 OVERRIDE_PRICE，而 _dispatch_supervisor_command 過去只檢查**上限**
# （ActionObjectASTGuardrail 也只看 auth_p <= ceiling），因此「主管可以單方面
# 承諾任意低價」。低於此比例（目標價的 50%）的覆寫一律拒絕，必須改走
# /approve 的人工覆寫路徑（有明確的 action="override" 與理由可稽核）。
def _dispatch_supervisor_command(
    session_id: str,
    user_command: str,
    expected_version: Optional[int] = None,
) -> dict:
    from ..engine.bilingual import (
        EnglishExecutiveCommandParser,
        ExecutiveActionEnum,
        TacticalActionObject,
        TacticalModeEnum,
        VendorTacticEnum,
        ActionObjectASTGuardrail,
        ChineseSurfaceRenderer,
        EnglishExecutiveRenderer,
        CURRENT_SCHEMA_VERSION,
        GuardrailViolationError,
    )
    parsed_cmd = EnglishExecutiveCommandParser.parse(user_command)
    # 先看有沒有等待授權的橋樑（bridge）：有 bridge 代表確實有一個掛起中的
    # 熔斷會話在等這個指令，即使它沒有被持久化成 session 也該算成功。
    had_bridge = hub.get_bridge(session_id) is not None
    cmd, custom_price = hub.dispatch_command(session_id, user_command)
    # [REGRESSION] dispatch_command 路徑（與 wait_for_auth 的 finally 不同）
    # 從不 unregister，_bridges 會隨每個指令的 session_id 無界成長。派發後即釋放。
    hub.unregister_bridge(session_id)
    sess = session_mgr.get_session(session_id)

    reply_script_zh = None
    confirmation_card_en = None
    action_obj_dump = None
    source_round_id = f"{session_id}_EXEC"

    # [REGRESSION] 用於誠實回報「指令是否真的產生狀態轉移」。舊版無論如何都回
    # status="success"，因此 WALK_AWAY 在非熔斷狀態下是 no-op 卻仍回報成功。
    applied = False
    already_terminal = False
    note: Optional[str] = None

    # [REGRESSION] 橋樑被解鎖**本身就是已生效的效果**：它釋放了正在
    # `wait_for_auth` 等待授權的協程。舊版只看 `sess` 內的狀態轉移，因此在
    # 「有 bridge 但沒有持久化 session」的情況下（熔斷會話尚未落庫）會回報
    # status="ignored" —— 與本函式下方註解「即使沒有持久化 session 也該算成功」
    # 自相矛盾，並且讓真正的人工授權流程被誤報為未生效。
    if had_bridge:
        applied = True

    # [修正] 舊版在 sess 為 None 時直接跳過整個 if 區塊，卻仍回傳
    # `"status": "success"`，因此對一個**不存在的 session** 發指令也會得到
    # HTTP 200 + success，且所有 payload 欄位為 null。呼叫端（與自動化測試）
    # 無法察覺指令其實沒有作用。
    # 例外：若存在等待授權的 bridge（熔斷中的會話正在等這個指令），
    # 該指令確實被派發並解鎖，即使沒有持久化 session 也應視為成功。
    if sess is None and not had_bridge:
        return {
            "status": "error",
            "session_id": session_id,
            "resolved_command": cmd.value,
            "detail": f"No session '{session_id}' exists and no pending authorization bridge; command was not applied.",
            "action": None,
            "reply_script_zh": None,
            "confirmation_card_en": None,
            "action_object": None,
        }

    # [假資料修正 §5.7] metrics_note 在 `sess` 為 None（僅有 bridge）時也必須有
    # 定義，否則末端回傳會 NameError。預設語意＝「沒有任何引擎輪次可沿用」。
    metrics_note = (
        "No engine round has been recorded for this session yet; Bayesian "
        "risk/confidence/floor and expected margin were NOT computed."
    )

    if sess:
        source_round_id = f"{session_id}_R{sess.current_round}_EXEC"

        # [修正] 舊版在 renderer 端填入與引擎無關的常數，並使用與引擎相同的欄位名：
        #   breakdown_risk  = 0.05 / 0.12（寫死）
        #   bayesian_confidence 未傳 -> 落到 Pydantic 預設 0.85
        #   estimated_floor_price 未傳 -> 渲染成 "N/A"
        #   expected_margin = ceiling - settled（那是「剩餘預算空間」，不是邊際利潤）
        # 同一 session 走 /round 得到的是 margin 2.77 / risk 0.013 / conf 0.812，
        # 走本端點卻得到 4.2 / 0.12 / 0.85 —— 兩張外觀相同的 CPO 決策卡互相矛盾。
        # 現在改為沿用該 session 最近一輪的**真實**數值（若歷史存在）；
        # 若無歷史輪次，則誠實地用 detail 標示「尚無輪次、未計算風險」，
        # 而不是吐一個沒有依據的常數。
        last_turn = sess.history[-1] if sess.history else None
        real_p_break = float(getattr(last_turn, "p_break", 0.0) or 0.0) if last_turn else None
        real_post_mean = float(getattr(last_turn, "posterior_mean", 1.0) or 1.0) if last_turn else None
        has_history = real_p_break is not None

        # [假資料修正 §5.7] 以下四個值原本在此端點沒有任何真實來源：
        #   * bayesian_confidence 寫死 0.85（Pydantic 預設值被當成測量值）
        #   * estimated_floor_price 未傳 -> 卡片渲染 "N/A"
        #   * expected_margin 用 `ceiling - settled`（剩餘預算空間）冒充單件毛利，
        #     健康 session（ceiling 16.2 / settled 15.80）只顯示 ¥0.40
        #   * posterior_reserve_std 未傳，卡片無法判斷信念分散度
        # /round 已把這些值寫進 turn.metadata（見上方第 3 節），此處只讀不改；
        # 缺值時一律回 None，由 renderer 顯示 "N/A" 而不是退回任何常數。
        _turn_meta = (getattr(last_turn, "metadata", None) or {}) if last_turn is not None else {}

        def _real_float(key: str):
            """從最近一輪 metadata 取真實值；型別不對／缺值一律回 None（不虛構）。"""
            val = _turn_meta.get(key) if isinstance(_turn_meta, dict) else None
            if val is None or isinstance(val, bool):
                return None
            try:
                fval = float(val)
            except (TypeError, ValueError):
                return None
            if fval != fval or fval in (float("inf"), float("-inf")):
                return None
            return fval

        real_bayesian_confidence = _real_float("bayesian_confidence")
        real_expected_margin = _real_float("expected_margin")
        real_floor_price = _real_float("estimated_floor_price")

        # [誠實揭露] 本端點不重跑引擎，因此「尚無任何輪次」時 risk / confidence /
        # floor / margin 全部沒有來源。舊版在無歷史時填 0.0 常數，讓主管以為
        # 「已計算且結果為零」。現在一律 None，卡片顯示 N/A + detail 說明。
        metrics_note = (
            "Metrics below are carried over from this session's most recent engine round."
            if has_history
            else "No engine round has been recorded for this session yet; "
                 "Bayesian risk/confidence/floor and expected margin were NOT computed."
        )
        # [假資料修正 §5.5] 取該 session 對方**實際報出**的模具費（分類器寫入
        # turn metadata 的 entities）；取不到即 0，不虛構。
        inbound_mold_fee = None
        if last_turn is not None:
            _ent = (getattr(last_turn, "metadata", None) or {}).get("entities", {})
            if isinstance(_ent, dict):
                inbound_mold_fee = _ent.get("mold_fee")
        if inbound_mold_fee is None:
            _raw = (getattr(last_turn, "raw_text", "") or "")
            inbound_mold_fee = _extract_mold_fee(_raw)

        # [REGRESSION] 低價下限：監督端以裸數字覆寫價格時的授權下限。
        min_authorized_price = round(sess.spec.target_price * SUPERVISOR_MIN_PRICE_RATIO, 2)
        if custom_price is not None and custom_price < min_authorized_price - 1e-9:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Supervisor override price ¥{custom_price:.2f} is below the authorized "
                    f"lower bound ¥{min_authorized_price:.2f} "
                    f"({SUPERVISOR_MIN_PRICE_RATIO:.0%} of target price ¥{sess.spec.target_price:.2f}). "
                    "Use the /approve endpoint with an explicit 'override' action and rationale "
                    "to commit below this bound."
                ),
            )

        # 計算結算價格
        if custom_price:
            settled_price = custom_price
        elif parsed_cmd.action == ExecutiveActionEnum.CONCEDE:
            # 讓步時優先接受供應商當前報價（若有），否則退回目標價 +5% 的保守步進。
            if sess.latest_opponent_offer is not None:
                settled_price = round(min(sess.latest_opponent_offer, sess.spec.ceiling_price), 2)
            else:
                settled_price = round(min(sess.spec.target_price * STAGNATION_PREMIUM, sess.spec.ceiling_price), 2)
        else:
            settled_price = sess.spec.target_price

        if parsed_cmd.action == ExecutiveActionEnum.CONCEDE:
            t_mode = TacticalModeEnum.LEAN_IN
        elif parsed_cmd.action == ExecutiveActionEnum.HOLD_FIRM:
            t_mode = TacticalModeEnum.FIRM_HOLD
        elif parsed_cmd.action == ExecutiveActionEnum.WALK_AWAY:
            t_mode = TacticalModeEnum.WALK_AWAY
        else:
            t_mode = TacticalModeEnum.LOGROLL if parsed_cmd.waive_mold_fee else TacticalModeEnum.LEAN_IN

        # [假資料修正 §5.8] 從「對方實際報出」的條款取真實值（turn.metadata entities）。
        # 取不到為 None —— 此處 None 代表「未觀測」，不得以任何常數冒充。
        _turn_entities = _turn_meta.get("entities") if isinstance(_turn_meta, dict) else None
        if not isinstance(_turn_entities, dict):
            _turn_entities = {}
        _turn_meta_lead_days = _turn_entities.get("lead_days")
        _turn_meta_deposit_pct = _turn_entities.get("deposit_pct")

        # 我方交期條款：主管指令明確要求 > 本 session 記錄到的對方實際報值 > 未觀測。
        # 「未觀測」時不填任何數字（保持 Pydantic 預設），因為這兩個欄位的語意是
        # 「我方承諾/要求的條件」，不是「對方報出的條件」。
        _our_lead_days = (
            parsed_cmd.demanded_lead_days
            if parsed_cmd.demanded_lead_days is not None
            else _turn_meta_lead_days
        )
        _our_deposit_pct = (
            parsed_cmd.demanded_deposit_pct
            if parsed_cmd.demanded_deposit_pct is not None
            else _turn_meta_deposit_pct
        )
        _our_terms: dict[str, Any] = {}
        if _our_lead_days is not None:
            _our_terms["lead_days"] = _our_lead_days
        if _our_deposit_pct is not None:
            _our_terms["deposit_pct"] = _our_deposit_pct

        action_obj = TacticalActionObject(  # type: ignore[arg-type]
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id=source_round_id,
            authorized_price=settled_price,
            walk_away_ceiling=sess.spec.ceiling_price,
            # 沿用最近一輪的真實 p_break；無歷史時不留假常數，
            # 用 breakdown_risk=0.0 並在 detail 註明「尚無輪次」。
            breakdown_risk=real_p_break if has_history else 0.0,  # type: ignore[arg-type]
            # [假資料修正 §5.7] expected_margin 只能是**引擎算出的單件毛利**
            # （turn.metadata 的 expected_margin）。舊版用 `ceiling - settled`
            # 冒充單件毛利：那是「距離天花板的剩餘預算空間」，方向與語意都錯，
            # 主管看到的 ¥0.40 不是利潤而是預算緩衝。若歷史沒有這個值
            # （例如舊 session 或 /approve 人工輪），就用 0.0 —— 它是該欄位的
            # 「未取得」表示，renderer 不會把 0.0 說成毛利。
            expected_margin=real_expected_margin if real_expected_margin is not None else 0.0,
            posterior_reserve_mean=real_post_mean if has_history else 1.0,  # type: ignore[arg-type]
            # bayesian_confidence 沿用真實值；無來源時 None -> renderer 顯示 N/A。
            bayesian_confidence=(
                real_bayesian_confidence if real_bayesian_confidence is not None else 0.0
            ),
            # estimated_floor_price 沿用最近一輪真實估算；無來源時留 None，
            # 卡片印 "N/A" 而不是臆造一個數字。
            estimated_floor_price=real_floor_price,
            tactical_mode=t_mode,
            product_name=sess.spec.product_name,
            supplier_contact_name="老闆",
            # [假資料修正 §5.8] lead_days / deposit_pct 的語意是「我方承諾/要求的
            # 條件」（TacticalActionObject 欄位說明即「承諾交期天數」「承諾定金
            # 比例」），不是「對方報出的條件」。舊版以 `or 25` / `or 0.30` 補值，
            # 於是把買方側假設冒充成對手開出的條件。
            # 現在只在有真實來源時填值（主管指令明確要求，或本 session 曾記錄到對方
            # 實際報出的條款）；兩者皆無時不填，讓 Pydantic 預設值生效 ——
            # 該預設值不構成任何觀測，且不得被對外文本以「貴廠開出」語氣引述。
            # [TYPING 2026-10-24] mypy 無法透過 **-spread 表達「僅這兩個可選欄位」，
            # 故以顯式的 dict[str, Any] 收納：鍵的正確性由下方兩個條件設值保證，
            # cast 只承認這個既有契約，不弱化其他任何檢查。
            **_our_terms,
            moq=sess.spec.moq,
            mold_fee_exempted=parsed_cmd.waive_mold_fee,
            # [假資料修正 §5.5] 舊版寫死 3000.0：主管若指令含「waive tooling」，
            # 系統就憑空宣稱減免一筆 ¥3000。現改為取該 session 實際記錄到的
            # 對方模具費；取不到就為 0（不虛構金額，改由話術只表達「免除」）。
            waive_mold_fee_amount=float(
                (inbound_mold_fee or 0.0)
            ) if parsed_cmd.waive_mold_fee else 0.0,
            counterpart_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
        )

        # AST 護欄驗證。
        # [REGRESSION] 舊版直接呼叫、無 try/except。主管若把 deposit_pct 或價格
        # 送成越界值，GuardrailViolationError 會一路穿出路由變成 HTTP 500（並洩漏
        # 內部 traceback）。安全護欄應該阻斷，而不是讓端點崩潰。
        try:
            ActionObjectASTGuardrail.verify(action_obj)
        except GuardrailViolationError as gv:
            raise HTTPException(
                status_code=422,
                detail=f"Supervisor command violates a hard mandate invariant: {gv}",
            ) from gv

        # 雙向表面渲染
        reply_script_zh = ChineseSurfaceRenderer.render(action_obj)
        confirmation_card_en = EnglishExecutiveRenderer.render_executive_confirmation_card(
            action_obj, command_executed=parsed_cmd.action.value
        )
        action_obj_dump = action_obj.model_dump()

        # [REGRESSION] 舊版只在 sess.state == CIRCUIT_TRIPPED_ESCALATED 時才做狀態
        # 轉移，於是 AUTOPILOT_CRUISING 狀態下送 command="3" (WALK_AWAY) 會回
        # status="success" / action="walk_away" 而會話仍留在 AUTOPILOT_CRUISING ——
        # 手機主管以為已中止談判，實際上仍在巡航並可繼續成交。
        # 現在 WALK_AWAY 從**每一個非終止狀態**真正撤退，且不再回報假成功。
        if cmd == RemoteAuthCommand.WALK_AWAY:
            if sess.state == AutopilotState.WALK_AWAY:
                applied = True
                already_terminal = True
                note = "Session is already WALK_AWAY; abort is idempotent."
            elif sess.state == AutopilotState.DEAL_CLOSED:
                note = (
                    "Session is DEAL_CLOSED (terminal); a walk-away abort cannot be "
                    "applied after the deal was closed."
                )
            else:
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.WALK_AWAY,
                    reason="Remote supervisor aborted negotiation (WALK_AWAY command).",
                    expected_version=expected_version,
                )
                applied = True
        else:
            # 非撤退指令：只有熔斷/等待遠端授權的會話需要「解鎖」回巡航。
            # 其他狀態下指令的效果是產生決策卡（已渲染），不改 FSM 狀態；明確揭露。
            if sess.state in (
                AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
                AutopilotState.AWAITING_REMOTE_AUTH,
            ):
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.AUTOPILOT_CRUISING,
                    reason=f"Remote unlocked via command {cmd.value}",
                    expected_version=expected_version,
                )
                applied = True
            elif sess.state == AutopilotState.AWAITING_HUMAN_DECISION:
                note = (
                    "This deal is waiting for a person to approve, hold, or reject. "
                    "The remote command was not applied."
                )
            elif sess.state in (AutopilotState.DEAL_CLOSED, AutopilotState.WALK_AWAY):
                note = (
                    f"Session is in terminal state {sess.state.value}; the command produced "
                    "a decision card but cannot change the state machine."
                )
            elif had_bridge:
                # 橋樑已解鎖 = 已生效；只是該 session 的 FSM 不在熔斷狀態，所以
                # 沒有額外的狀態轉移。明確說明，但不得回報 ignored。
                note = (
                    f"Pending authorization bridge unlocked; session FSM stayed in "
                    f"{sess.state.value} (no circuit-breaker state to clear)."
                )
            else:
                note = (
                    f"Session is in state {sess.state.value}; command produced a decision card "
                    "without a state transition (no pending authorization was open)."
                )

    action_name = "concede_step" if cmd == RemoteAuthCommand.CONCEDE else cmd.value.lower()
    return {
        # [修正] 舊版無論如何都回 status="success"。現在只有真的產生效果（或本來
        # 就是幂等的終止狀態）才回 success；狀態未改變時回 ignored + 說明，
        # 讓呼叫端不會誤以為指令已生效。
        "status": "success" if applied else "ignored",
        "session_id": session_id,
        "resolved_command": cmd.value,
        "source_round_id": source_round_id,
        "custom_price": custom_price,
        "action": action_name,
        "state_transition_applied": applied,
        "already_terminal": already_terminal,
        "detail": note,
        "reply_script_zh": reply_script_zh,
        "confirmation_card_en": confirmation_card_en,
        "action_object": action_obj_dump,
        # [修正] 誠實揭露：此端點不計算新的引擎輪次。risk/confidence/floor/margin
        # 皆沿自最近一輪已持久化的真實值；若該 session 尚無任何輪次，或該輪
        # 沒有記錄到某個值，卡片上對應欄位顯示 N/A，而非任何常數。
        "metrics_source": "last_round_history" if sess and sess.history else "none",
        "metrics_note": metrics_note if (sess and sess.history) else (
            "No engine round has been recorded for this session yet; Bayesian "
            "risk/confidence/floor and expected margin were NOT computed."
        ),
    }


class SessionCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Optional[str] = Field(default=None, max_length=512)
    command_text: Optional[str] = Field(default=None, max_length=512)
    source: str = Field(default="mobile", max_length=64)



def register_command_routes(app, api_v1) -> None:
    @app.post("/webhook/mobile-command", dependencies=[Depends(require_webhook_token)])
    async def receive_mobile_command(request: Request):
        """接收手機端（WhatsApp Twilio / Telegram / 微信 Webhook）指令回調。

        [REGRESSION] 本端點原本無認證。它直接呼叫 _dispatch_supervisor_command ->
        hub.dispatch_command，後者只要有人送 "1" 就 bridge.unlock(...) —— 任何人只要
        知道/猜到 session_id 就能偽造主管核准，把已熔斷的會話解鎖回
        AUTOPILOT_CRUISING，而這正是專案的核心安全機制（合規人機審批門禁）。
        現在必須帶 X-Webhook-Token（production 為強制 fail-closed）。
        """
        session_id = "default_session"
        user_command = ""

        # 1. 嘗試解析 Form 資料 (Twilio WhatsApp Webhook 標準)
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            user_command = str(form.get("Body", "")).strip()
            session_id = str(form.get("session_id", "default_session"))
        else:
            # 2. 嘗試解析 JSON 資料 (Telegram / 自訂指令)
            try:
                body = await request.json()
                user_command = str(body.get("command") or body.get("command_text") or "").strip()
                session_id = str(body.get("session_id", "default_session"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid request body format.") from exc

        if not user_command:
            raise HTTPException(status_code=400, detail="Missing command in payload.")

        try:
            return _dispatch_supervisor_command(session_id, user_command)
        except KeyError as e:
            return {"status": "ignored", "reason": str(e)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


    @_legacy_route(api_v1.post)("/sessions/{session_id}/command")
    def execute_session_command(session_id: str, body: SessionCommandBody):
        """執行特定會話的高管授權或決策指令。"""
        _validate_session_id_path(session_id)
        user_command = (body.command or body.command_text or "").strip()
        if not user_command:
            raise HTTPException(status_code=400, detail="Missing command.")
        try:
            return _dispatch_supervisor_command(session_id, user_command)
        except KeyError as e:
            return {"status": "ignored", "reason": str(e)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


