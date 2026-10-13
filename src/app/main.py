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
    opponent_raw_text: str
    opponent_offer_price: Optional[float] = None
    round_index: Optional[int] = None


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
    1. 記錄對手回價與發言
    2. 貝氏信念更新並以引擎重解
    3. 安全熔斷檢查 (P_break >= 15% 觸發斷路器並派發手機推播)
    4. 回傳下一輪策略建議與自駕狀態
    """
    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")

    current_round = action.round_index or (sess.current_round + 1)
    target_price = sess.spec.target_price
    ceiling_price = sess.spec.ceiling_price
    opp_price = action.opponent_offer_price

    # 1. 計算歸一化報價與更新底價後驗
    prev_mean = sess.spec.prior_reserve_mean
    prev_std = sess.spec.prior_reserve_std
    if sess.history:
        for t in reversed(sess.history):
            if t.posterior_mean is not None:
                prev_mean = t.posterior_mean
                prev_std = t.posterior_std or prev_std
                break

    post_mean, post_std = prev_mean, prev_std
    if opp_price is not None:
        norm_offer = opp_price / target_price
        post_mean, post_std = bayes_update(prev_mean, prev_std, norm_offer)

    # 2. 跑引擎重解
    solve_res = solve(
        n_opp_samples=sess.spec.max_rounds * 4000,
        use_gpu=False,
        reserve_mean=post_mean,
        reserve_std=post_std,
    )
    rec = recommend(solve_res, target_price=target_price)
    prop = proposal.build_proposal(
        {"target_price": target_price, "prior_reserve_mean": post_mean, "prior_reserve_std": post_std},
        observed_offer=(opp_price / target_price) if opp_price else None,
    )

    p_break = rec["breakdown_prob"]
    recommended_offer_price = rec["opening_offer_price"]

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
    if p_break >= 0.15:
        trip_reason = f"Breakdown risk {p_break:.1%} exceeds 15% safety limit."
    elif opp_price and opp_price > ceiling_price:
        trip_reason = f"Opponent offer ¥{opp_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."

    if trip_reason:
        # 觸發熔斷！切換狀態並派發手機推播
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
        )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": True,
            "trip_reason": trip_reason,
            "recommendation": rec,
            "proposal": prop,
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

    # 記錄我方回覆輪次
    auto_reply_text = f"經我方核算，首批訂單意向價為 ¥{recommended_offer_price:.2f}，現款現結，請確認排單。"
    turn_our = NegotiationTurn(
        round_index=current_round,
        sender="buyer_agent",
        raw_text=auto_reply_text,
        parsed_price=recommended_offer_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
    )
    sess = session_mgr.append_turn(session_id, turn_our)

    return {
        "session": sess,
        "autopilot_state": sess.state.value,
        "circuit_tripped": False,
        "turn": turn_our,
        "recommendation": rec,
        "proposal": prop,
        "recommended_offer_price": recommended_offer_price,
        "reply_text": auto_reply_text,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
