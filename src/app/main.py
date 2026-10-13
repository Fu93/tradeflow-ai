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
from pydantic import BaseModel

from ..engine import solve, recommend, bayes_update, robustness, model, proposal
from ..session.schema import (
    SessionSpec,
    NegotiationSession,
    NegotiationTurn,
    AutopilotState,
    RemoteAuthCommand,
)
from ..session.manager import SessionManager, InvalidStateTransitionError
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
        # Twilio 允許自訂 URL 參數或透過 session mapping
        session_id = str(form.get("session_id", "default_session"))
    else:
        # 2. 嘗試解析 JSON 資料 (Telegram / 自訂指令)
        try:
            body = await request.json()
            user_command = str(body.get("command", "")).strip()
            session_id = str(body.get("session_id", "default_session"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body format.")

    if not user_command:
        raise HTTPException(status_code=400, detail="Missing command in payload.")

    try:
        cmd, custom_price = hub.dispatch_command(session_id, user_command)
        sess = session_mgr.get_session(session_id)
        if sess and sess.state == AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
            if cmd == RemoteAuthCommand.WALK_AWAY:
                session_mgr.transition_state(session_id, AutopilotState.WALK_AWAY, reason="Remote supervisor aborted negotiation")
            else:
                session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING, reason=f"Remote unlocked via command {cmd.value}")
        action_name = "concede_step" if cmd == RemoteAuthCommand.CONCEDE else cmd.value.lower()
        return {
            "status": "success",
            "session_id": session_id,
            "resolved_command": cmd.value,
            "custom_price": custom_price,
            "action": action_name,
        }
    except KeyError as e:
        return {"status": "ignored", "reason": str(e)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- v4 會話管理 REST APIs ----

class RoundActionRequest(BaseModel):
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

    def get_effective_raw_text(self) -> str:
        return self.opponent_raw_text or self.supplier_text or ""

    def get_effective_offer_price(self) -> Optional[float]:
        return self.opponent_offer_price if self.opponent_offer_price is not None else self.supplier_offer


@app.post("/api/v1/sessions", response_model=NegotiationSession)
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


@app.post("/api/v1/sessions/{session_id}/round")
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
        sess = session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING)

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
    )
    inbound_classification = InboundIntentClassifier.classify(raw_text)
    if opp_price is None and inbound_classification.mentioned_price is not None:
        opp_price = inbound_classification.mentioned_price

    # 1.5 多目標非價格條款 TCO 影子成本折算 (Total Cost of Ownership Normalization)
    from ..engine.tco import TCOEngine, TradeTermsSpec, SupplierOfferedTerms
    terms_spec = TradeTermsSpec(baseline_moq=sess.spec.moq)
    offered_moq = action.offered_moq or inbound_classification.demanded_moq
    tco_breakdown = None
    effective_opp_price = opp_price

    if opp_price is not None:
        offered_terms = SupplierOfferedTerms(
            raw_unit_price=opp_price,
            offered_lead_days=action.offered_lead_days,
            offered_moq=offered_moq,
            deposit_pct=action.deposit_pct,
            unbundled_tax_rate=action.unbundled_tax_rate,
            unbundled_packaging_fee=action.unbundled_packaging_fee,
        )
        tco_breakdown = TCOEngine.calculate_effective_price(terms_spec, offered_terms)
        effective_opp_price = tco_breakdown.effective_unit_price

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
    recommended_offer_price = rec["opening_offer_price"]
    floor_price_est = round(post_mean * target_price, 2)
    bayesian_confidence = round(max(0.50, min(0.98, 1.0 - post_std)), 3)

    # 3. 記錄對手發言輪次
    turn_opp = NegotiationTurn(
        round_index=current_round,
        sender="opponent",
        raw_text=action.opponent_raw_text,
        parsed_price=opp_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
    )
    sess = session_mgr.append_turn(session_id, turn_opp)

    # 4. 安全熔斷矩陣評估
    trip_reason = None
    if inbound_classification.requires_escalation:
        trip_reason = f"Security escalation: {inbound_classification.primary_intent.value} ({inbound_classification.tactical_rationale})"
    elif p_break >= 0.15:
        trip_reason = f"Breakdown risk {p_break:.1%} exceeds 15% safety limit."
    elif opp_price and opp_price > ceiling_price:
        trip_reason = f"Opponent offer ¥{opp_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."

    if trip_reason:
        # 觸發熔斷！構建結構化策略對象並派發高階英文決策卡
        trip_action_obj = TacticalActionObject(
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
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": True,
            "trip_reason": trip_reason,
            "recommendation": rec,
            "proposal": prop,
            "action_object": trip_action_obj.model_dump(),
            "executive_card_en": EnglishExecutiveRenderer.render_executive_card(trip_action_obj),
            "reply_text": None,  # 阻斷回覆，等待主管手機授權
        }

    # 5. 安全自駕中 (AUTOPILOT_CRUISING)
    if sess.state == AutopilotState.IDLE:
        sess = session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING)

    # 判斷是否已達成協議 (價差在 2% 以內)
    if opp_price and abs(opp_price - recommended_offer_price) <= (0.02 * target_price):
        sess = session_mgr.transition_state(session_id, AutopilotState.DEAL_CLOSED)
        notifier.notify_deal_closed(
            session_id=session_id,
            product=sess.spec.product_name,
            final_price=opp_price,
            savings_pct=(ceiling_price - opp_price) / ceiling_price,
            rounds=current_round,
        )
        # 觸發 Evolus 智慧結案合約歸檔 (Evolus Business Agent Audit Trail)
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
            pass  # 容錯降級，不阻塞主結案流程

    # 6. 多維契約條款置換 (Logrolling Pareto Optimization)
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

    # 6.5 構建語言中立策略決策對象 (TacticalActionObject) 作為單一真相源
    if logrolling_data:
        t_mode = TacticalModeEnum.LOGROLL
        auth_price = logrolling_data["proposed_price"]
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

    # 6.6 AST 不變量硬護欄驗證單一真相源
    ActionObjectASTGuardrail.verify(action_obj)

    # 6.7 雙向表面渲染
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
        "turn": turn_our,
        "inbound_intent": inbound_classification.primary_intent.value,
        "tco_breakdown": tco_breakdown.model_dump() if tco_breakdown else None,
        "logrolling_proposal": logrolling_data,
        "effective_opp_price": effective_opp_price,
        "telemetry_summary_en": telemetry_eng,
        "executive_card_en": executive_card_en,
        "action_object": action_obj.model_dump(),
        "recommendation": rec,
        "proposal": prop,
        "recommended_offer_price": auth_price,
        "reply_text": auto_reply_text,
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
def api_run_multi_auction():
    """觸發 4 供應商逆向拍賣 (96M Rollouts) 模擬，供 Web 前端大屏展示。"""
    try:
        res = simulate_reverse_auction()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

