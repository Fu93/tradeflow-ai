"""v4/v5 session lifecycle: create, list, get, and the human approval gate.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. /approve is the
copilot contract: a person authorises the price, the desk never sends.
"""
from __future__ import annotations
from typing import Any, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config.heuristics import API_PRICE_CAP
from ..session.manager import InvalidStateTransitionError, OptimisticLockConflictError
from ..session.schema import (
    AutopilotState,
    NegotiationSession,
    NegotiationTurn,
    SessionSpec,
)
from .state import (
    _latest_recommended_price,
    _legacy_route,
    _reserve_not_identified,
    _validate_session_id_path,
    logger,
    session_mgr,
)


class ApproveActionRequest(BaseModel):
    # See RoundActionRequest: a misspelled field must fail loudly, not silently
    # approve with defaults.
    model_config = ConfigDict(extra="forbid")

    action: str = "approve"  # "approve", "hold", "modify", "override", "reject"
    custom_price: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    custom_reply_text: Optional[str] = Field(default=None, max_length=10_000)
    expected_version: Optional[int] = Field(default=None, ge=1)
    tactical_rationale: Optional[str] = Field(default=None, max_length=2_000)

    @field_validator("action", mode="before")
    @classmethod
    def _validate_action(cls, v: Any) -> Any:
        if v is None:
            return "approve"
        key = str(v).strip().lower()
        if key not in ("approve", "hold", "modify", "override", "reject"):
            raise ValueError(
                f"Unknown approve action {v!r}; expected one of "
                "approve|hold|modify|override|reject"
            )
        return key


def _stored_supplier_draft(sess) -> tuple:
    """Last paste-back draft, its language, the supplier text, and the engine price."""
    from ..engine.bilingual import supplier_message_language

    reply = None
    language = None
    inbound = ""
    price = None
    for turn in reversed(getattr(sess, "history", None) or []):
        meta = dict(getattr(turn, "metadata", None) or {})
        if reply is None:
            text = meta.get("generated_reply")
            if isinstance(text, str) and text.strip():
                reply = text
                language = meta.get("draft_language")
                raw_price = meta.get("recommended_offer_price")
                if isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool):
                    price = float(raw_price)
        if not inbound and getattr(turn, "sender", None) == "opponent" and getattr(turn, "raw_text", None):
            inbound = str(turn.raw_text)
    if language not in ("en", "zh"):
        language = supplier_message_language(inbound)
    return reply, language, inbound, price



def register_sessions_routes(api_v1) -> None:
    @_legacy_route(api_v1.post)("/sessions", response_model=NegotiationSession)
    def create_session(spec: SessionSpec):
        """建立新的談判會話。

        A market_context block is attached here. It is a customs reference.
        The solver, the recommended price, and the circuit breaker do not read it.
        A caller-supplied market_context is kept (still stamped reference-only)
        so a test can prove the price does not move when the block changes.
        """
        from ..market.trade_data import _stamp_reference, build_market_context

        if spec.market_context:
            spec = spec.model_copy(update={"market_context": _stamp_reference(dict(spec.market_context))})
        else:
            ctx = build_market_context(spec.hs_code, spec.product_name)
            hs_update = spec.hs_code
            resolved = ctx.get("hs")
            if not hs_update and isinstance(resolved, str) and resolved.isdigit():
                hs_update = resolved
            spec = spec.model_copy(update={"market_context": ctx, "hs_code": hs_update})
        if session_mgr.get_session(spec.session_id) is not None:
            # [FIX] 同 ID 重建會無聲清空既有會話；至少留下明確日誌供演示/審計追查。
            logger.warning(
                "create_session: session_id=%s already exists and will be RECREATED "
                "(existing history discarded).",
                spec.session_id,
            )
        try:
            return session_mgr.create_session(spec)
        except Exception as exc:
            # [REGRESSION] 舊版 detail=str(e) 會把內部路徑/憑證值洩漏給呼叫端。
            logger.exception("create_session failed")
            raise HTTPException(status_code=500, detail="Failed to create the negotiation session.") from exc


    @_legacy_route(api_v1.get)("/sessions", response_model=List[NegotiationSession])
    def list_sessions():
        """檢索所有現有談判會話。"""
        return session_mgr.list_sessions()


    @_legacy_route(api_v1.get)("/sessions/{session_id}", response_model=NegotiationSession)
    def get_session(session_id: str):
        """以 ID 查詢特定會話。"""
        _validate_session_id_path(session_id)
        sess = session_mgr.get_session(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found.")
        return sess


    @_legacy_route(api_v1.post)("/sessions/{session_id}/approve")
    def approve_session_decision(session_id: str, body: ApproveActionRequest):
        """採購決策副駕駛 (Copilot) 人工審核與定奪授權端點。

        支援功能：
        1. approve: 人工核准系統推薦報價與話術。
        2. modify / override: 人工覆寫報價或自訂回覆話術 (需通過 ceiling_price 硬護欄)。
        3. reject: 人工否決並破局撤退 (WALK_AWAY)。
        """
        _validate_session_id_path(session_id)
        sess = session_mgr.get_session(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found.")

        waiting = {
            AutopilotState.AWAITING_HUMAN_DECISION,
            AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        }
        not_waiting = (
            "This deal is not waiting for a decision, so that action was not recorded."
        )

        # Hold stays on the human. It records the choice and does not approve.
        if body.action == "hold":
            if sess.state not in waiting:
                raise HTTPException(status_code=409, detail=not_waiting)
            held_price = _latest_recommended_price(sess)
            session_mgr.annotate_last_turn(
                session_id,
                {
                    "human_decision": "hold",
                    "held_engine_price": held_price,
                    "rationale": body.tactical_rationale or "Hold firm at the engine price.",
                },
            )
            sess = session_mgr.get_session(session_id)
            return {
                "status": "held",
                "action": "hold",
                "session": sess,
                "autopilot_state": sess.state.value,
                "applied_price": None,
                "message": (
                    "Held the engine price. The deal is still waiting for you to approve or reject. "
                    "Nothing was sent."
                ),
            }

        # CAS 樂觀鎖版本檢查
        if body.expected_version is not None and sess.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"CAS optimistic lock conflict on session '{session_id}': "
                    f"expected_version={body.expected_version}, current_version={sess.version}"
                ),
            )

        # 1. 否決撤退
        if body.action == "reject":
            try:
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.WALK_AWAY,
                    reason=body.tactical_rationale or "Human buyer rejected offer and terminated negotiation.",
                    expected_version=sess.version,
                )
            except InvalidStateTransitionError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="This deal cannot be rejected from its current state, so nothing was recorded.",
                ) from exc
            except OptimisticLockConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {
                "status": "rejected",
                "session": sess,
                "action": "reject",
                "autopilot_state": sess.state.value,
            }

        # Approve writes a buyer turn and leaves the waiting state. A second
        # approve, or an approve after the deal has moved on, must not 500.
        if sess.state != AutopilotState.AWAITING_HUMAN_DECISION:
            raise HTTPException(status_code=409, detail=not_waiting)

        # 2. 核准或覆寫
        # [P0 REGRESSION] 舊版在未指定 custom_price 時用 `sess.latest_our_offer or target`。
        # 但 copilot 模式**不會**寫入 buyer_agent 輪次，latest_our_offer 因此永遠是
        # None（除非使用者先前跑過 autopilot）—— 於是「核准系統建議」實際上核准的是
        # target_price，而不是畫面/話術上的建議價。實測：引擎建議 ¥13.02，按 approve
        # 後送出的卻是 ¥12.00；人類以為自己核准了 AI 的建議，實際批准的是一個不同數字。
        # 現在優先用 /round 寫回最近一則對手輪次 metadata 的 recommended_offer_price，
        # 其次才退回 latest_our_offer / target_price。
        final_price = body.custom_price
        if final_price is None:
            final_price = _latest_recommended_price(sess)
            if final_price is None and _reserve_not_identified(sess):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No counter was issued. One ask without a reserve prior or a markup "
                        "is not enough to approve."
                    ),
                )
            if final_price is None:
                final_price = sess.latest_our_offer or sess.spec.target_price

        # 護欄校驗：不得突破天花板價
        if final_price > sess.spec.ceiling_price:
            raise HTTPException(
                status_code=422,
                detail=f"Approved price ¥{final_price:.2f} breaches walk-away ceiling ¥{sess.spec.ceiling_price:.2f}.",
            )

        stored_reply, draft_language, inbound_text, stored_price = _stored_supplier_draft(sess)
        custom_reply = (body.custom_reply_text or "").strip()
        if custom_reply:
            final_reply_text = custom_reply
            draft_from_supplier = False
        else:
            from ..engine.bilingual import draft_for_approval

            final_reply_text = draft_for_approval(
                float(final_price),
                language=draft_language,
                inbound_text=inbound_text,
                stored_reply=stored_reply,
                stored_price=stored_price,
                product_name=sess.spec.product_name,
                ceiling=float(sess.spec.ceiling_price),
                moq=int(sess.spec.moq or 500),
            )
            draft_from_supplier = True

        # 沿用最近一輪的**實測**遙測值，而不是寫入 p_break=0.0。
        # [P0 REGRESSION] 舊版人工核准輪固定 p_break=0.0，於是：
        #   * SSE 最後一輪的 last_p_break=0.0 → 儀表板顯示「0.0% 破局風險 / SAFE」；
        #   * 之後的手機主管卡沿用最後一輪 → 顯示 0.0% 風險與 N/A 信心；
        #   * 真實的最後一輪風險（例如 0.5%–2%）被這筆人工紀錄覆蓋。
        # 人工核准不會讓風險變成零；它只是把上一輪的風險帶到決策點。因此改為
        # 沿用最近一輪有值的 p_break / posterior / 遙測欄位，並在 metadata 標明
        # 這些數字是 carried-over（非重新計算）。
        _prev = None
        for _t in reversed(sess.history):
            if _t.p_break is not None or _t.posterior_mean is not None:
                _prev = _t
                break
        _prev_meta = dict(getattr(_prev, "metadata", None) or {}) if _prev is not None else {}
        turn_our = NegotiationTurn(
            round_index=sess.current_round,
            sender="buyer_human_approved",
            raw_text=final_reply_text,
            parsed_price=final_price,
            p_break=(_prev.p_break if _prev is not None else None),
            posterior_mean=(_prev.posterior_mean if _prev is not None else None),
            posterior_std=(_prev.posterior_std if _prev is not None else None),
            metadata={
                "approved_by": "human_buyer",
                "action": body.action,
                "rationale": body.tactical_rationale or "Human buyer authorized decision",
                "recommended_offer_price": _latest_recommended_price(sess),
                "metrics_carried_over_from": (
                    f"round {_prev.round_index} ({_prev.sender})" if _prev is not None else None
                ),
                # 主管卡會讀這三個鍵；沿用上一輪真實值，避免核准後卡片變成 0.0/N/A。
                "bayesian_confidence": _prev_meta.get("bayesian_confidence"),
                "expected_margin": _prev_meta.get("expected_margin"),
                "estimated_floor_price": _prev_meta.get("estimated_floor_price"),
                "generated_reply": final_reply_text,
                "draft_language": draft_language,
                "draft_from_supplier": draft_from_supplier,
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
            # 檢查是否達成協議
            is_deal_closed = False
            if sess.latest_opponent_offer is not None and sess.latest_opponent_offer <= final_price:
                is_deal_closed = True

            if is_deal_closed:
                sess = session_mgr.transition_state(
                    session_id, AutopilotState.DEAL_CLOSED, expected_version=sess.version
                )
            else:
                # 轉入常規 Copilot 監聽狀態
                sess = session_mgr.transition_state(
                    session_id, AutopilotState.COPILOT_MONITORING, expected_version=sess.version
                )
        except OptimisticLockConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InvalidStateTransitionError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "That decision does not apply to this deal in its current state, "
                    "so it was not recorded."
                ),
            ) from exc

        return {
            "status": "approved",
            "action": body.action,
            "session": sess,
            "autopilot_state": sess.state.value,
            "applied_price": final_price,
            "reply_text": final_reply_text,
            "turn": turn_our,
        }


