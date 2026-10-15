"""TradeFlow app — FastAPI 入口 (v4 全自主自駕與遙測擴充)。

職責：
1. 向後相容：保留 `/health`, `/negotiate`, `/webhook/evolus`。
2. 跨域支援 (CORS)：允許 Chrome 擴充套件 (`chrome-extension://`) 與前端直連。
3. 雙向指令：提供 `/webhook/mobile-command` 接收 WhatsApp / 微信 / TG 回調。
4. 會話生命週期：提供 `/api/v1/sessions` 進行狀態機驅動、即時求解與熔斷遙測。
"""
from __future__ import annotations
import os
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict

from ..engine import (
    solve,
    recommend,
    bayes_update,
    compute_belief_confidence,
    robustness,
    model,
    proposal,
    robust_bayes_update,
    compute_calibrated_deal_prob,
    BayesianBeliefState,
    TCOEngine,
)
from ..session.schema import (
    SessionSpec,
    NegotiationSession,
    NegotiationTurn,
    AutopilotState,
    RemoteAuthCommand,
)
from ..session.manager import SessionManager, InvalidStateTransitionError, OptimisticLockConflictError
from ..transport.webhook_hub import hub
from ..transport.telemetry import notifier
from . import orchestrator

app = FastAPI(title="TradeFlow Negotiation Engine", version="0.4.0")

# 開放 CORS 給 Chrome 擴充套件與本機除錯前端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域會話管理器實例
session_mgr = SessionManager()

# ---- 安全與認證門禁 (Fail-Closed Default-Deny Security Gate) ----
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

SANDBOX_DEMO_TOKEN = "SANDBOX-DEMO-TOKEN"
ENV_MODE = os.getenv("TRADEFLOW_ENV", "sandbox").lower()


def require_api_key(api_key: Optional[str] = Depends(api_key_header)):
    """API 安全金鑰驗證門禁 (Fail-Closed 默認拒絕規範).
    
    1. 演練沙盒模式 (TRADEFLOW_ENV=sandbox, 預設):
       支援黑客松評委演示、前端無痛演練與 pytest 單元測試。
       若配置了密鑰則強制驗證密鑰或 SANDBOX_DEMO_TOKEN，未配置時標記沙盒訪問放行。
    2. 生產模式 (TRADEFLOW_ENV=production):
       Fail-Closed: 若未設置 TRADEFLOW_API_KEY (或長度 < 16) 則直接阻斷存取 (500)；
       未攜帶或攜帶非法密鑰則直接阻斷 (401)，杜絕未受控裸奔。
    """
    if ENV_MODE == "sandbox":
        configured_key = os.getenv("TRADEFLOW_API_KEY")
        if configured_key:
            if not api_key or (api_key.strip() != configured_key.strip() and api_key.strip() != SANDBOX_DEMO_TOKEN):
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: Invalid X-API-Key header in sandbox mode. Access denied.",
                )
        return api_key or SANDBOX_DEMO_TOKEN

    # 生產模式：Fail-Closed
    configured_key = os.getenv("TRADEFLOW_API_KEY")
    if not configured_key or len(configured_key.strip()) < 16:
        raise HTTPException(
            status_code=500,
            detail="Security Violation: System is in production mode but TRADEFLOW_API_KEY is not configured (Fail-Closed).",
        )
    if not api_key or api_key.strip() != configured_key.strip():
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-API-Key header in production mode. Access denied.",
        )
    return api_key


# ---- 既有端點 (向後相容) ----

class Spec(BaseModel):
    product: str = "auto umbrella"
    target_price: float = 12.0          # ¥ per unit
    moq: int = 500
    rounds: int = model.ROUNDS
    n_samples: int = 20_000
    lambda_break: float = 1.0
    prior_reserve_mean: float = 1.08
    prior_reserve_std: float = 0.10
    observed_offer: float | None = None  # 供應商回價（歸一化，1.0=目標價）


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.4.0"}


@app.post("/negotiate")
def negotiate(spec: Spec):
    post_mean, post_std = spec.prior_reserve_mean, spec.prior_reserve_std
    if spec.observed_offer is not None:
        post_mean, post_std = bayes_update(post_mean, post_std, spec.observed_offer)
    try:
        res = solve(
            n_opp_samples=spec.n_samples,
            use_gpu=False,
            lambda_break=spec.lambda_break,
            reserve_mean=post_mean,
            reserve_std=post_std,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    rec = recommend(res, target_price=spec.target_price)
    rec["posterior_reserve_mean"] = float(post_mean)
    rec["posterior_reserve_std"] = float(post_std)
    return rec


@app.post("/webhook/evolus")
def webhook_evolus(payload: dict):
    """Evolus workflow 回呼入口（email-in → extractor → 這裡）。"""
    try:
        return orchestrator.on_webhook(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- v4 雙向遠端指令端點 ----

class MobileCommandBody(BaseModel):
    session_id: str = "default_session"
    command: str                        # "1", "2", "3" 或 "13.2"
    source: str = "webhook"


def _dispatch_supervisor_command(session_id: str, user_command: str) -> dict:
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
    )
    parsed_cmd = EnglishExecutiveCommandParser.parse(user_command)
    cmd, custom_price = hub.dispatch_command(session_id, user_command)
    sess = session_mgr.get_session(session_id)

    reply_script_zh = None
    confirmation_card_en = None
    action_obj_dump = None
    source_round_id = f"{session_id}_EXEC"

    if sess:
        source_round_id = f"{session_id}_R{sess.current_round}_EXEC"
        # 計算結算價格
        if custom_price:
            settled_price = custom_price
        elif parsed_cmd.action == ExecutiveActionEnum.CONCEDE:
            settled_price = round(sess.spec.target_price * 1.05, 2)
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

        action_obj = TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id=source_round_id,
            authorized_price=settled_price,
            walk_away_ceiling=sess.spec.ceiling_price,
            breakdown_risk=0.05 if parsed_cmd.action == ExecutiveActionEnum.CONCEDE else 0.12,
            expected_margin=round(max(0.0, sess.spec.ceiling_price - settled_price), 2),
            tactical_mode=t_mode,
            product_name=sess.spec.product_name,
            supplier_contact_name="老闆",
            lead_days=parsed_cmd.demanded_lead_days or 25,
            deposit_pct=parsed_cmd.demanded_deposit_pct or 0.30,
            moq=sess.spec.moq,
            mold_fee_exempted=parsed_cmd.waive_mold_fee,
            waive_mold_fee_amount=3000.0 if parsed_cmd.waive_mold_fee else 0.0,
            counterpart_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
        )

        # AST 護欄驗證
        ActionObjectASTGuardrail.verify(action_obj)

        # 雙向表面渲染
        reply_script_zh = ChineseSurfaceRenderer.render(action_obj)
        confirmation_card_en = EnglishExecutiveRenderer.render_executive_confirmation_card(
            action_obj, command_executed=parsed_cmd.action.value
        )
        action_obj_dump = action_obj.model_dump()

        if sess.state == AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
            if cmd == RemoteAuthCommand.WALK_AWAY:
                session_mgr.transition_state(session_id, AutopilotState.WALK_AWAY, reason="Remote supervisor aborted negotiation")
            else:
                session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING, reason=f"Remote unlocked via command {cmd.value}")

    action_name = "concede_step" if cmd == RemoteAuthCommand.CONCEDE else cmd.value.lower()
    return {
        "status": "success",
        "session_id": session_id,
        "resolved_command": cmd.value,
        "source_round_id": source_round_id,
        "custom_price": custom_price,
        "action": action_name,
        "reply_script_zh": reply_script_zh,
        "confirmation_card_en": confirmation_card_en,
        "action_object": action_obj_dump,
    }


@app.post("/webhook/mobile-command")
async def receive_mobile_command(request: Request):
    """接收手機端（WhatsApp Twilio / Telegram / 微信 Webhook）指令回調。"""
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
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body format.")

    if not user_command:
        raise HTTPException(status_code=400, detail="Missing command in payload.")

    try:
        return _dispatch_supervisor_command(session_id, user_command)
    except KeyError as e:
        return {"status": "ignored", "reason": str(e)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SessionCommandBody(BaseModel):
    command: Optional[str] = None
    command_text: Optional[str] = None
    source: str = "mobile"


@app.post("/api/v1/sessions/{session_id}/command", dependencies=[Depends(require_api_key)])
def execute_session_command(session_id: str, body: SessionCommandBody):
    """執行特定會話的高管授權或決策指令。"""
    user_command = (body.command or body.command_text or "").strip()
    if not user_command:
        raise HTTPException(status_code=400, detail="Missing command.")
    try:
        return _dispatch_supervisor_command(session_id, user_command)
    except KeyError as e:
        return {"status": "ignored", "reason": str(e)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- v4 會話管理 REST APIs ----

class RoundActionRequest(BaseModel):
    # Reject unknown fields. Without this, a misspelled key (e.g. sending
    # `supplier_message` instead of `supplier_text`) was silently dropped by
    # Pydantic, the round ran with empty inbound text, and the API returned
    # HTTP 200 with a plausible-but-wrong NORMAL_COUNTER_OFFER decision instead
    # of an error. Silent fallback to a default decision is the dangerous case:
    # the caller cannot tell a real analysis from a no-input analysis.
    model_config = ConfigDict(extra="forbid")

    opponent_raw_text: Optional[str] = None
    supplier_text: Optional[str] = None
    opponent_offer_price: Optional[float] = None
    supplier_offer: Optional[float] = None
    round_index: Optional[int] = None
    offered_lead_days: Optional[int] = None
    offered_moq: Optional[int] = None
    deposit_pct: Optional[float] = None
    unbundled_tax_rate: Optional[float] = None
    unbundled_packaging_fee: Optional[float] = None
    expected_version: Optional[int] = None
    mode: Optional[str] = "autopilot"  # "autopilot" | "copilot"
    # Stage-2 LLM 語意推理為同步網路呼叫（實測 2–13 秒）。預設關閉以維持回應速度；
    # 需要更深語意理解時才開啟（例如離線批次分析、或使用者明確願意等待）。
    deep_semantic: bool = False

    def get_effective_raw_text(self) -> str:
        return self.opponent_raw_text or self.supplier_text or ""

    def get_effective_offer_price(self) -> Optional[float]:
        return self.opponent_offer_price if self.opponent_offer_price is not None else self.supplier_offer


class ApproveActionRequest(BaseModel):
    # See RoundActionRequest: a misspelled field must fail loudly, not silently
    # approve with defaults.
    model_config = ConfigDict(extra="forbid")

    action: str = "approve"  # "approve", "modify", "override", "reject"
    custom_price: Optional[float] = None
    custom_reply_text: Optional[str] = None
    expected_version: Optional[int] = None
    tactical_rationale: Optional[str] = None


# ---- v5 條款 TCO 與貝氏推斷 Request Models ----

class TCOEvalRequest(BaseModel):
    raw_price: float
    baseline_lead_days: int = 15
    offered_lead_days: Optional[int] = None
    baseline_moq: int = 500
    offered_moq: Optional[int] = None
    wacc: float = 0.08

class BayesianUpdateRequest(BaseModel):
    prior_mean: float = 1.08
    prior_std: float = 0.10
    observed_offer: float
    estimated_markup: float = 0.08
    our_next_offer: Optional[float] = None


class SemanticClassifyRequest(BaseModel):
    """Stage-2 語意分類預覽請求。"""
    text: str
    enable_stage2: bool = True  # 是否實際呼叫 AMD 託管模型（2–13 秒）


class MultiAuctionSupplierInput(BaseModel):
    name: str
    profile: Optional[str] = "Standard"


class MultiAuctionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_price: float = 12.0
    walk_away_ceiling: float = 16.2
    moq: int = 10000
    n_samples: int = 20000
    n_rounds: int = 3
    suppliers: Optional[List[MultiAuctionSupplierInput]] = None
    rounds_quotes: Optional[List[List[float]]] = None


@app.post("/api/v1/tco/evaluate")
def evaluate_nonlinear_tco(req: TCOEvalRequest):
    """計算非價格條款（交期延誤、超額起訂量）之非線性影子成本。"""
    res = TCOEngine.calculate_nonlinear_effective_tco(
        raw_price=req.raw_price,
        baseline_lead_days=req.baseline_lead_days,
        offered_lead_days=req.offered_lead_days,
        baseline_moq=req.baseline_moq,
        offered_moq=req.offered_moq,
        wacc=req.wacc,
    )
    return {"status": "ok", "tco_analysis": res}


@app.post("/api/v1/bayesian/update")
def calculate_bayesian_update(req: BayesianUpdateRequest):
    """執行高斯-均勻雙模態混合似然推斷與離群點拒絕。"""
    res = robust_bayes_update(
        prior_mean=req.prior_mean,
        prior_std=req.prior_std,
        observed_offer=req.observed_offer,
        estimated_markup=req.estimated_markup,
        our_next_offer=req.our_next_offer,
    )
    return {"status": "ok", "belief_state": res.model_dump()}


@app.post("/api/v1/sessions", response_model=NegotiationSession, dependencies=[Depends(require_api_key)])
def create_session(spec: SessionSpec):
    """建立新的談判會話。"""
    try:
        return session_mgr.create_session(spec)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/sessions", response_model=List[NegotiationSession])
def list_sessions():
    """檢索所有現有談判會話。"""
    return session_mgr.list_sessions()


@app.get("/api/v1/sessions/{session_id}", response_model=NegotiationSession)
def get_session(session_id: str):
    """以 ID 查詢特定會話。"""
    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    return sess


@app.post("/api/v1/classify/semantic", dependencies=[Depends(require_api_key)])
async def classify_semantic_preview(body: SemanticClassifyRequest):
    """Stage-2 語意分類的獨立端點（非同步、不阻塞事件迴圈）。

    設計動機：Stage-2 為 2–13 秒的網路呼叫。將其從主談判輪次中拆出後：
    1. 主輪次端點維持毫秒級回應（僅 Stage-1）。
    2. 需要深度語意理解時，前端可併行呼叫本端點，完成後再更新 UI。

    回傳同時包含 Stage-1（即時）與 Stage-2（深度）兩者結果，方便對照差異。
    """
    from ..engine.bilingual import InboundIntentClassifier as _Clf

    text = body.text or ""
    stage1 = _Clf.classify_stage1_only(text)

    stage2 = None
    if body.enable_stage2:
        deep = await _Clf.classify_with_llm_async(text)
        if deep is not None:
            stage2 = {
                "primary_intent": deep.primary_intent.value,
                "confidence": deep.confidence,
                "tactical_rationale": deep.tactical_rationale,
                "requires_escalation": deep.requires_escalation,
            }

    return {
        "text": text,
        "stage1": {
            "primary_intent": stage1.primary_intent.value,
            "confidence": stage1.confidence,
            "mentioned_price": stage1.mentioned_price,
            "demanded_moq": stage1.demanded_moq,
            "requires_escalation": stage1.requires_escalation,
        },
        "stage2": stage2,
        "stage2_available": stage2 is not None,
    }


@app.post("/api/v1/sessions/{session_id}/approve", dependencies=[Depends(require_api_key)])
def approve_session_decision(session_id: str, body: ApproveActionRequest):
    """採購決策副駕駛 (Copilot) 人工審核與定奪授權端點。

    支援功能：
    1. approve: 人工核准系統推薦報價與話術。
    2. modify / override: 人工覆寫報價或自訂回覆話術 (需通過 ceiling_price 硬護欄)。
    3. reject: 人工否決並破局撤退 (WALK_AWAY)。
    """
    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")

    # 狀態守衛：必須處於等待人類決策、監聽中或熔斷狀態
    if sess.state not in (
        AutopilotState.AWAITING_HUMAN_DECISION,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.COPILOT_MONITORING,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Session is in state '{sess.state.value}', not awaiting human decision.",
        )

    # CAS 樂觀鎖版本檢查
    if body.expected_version is not None and sess.version != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"CAS optimistic lock conflict on session '{session_id}': expected_version={body.expected_version}, current_version={sess.version}",
        )

    # 1. 否決撤退
    if body.action == "reject":
        sess = session_mgr.transition_state(
            session_id,
            AutopilotState.WALK_AWAY,
            reason=body.tactical_rationale or "Human buyer rejected offer and terminated negotiation.",
            expected_version=sess.version,
        )
        return {
            "status": "rejected",
            "session": sess,
            "action": "reject",
            "autopilot_state": sess.state.value,
        }

    # 2. 核准或覆寫
    final_price = body.custom_price
    if final_price is None:
        # 若未指定 custom_price，從上一輪推薦或目標價推算
        final_price = sess.latest_our_offer or sess.spec.target_price

    # 護欄校驗：不得突破天花板價
    if final_price > sess.spec.ceiling_price:
        raise HTTPException(
            status_code=422,
            detail=f"Approved price ¥{final_price:.2f} breaches walk-away ceiling ¥{sess.spec.ceiling_price:.2f}.",
        )

    final_reply_text = body.custom_reply_text
    if not final_reply_text:
        final_reply_text = f"經我方核算，確定意向合作單價為 ¥{final_price:.2f}，現款現結走官方系統，請確認排單。"

    turn_our = NegotiationTurn(
        round_index=sess.current_round,
        sender="buyer_human_approved",
        raw_text=final_reply_text,
        parsed_price=final_price,
        p_break=0.0,
        metadata={
            "approved_by": "human_buyer",
            "action": body.action,
            "rationale": body.tactical_rationale or "Human buyer authorized decision",
        },
    )

    try:
        sess = session_mgr.append_turn(session_id, turn_our, expected_version=sess.version)
        sess = session_mgr.transition_state(
            session_id,
            AutopilotState.HUMAN_APPROVED_APPLIED,
            reason=f"Human buyer approved action '{body.action}' at price ¥{final_price:.2f}",
            expected_version=sess.version,
        )
    except OptimisticLockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 檢查是否達成協議
    is_deal_closed = False
    if sess.latest_opponent_offer is not None and sess.latest_opponent_offer <= final_price:
        is_deal_closed = True

    if is_deal_closed:
        sess = session_mgr.transition_state(session_id, AutopilotState.DEAL_CLOSED, expected_version=sess.version)
    else:
        # 轉入常規 Copilot 監聽狀態
        sess = session_mgr.transition_state(session_id, AutopilotState.COPILOT_MONITORING, expected_version=sess.version)

    return {
        "status": "approved",
        "action": body.action,
        "session": sess,
        "autopilot_state": sess.state.value,
        "applied_price": final_price,
        "reply_text": final_reply_text,
        "turn": turn_our,
    }


@app.post("/api/v1/sessions/{session_id}/round", dependencies=[Depends(require_api_key)])
def process_negotiation_round(session_id: str, action: RoundActionRequest):
    """
    處理單輪網格回報：
    1. 意圖識別與潛台詞抽取
    2. 多目標非價格條款 TCO (Total Cost of Ownership) 歸一化折算
    3. 貝氏信念更新並以 MCTS 引擎重解
    4. 安全熔斷檢查 (P_break >= 15% 觸發斷路器並派發手機推播)
    5. 回傳下一輪策略建議與自駕狀態
    """
    sess = session_mgr.get_session(session_id)
    if not sess:
        # 自動預置新會話 (Auto-Provisioning): 防止擴充套件因全新環境找不到會話噴 404
        default_spec = SessionSpec(
            session_id=session_id,
            product_name="Custom Carbon Auto Umbrella",
            target_price=12.0,
            ceiling_price=16.2,
            moq=5000,
            max_rounds=5,
            supplier_id="Web_Inbound_Supplier",
            supplier_channel="web_chat",
        )
        sess = session_mgr.create_session(default_spec)
        initial_state = AutopilotState.COPILOT_MONITORING if action.mode == "copilot" else AutopilotState.AUTOPILOT_CRUISING
        sess = session_mgr.transition_state(session_id, initial_state)
    elif action.expected_version is not None and sess.version != action.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"CAS optimistic lock conflict on session '{session_id}': expected_version={action.expected_version}, current_version={sess.version}",
        )

    if sess.state in (AutopilotState.DEAL_CLOSED, AutopilotState.WALK_AWAY):
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": False,
            "pending_human_approval": False,
            "trip_reason": f"Session already concluded with state {sess.state.value}",
            "reply_text": f"會話已於先前輪次結案或終止 ({sess.state.value})。",
        }

    current_round = action.round_index or (sess.current_round + 1)
    target_price = sess.spec.target_price
    ceiling_price = sess.spec.ceiling_price
    opp_price = action.get_effective_offer_price()
    raw_text = action.get_effective_raw_text()

    # 1. 供應商套路意圖分類與潛台詞反編譯
    from ..engine.bilingual import (
        InboundIntentClassifier,
        BilingualTacticalBridge,
        NumericGuardrailValidator,
        GuardrailViolationError,
        TacticalActionObject,
        TacticalModeEnum,
        ActionObjectASTGuardrail,
        ChineseSurfaceRenderer,
        EnglishExecutiveRenderer,
        CURRENT_SCHEMA_VERSION,
    )
    # 1. 供應商套路意圖分類與潛台詞反編譯
    #    效能說明：Stage-1（正則 + 端側 ONNX）實測 ~1ms。
    #    Stage-2（AMD tokenfactory LLM）實測 2–13 秒，故預設不啟用，避免阻塞同步 HTTP 請求。
    #    呼叫端若需要更深語意理解，可設 deep_semantic=true。
    inbound_classification = (
        InboundIntentClassifier.classify(raw_text)
        if action.deep_semantic
        else InboundIntentClassifier.classify_stage1_only(raw_text)
    )
    if opp_price is None and inbound_classification.mentioned_price is not None:
        opp_price = inbound_classification.mentioned_price

    # 1.5 多目標非價格條款 TCO 影子成本折算 (Total Cost of Ownership Normalization)
    from ..engine.tco import TradeTermsSpec
    terms_spec = TradeTermsSpec(baseline_moq=sess.spec.moq)
    offered_moq = action.offered_moq or inbound_classification.demanded_moq
    tco_breakdown = None
    effective_opp_price = opp_price

    if opp_price is not None:
        tco_res = TCOEngine.calculate_nonlinear_effective_tco(
            raw_price=opp_price,
            baseline_lead_days=terms_spec.baseline_lead_days,
            offered_lead_days=action.offered_lead_days,
            baseline_moq=sess.spec.moq,
            offered_moq=offered_moq,
            wacc=terms_spec.annual_cost_of_capital,
        )
        effective_opp_price = tco_res["effective_unit_tco"]
        tco_breakdown = tco_res

    # 2. 計算歸一化報價與更新底價後驗 (以 TCO 等效總成本驅動博弈)
    prev_mean = sess.spec.prior_reserve_mean
    prev_std = sess.spec.prior_reserve_std
    if sess.history:
        for t in reversed(sess.history):
            if t.posterior_mean is not None:
                prev_mean = t.posterior_mean
                prev_std = t.posterior_std or prev_std
                break

    post_mean, post_std = prev_mean, prev_std
    if effective_opp_price is not None:
        norm_offer = effective_opp_price / target_price
        post_mean, post_std = bayes_update(prev_mean, prev_std, norm_offer)

    # 3. 跑引擎重解
    solve_res = solve(
        n_opp_samples=sess.spec.max_rounds * 4000,
        use_gpu=False,
        reserve_mean=post_mean,
        reserve_std=post_std,
    )
    rec = recommend(solve_res, target_price=target_price)
    prop = proposal.build_proposal(
        {"target_price": target_price, "prior_reserve_mean": post_mean, "prior_reserve_std": post_std},
        observed_offer=(effective_opp_price / target_price) if effective_opp_price else None,
    )

    p_break = rec["breakdown_prob"]
    raw_recommended_price = rec["opening_offer_price"]
    recommended_offer_price = min(raw_recommended_price, ceiling_price)
    floor_price_est = round(post_mean * target_price, 2)
    bayesian_confidence = compute_belief_confidence(prev_std, post_std, post_mean)

    # 3. 記錄對手發言輪次
    turn_opp = NegotiationTurn(
        round_index=current_round,
        sender="opponent",
        raw_text=action.get_effective_raw_text(),
        parsed_price=opp_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
    )
    try:
        sess = session_mgr.append_turn(session_id, turn_opp, expected_version=action.expected_version)
    except OptimisticLockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 4. 安全熔斷矩陣評估
    trip_reason = None
    if inbound_classification.requires_escalation:
        trip_reason = f"Security escalation: {inbound_classification.primary_intent.value} ({inbound_classification.tactical_rationale})"
    elif p_break >= 0.15:
        trip_reason = f"Breakdown risk {p_break:.1%} exceeds 15% safety limit."
    elif opp_price and opp_price > ceiling_price:
        trip_reason = f"Opponent offer ¥{opp_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."
    elif raw_recommended_price > ceiling_price:
        trip_reason = f"Engine recommended price ¥{raw_recommended_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."

    if trip_reason:
        # 觸發熔斷！構建結構化策略對象並派發高階英文決策卡
        trip_action_obj = TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id=f"{session_id}_R{current_round}_{sess.spec.supplier_id}",
            authorized_price=recommended_offer_price,
            walk_away_ceiling=ceiling_price,
            breakdown_risk=p_break,
            expected_margin=rec.get("expected_margin_price", 0.0),
            concession_step=rec.get("concession_step", 0.0),
            posterior_reserve_mean=post_mean,
            posterior_reserve_std=post_std,
            bayesian_confidence=bayesian_confidence,
            estimated_floor_price=floor_price_est,
            tactical_mode=TacticalModeEnum.FIRM_HOLD if p_break < 0.15 else TacticalModeEnum.WALK_AWAY,
            product_name=sess.spec.product_name,
            lead_days=action.offered_lead_days or 25,
            deposit_pct=action.deposit_pct or 0.30,
            moq=offered_moq or sess.spec.moq,
            counterpart_intent=inbound_classification.primary_intent,
            counterpart_offered_price=opp_price,
            circuit_tripped=True,
            trip_reason=trip_reason,
        )
        if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
            sess = session_mgr.transition_state(
                session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=trip_reason
            )
        notifier.notify_circuit_breaker(
            session_id=session_id,
            product=sess.spec.product_name,
            opponent_offer=opp_price or target_price,
            p_break=p_break,
            advice_text=prop["advice_text"],
            proposed_price=recommended_offer_price,
            action_object=trip_action_obj,
        )
        v2_belief = robust_bayes_update(
            prior_mean=sess.spec.prior_reserve_mean,
            prior_std=sess.spec.prior_reserve_std,
            observed_offer=opp_price / target_price if opp_price else post_mean,
            our_next_offer=recommended_offer_price / target_price if recommended_offer_price else None,
        )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": True,
            "pending_human_approval": True,
            "trip_reason": trip_reason,
            "recommendation": rec,
            "proposal": prop,
            "action_object": trip_action_obj.model_dump(),
            "executive_card_en": EnglishExecutiveRenderer.render_executive_card(trip_action_obj),
            "reply_text": None,  # 阻斷回覆，等待主管手機授權
            "tco_breakdown": tco_breakdown,
            "effective_opp_price": effective_opp_price,
            "bayesian_belief_state": v2_belief.model_dump(),
        }

    # 5. 多維契約條款置換 (Logrolling Pareto Optimization)
    logrolling_data = None
    from ..engine.logrolling import (
        LogrollingEngine,
        CorporateMandate,
        SupplierCurrentOffer,
        TradeoffType,
    )
    # 若有設定或自動生成預設 CorporateMandate，且價格處於僵局或高於目標價 8%
    mandate_obj = None
    if sess.spec.corporate_mandate:
        try:
            mandate_obj = CorporateMandate(**sess.spec.corporate_mandate)
        except Exception:
            pass
    if not mandate_obj:
        b_moq = sess.spec.moq
        mandate_obj = CorporateMandate(
            target_price=target_price,
            walk_away_ceiling=ceiling_price,
            baseline_lead_days=action.offered_lead_days or 25,
            baseline_moq=b_moq,
            max_allowed_moq=max(1200, int(b_moq * 2)),
        )

    # 判定是否觸發多維條款置換 (當對手不降價或哭窮抬價時)
    should_logroll = (
        opp_price is not None
        and opp_price > target_price * 1.05
        and current_round >= 2
    )

    if should_logroll:
        supp_offer_obj = SupplierCurrentOffer(
            price=opp_price,
            lead_days=action.offered_lead_days or 25,
            deposit_pct=action.deposit_pct or 0.30,
            moq=offered_moq or sess.spec.moq,
            mold_fee=3000.0,
            is_price_stagnant=True,
        )
        try:
            lr_res = LogrollingEngine.evaluate(mandate_obj, supp_offer_obj)
            best_prop = lr_res.best_recommendation
            logrolling_data = best_prop.model_dump()
            auto_reply_text = best_prop.buyer_script_zh
        except Exception:
            auto_reply_text = None
    else:
        auto_reply_text = None

    # 5.5 構建語言中立策略決策對象 (TacticalActionObject) 作為單一真相源
    if logrolling_data:
        t_mode = TacticalModeEnum.LOGROLL
        auth_price = min(logrolling_data["proposed_price"], ceiling_price)
        lead_days_val = logrolling_data["proposed_lead_days"]
        deposit_pct_val = logrolling_data["proposed_deposit_pct"]
        moq_val = logrolling_data["proposed_moq"]
        mold_exempt = logrolling_data.get("mold_fee_exempted", False)
        waive_mold_amount = 3000.0 if mold_exempt else 0.0
    else:
        t_mode = (
            TacticalModeEnum.LEAN_IN
            if p_break < 0.08
            else (TacticalModeEnum.CROSS_ANCHOR if p_break < 0.15 else TacticalModeEnum.SIDELINE)
        )
        auth_price = recommended_offer_price
        lead_days_val = action.offered_lead_days or 25
        deposit_pct_val = action.deposit_pct or 0.30
        moq_val = offered_moq or sess.spec.moq
        mold_exempt = False
        waive_mold_amount = 0.0

    action_obj = TacticalActionObject(
        schema_version=CURRENT_SCHEMA_VERSION,
        source_round_id=f"{session_id}_R{current_round}_{sess.spec.supplier_id}",
        authorized_price=auth_price,
        walk_away_ceiling=ceiling_price,
        breakdown_risk=p_break,
        expected_margin=rec.get("expected_margin_price", 0.0),
        concession_step=rec.get("concession_step", 0.0),
        posterior_reserve_mean=post_mean,
        posterior_reserve_std=post_std,
        bayesian_confidence=bayesian_confidence,
        estimated_floor_price=floor_price_est,
        tactical_mode=t_mode,
        product_name=sess.spec.product_name,
        supplier_contact_name="老闆",
        lead_days=lead_days_val,
        deposit_pct=deposit_pct_val,
        moq=moq_val,
        mold_fee_exempted=mold_exempt,
        waive_mold_fee_amount=waive_mold_amount,
        counterpart_intent=inbound_classification.primary_intent,
        counterpart_offered_price=opp_price,
        competing_lead_price=target_price * 1.05,
        circuit_tripped=False,
    )

    # 5.6 AST 不變量硬護欄驗證單一真相源
    ActionObjectASTGuardrail.verify(action_obj)

    # 5.7 雙向表面渲染
    # 中文表面渲染 (面向供應商對話)：沿海地道行話，徹底消除外商溢價
    auto_reply_text = ChineseSurfaceRenderer.render(action_obj)

    # 文本層數值護欄複核 (防止任何渲染偏差)
    try:
        NumericGuardrailValidator.validate_reply(
            auto_reply_text,
            authorized_price=auth_price,
            walk_away_ceiling=ceiling_price,
        )
    except GuardrailViolationError:
        auto_reply_text = f"經核算，我方首批訂單確定意向價為 ¥{auth_price:.2f}，現款現結走官方系統，請確認排單。"

    # 英文高管決策卡與遙測摘要 (面向西方採購主管 / CPO 儀表板)
    executive_card_en = EnglishExecutiveRenderer.render_executive_card(action_obj)
    telemetry_eng = EnglishExecutiveRenderer.render_telemetry_summary(action_obj)

    # 5.8 v5 Should-Cost 與校準貝氏混合推斷增強
    v2_belief = robust_bayes_update(
        prior_mean=sess.spec.prior_reserve_mean,
        prior_std=sess.spec.prior_reserve_std,
        observed_offer=opp_price / target_price if opp_price else post_mean,
        our_next_offer=auth_price / target_price if auth_price else None,
    )
    # 6. 分流：採購決策副駕駛 (Copilot) vs 全自主巡航 (Autopilot)
    if action.mode == "copilot":
        if sess.state != AutopilotState.AWAITING_HUMAN_DECISION:
            sess = session_mgr.transition_state(
                session_id,
                AutopilotState.AWAITING_HUMAN_DECISION,
                reason="Copilot decision radar prepared; awaiting human buyer review and authorization.",
            )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": False,
            "pending_human_approval": True,
            "turn": None,
            "inbound_intent": inbound_classification.primary_intent.value,
            "tco_breakdown": tco_breakdown,
            "logrolling_proposal": logrolling_data,
            "effective_opp_price": effective_opp_price,
            "telemetry_summary_en": telemetry_eng,
            "executive_card_en": executive_card_en,
            "action_object": action_obj.model_dump(),
            "recommendation": rec,
            "proposal": prop,
            "recommended_offer_price": auth_price,
            "reply_text": auto_reply_text,
            "bayesian_belief_state": v2_belief.model_dump(),
        }

    # ---- Autopilot 巡航模式分支 (向後相容全自動自駕) ----
    if sess.state == AutopilotState.IDLE:
        sess = session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING)

    is_deal_closed = False
    if opp_price is not None:
        if opp_price <= target_price * 1.02:
            is_deal_closed = True
        elif current_round >= sess.spec.max_rounds and opp_price <= ceiling_price and abs(opp_price - recommended_offer_price) <= (0.02 * target_price):
            is_deal_closed = True

    if is_deal_closed and sess.state != AutopilotState.DEAL_CLOSED:
        sess = session_mgr.transition_state(session_id, AutopilotState.DEAL_CLOSED)
        notifier.notify_deal_closed(
            session_id=session_id,
            product=sess.spec.product_name,
            final_price=opp_price,
            savings_pct=(ceiling_price - opp_price) / ceiling_price,
            rounds=current_round,
        )
        try:
            if os.getenv("EVOLUS_API_KEY") and os.getenv("EVOLUS_APP_ID"):
                from ..evolus.client import EvolusClient
                evolus_c = EvolusClient()
                deal_summary = (
                    f"Procurement Deal Closed: {sess.spec.product_name} at ¥{opp_price:.2f} "
                    f"(Target: ¥{target_price:.2f}, MOQ: {sess.spec.moq}). "
                    f"Completed in {current_round} rounds with total savings of "
                    f"{((ceiling_price - opp_price) / ceiling_price):.1%}."
                )
                evolus_c.run_email_tool(notes=deal_summary, tone="professional")
        except Exception:
            pass

    turn_our = NegotiationTurn(
        round_index=current_round,
        sender="buyer_agent",
        raw_text=auto_reply_text,
        parsed_price=auth_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
        logrolling_proposal=logrolling_data,
    )
    sess = session_mgr.append_turn(session_id, turn_our)

    return {
        "session": sess,
        "autopilot_state": sess.state.value,
        "circuit_tripped": False,
        "pending_human_approval": False,
        "turn": turn_our,
        "inbound_intent": inbound_classification.primary_intent.value,
        "tco_breakdown": tco_breakdown,
        "logrolling_proposal": logrolling_data,
        "effective_opp_price": effective_opp_price,
        "telemetry_summary_en": telemetry_eng,
        "executive_card_en": executive_card_en,
        "action_object": action_obj.model_dump(),
        "recommendation": rec,
        "proposal": prop,
        "recommended_offer_price": auth_price,
        "reply_text": auto_reply_text,
        "bayesian_belief_state": v2_belief.model_dump(),
    }


# ---- Web Portal & 多供應商逆向拍賣模擬端點 ----
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from ..web.routes_web import router as web_router
from ..engine.multi_solver import simulate_reverse_auction

static_path = Path(__file__).parent.parent / "web" / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

app.include_router(web_router)


@app.post("/api/v1/multi-auction/run")
def api_run_multi_auction(body: Optional[MultiAuctionRunRequest] = None):
    """觸發多供應商逆向拍賣。

    不傳報價時，改為執行動態多 Agent 自博弈競價（各家對抗 Agent 依據數學模型
    逐輪即時算出報價）；亦可傳入自定義供應商名單與逐輪報價矩陣。
    """
    try:
        req = body or MultiAuctionRunRequest()
        supp_list = [s.model_dump() for s in req.suppliers] if req.suppliers else None
        res = simulate_reverse_auction(
            target_price=req.target_price,
            walk_away_ceiling=req.walk_away_ceiling,
            moq=req.moq,
            n_samples=req.n_samples,
            suppliers=supp_list,
            rounds_quotes=req.rounds_quotes,
            n_rounds=req.n_rounds,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

