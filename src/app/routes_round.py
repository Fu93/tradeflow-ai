"""The v5 /round endpoint.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py, retired dead code
included and labelled: the handler raises unconditionally at the
"A posted price is not a confirmation" gate, so the engine path below it
cannot execute. It is kept because it documents the desk-first pivot and the
guardrails each branch carried; deleting it is a Phase-4 decision, recorded
in docs/internal/UPGRADE_PLAN_2026-10-24.md, not an accident of a refactor.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config.heuristics import (
    API_PRICE_CAP,
    BASELINE_DEPOSIT_PCT,
    BUYER_CAPITAL_RATE,
    CAUTION_RISK_THRESHOLD,
    DEPOSIT_HARD_CAP,
    MOQ_HEADROOM_MULTIPLE,
    NEAR_DEAL_RATIO,
    PRICE_MATCH_RATIO,
    ROUND_N_SAMPLES,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
    STAGNATION_PREMIUM,
)
from src.app.round.circuit_breaker import RoundSafetyInput, circuit_breaker_reason
from ..session.manager import InvalidStateTransitionError, OptimisticLockConflictError
from ..session.schema import AutopilotState, NegotiationTurn
from ..transport.telemetry import notifier
from .state import (
    _legacy_route,
    _validate_session_id_path,
    compute_belief_confidence,
    logger,
    model,
    one_step_counter,
    proposal,
    robust_bayes_update,
    session_mgr,
    TCOEngine,
)


# ---- v4 會話管理 REST APIs ----

# 談判模式枚舉：copilot（人類定奪，預設）| autopilot（全自主巡航）。
class ModeEnum(str, Enum):
    COPILOT = "copilot"
    AUTOPILOT = "autopilot"


class RoundActionRequest(BaseModel):
    # Reject unknown fields. Without this, a misspelled key (e.g. sending
    # `supplier_message` instead of `supplier_text`) was silently dropped by
    # Pydantic, the round ran with empty inbound text, and the API returned
    # HTTP 200 with a plausible-but-wrong NORMAL_COUNTER_OFFER decision instead
    # of an error. Silent fallback to a default decision is the dangerous case:
    # the caller cannot tell a real analysis from a no-input analysis.
    model_config = ConfigDict(extra="forbid")

    opponent_raw_text: Optional[str] = Field(default=None, max_length=10_000)
    supplier_text: Optional[str] = Field(default=None, max_length=10_000)
    opponent_offer_price: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    supplier_offer: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    round_index: Optional[int] = Field(default=None, ge=1, le=100)
    offered_lead_days: Optional[int] = Field(default=None, ge=1, le=3650)
    offered_moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    # [REGRESSION] 原本 deposit_pct 無上界：0.85 會被 Pydantic 接受，然後
    # ActionObjectASTGuardrail 才拋 GuardrailViolationError，直接把路由炸成
    # HTTP 500。加上 60% 的硬紅線，讓越界輸入在邊界就變成 422。
    deposit_pct: Optional[float] = Field(default=None, gt=0.0, le=DEPOSIT_HARD_CAP, allow_inf_nan=False)
    unbundled_tax_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    unbundled_packaging_fee: Optional[float] = Field(default=None, ge=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    expected_version: Optional[int] = Field(default=None, ge=1)
    # [REGRESSION] 預設值原為 "autopilot"，也就是 API 的預設路徑會在零人工核可
    # 下直接進入終止態 DEAL_CLOSED 並產出可直接複製發送的成交確認話術，違反
    # README 承諾的「系統不代發要約，締約權保留在人類手上」。改為 "copilot"，
    # 並以 enum 驗證；None/未知字串一律落到 copilot（絕不落到 autopilot）。
    mode: Optional[ModeEnum] = ModeEnum.COPILOT
    # Stage-2 LLM 語意推理為同步網路呼叫（實測 2–13 秒）。預設關閉以維持回應速度；
    # 需要更深語意理解時才開啟（例如離線批次分析、或使用者明確願意等待）。
    deep_semantic: bool = False
    # AMD live mode is a parallel reading. It does not run inside this handler.
    # The dashboard calls POST /quotes/read beside this round. The solver price
    # below never reads a model field.
    amd_live: bool = False
    # Blank stays None. The default cap in the heuristics module still applies.
    risk_tolerance: Optional[float] = Field(
        default=None,
        ge=RISK_TOLERANCE_FLOOR,
        le=RISK_TOLERANCE_MAX,
        allow_inf_nan=False,
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, v: Any) -> Any:
        """None / 未知字串 -> copilot；只接受已載明的兩種模式。

        [REGRESSION] 舊版型別是 Optional[str]，因此 `"mode": null` 會讓
        `action.mode == "copilot"` 為 False 而落入 autopilot 分支；任何未知字串
        同樣落入 autopilot。現在未知值一律降級到「更安全」的 copilot。
        """
        if v is None:
            return ModeEnum.COPILOT
        if isinstance(v, ModeEnum):
            return v
        key = str(v).strip().lower()
        if key in ("copilot", "assisted", "human"):
            return ModeEnum.COPILOT
        if key in ("autopilot", "auto"):
            return ModeEnum.AUTOPILOT
        logger.warning("Unknown round mode %r; defaulting to copilot (never autopilot).", v)
        return ModeEnum.COPILOT

    def is_autopilot(self) -> bool:
        return self.mode == ModeEnum.AUTOPILOT

    def get_effective_raw_text(self) -> str:
        return self.opponent_raw_text or self.supplier_text or ""

    def get_effective_offer_price(self) -> Optional[float]:
        return self.opponent_offer_price if self.opponent_offer_price is not None else self.supplier_offer

    def has_evidence(self) -> bool:
        """本輪是否帶有可分析的證據（供應商原文或報價）。

        [REGRESSION] 所有欄位都是 Optional，因此 POST {} 原本會回 200：系統照樣
        跑完整求解、回傳一個看起來很合理的建議價，並在 history 寫入一則
        raw_text="" 的 opponent 輪次。呼叫端無法分辨「真的分析了供應商訊息」與
        「什麼都沒送進去，拿到預設決策」。

        檢查放在這裡（模型仍可單獨構造，例如只為驗證 mode 欄位的測試），
        由路由在收到請求時拒絕零證據輪次（見 process_negotiation_round）。
        """
        return bool((self.get_effective_raw_text() or "").strip()) or (
            self.get_effective_offer_price() is not None
        )



def register_round_routes(api_v1) -> None:
    @_legacy_route(api_v1.post)("/sessions/{session_id}/round")
    def process_negotiation_round(session_id: str, action: RoundActionRequest):
        """
        處理單輪網格回報：
        1. 意圖識別與潛台詞抽取
        2. 多目標非價格條款 TCO (Total Cost of Ownership) 歸一化折算
        3. 貝氏信念更新並以 MCTS 引擎重解
        4. 安全熔斷檢查 (P_break >= 15% 觸發斷路器並派發手機推播)
        5. 回傳下一輪策略建議與自駕狀態
        """
        # [REGRESSION] 舊版在 session 不存在時「自動預置」一個新會話，而 session_id
        # 由 URL 路徑自由指定 —— 攻擊者只需一個 URL 就能憑空灌入會話並觸發引擎運算。
        # 路徑參數必須先過同樣的白名單（否則拒絕），杜絕路徑穿越檔案寫入。
        _validate_session_id_path(session_id)
        # [REGRESSION] 零證據輪次必須在邊界被拒絕（見 RoundActionRequest.has_evidence）。
        # 否則引擎會對「沒有任何供應商輸入」的請求產生一份完整推薦並寫入歷史。
        if not action.has_evidence():
            raise HTTPException(
                status_code=422,
                detail=(
                    "A negotiation round requires non-empty opponent_raw_text/supplier_text "
                    "or an opponent_offer_price/supplier_offer; refusing to produce a decision "
                    "from zero evidence."
                ),
            )
        sess = session_mgr.get_session(session_id)
        if not sess:
            raise HTTPException(
                status_code=404,
                detail="No such session. Create one with a target and a ceiling before posting a round.",
            )
        elif action.expected_version is not None and sess.version != action.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"CAS optimistic lock conflict on session '{session_id}': "
                    f"expected_version={action.expected_version}, current_version={sess.version}"
                ),
            )

        if sess.state in (AutopilotState.DEAL_CLOSED, AutopilotState.WALK_AWAY):
            return {
                "session": sess,
                "autopilot_state": sess.state.value,
                "circuit_tripped": False,
                "pending_human_approval": False,
                "trip_reason": f"Session already concluded with state {sess.state.value}",
                "reply_text": (
                    f"This session was already concluded in an earlier round ({sess.state.value}); "
                    "no further rounds were processed."
                ),
            }

        current_round = action.round_index or (sess.current_round + 1)
        target_price = sess.spec.target_price
        ceiling_price = sess.spec.ceiling_price
        opp_price = action.get_effective_offer_price()
        raw_text = action.get_effective_raw_text()

        # 1. 供應商套路意圖分類與潛台詞反編譯
        from ..engine.bilingual import (
            InboundIntentClassifier,
            NumericGuardrailValidator,
            GuardrailViolationError,
            TacticalActionObject,
            TacticalModeEnum,
            ActionObjectASTGuardrail,
            EnglishExecutiveRenderer,
            render_supplier_reply,
            supplier_message_language,
            CURRENT_SCHEMA_VERSION,
        )
        # 1. 供應商套路意圖分類與潛台詞反編譯
        #    效能說明：Stage-1（正則 + 端側 ONNX）實測 ~1ms。
        #    Stage-2（AMD tokenfactory LLM）實測 2–13 秒，故預設不啟用，避免阻塞同步 HTTP 請求。
        #    呼叫端若需要更深語意理解，可設 deep_semantic=true。
        #    MOQ and lead time may be read from the text for the cost breakdown.
        #    The unit price is only the caller's confirmed opponent_offer_price.
        stage1 = InboundIntentClassifier.classify_stage1_only(raw_text)
        if action.deep_semantic:
            inbound_classification = InboundIntentClassifier.classify(raw_text)
        else:
            inbound_classification = stage1
        if opp_price is None:
            raise HTTPException(
                status_code=422,
                detail="Confirm the supplier unit price. Raw text is not a decision input.",
            )
        from src.desk.headline import audit_line, confirmed_prices

        # A caller-supplied price is not a person confirming a span. The desk
        # review route is the confirm flow. This handler does not stamp
        # review_accepted_by and does not turn the posted number into an offer.
        posted_price = {
            "status": "review",
            "parsed": {"unit_price": float(opp_price)},
            "provenance": {"price_source": "explicit_field", "unconfirmed": True},
        }
        _kept_prices, unconfirmed_prices = confirmed_prices([posted_price])
        round_audit = audit_line(unconfirmed_prices)
        raise HTTPException(
            status_code=422,
            detail=(
                "A posted price is not a confirmation. Confirm the supplier unit price "
                "on the desk. " + round_audit
            ),
        )

        # 1.5 多目標非價格條款 TCO 影子成本折算 (Total Cost of Ownership Normalization)
        offered_moq = action.offered_moq or stage1.demanded_moq
        tco_breakdown = None
        effective_opp_price = opp_price
        mandate_lead = None
        if isinstance(sess.spec.corporate_mandate, dict):
            mandate_lead = sess.spec.corporate_mandate.get("baseline_lead_days")

        if opp_price is not None:
            tco_res = TCOEngine.calculate_nonlinear_effective_tco(
                raw_price=opp_price,
                baseline_lead_days=mandate_lead,
                offered_lead_days=action.offered_lead_days,
                baseline_moq=sess.spec.moq,
                offered_moq=offered_moq,
                wacc=BUYER_CAPITAL_RATE,
            )
            effective_opp_price = tco_res["effective_unit_tco"]
            tco_breakdown = tco_res

        if effective_opp_price is None:
            raise HTTPException(
                status_code=422,
                detail="A counter needs a numeric supplier quote.",
            )

        # 2. 計算歸一化報價與更新底價後驗 (以 TCO 等效總成本驅動博弈)
        prev_mean = sess.spec.prior_reserve_mean
        prev_std = sess.spec.prior_reserve_std
        if sess.history:
            for t in reversed(sess.history):
                if t.posterior_mean is not None:
                    prev_mean = t.posterior_mean
                    prev_std = t.posterior_std or prev_std
                    break

        from src.engine.reserve import ReserveState

        state = ReserveState.policy()
        norm_offer = float(effective_opp_price) / float(target_price)
        post_mean = norm_offer * float(state.mean())
        post_std = norm_offer * float(state.std())
        confidence_prior_std = post_std
        del prev_mean, prev_std

        # One next offer. The legacy multi-round grid is not this decision.
        rec = one_step_counter(
            quote=float(effective_opp_price),
            target=float(target_price),
            ceiling=float(ceiling_price),
            mean=float(post_mean),
            std=float(post_std),
            n_samples=int(ROUND_N_SAMPLES),
            risk_tolerance=action.risk_tolerance,
        )
        prop = proposal.build_proposal(
            {
                "target_price": target_price,
                "prior_reserve_mean": post_mean,
                "prior_reserve_std": post_std,
                "walk_away_ceiling": ceiling_price,
                "quote_price": float(effective_opp_price),
                "n_samples": int(ROUND_N_SAMPLES),
                "risk_tolerance": action.risk_tolerance,
            },
            observed_offer=None,
        )

        p_break = rec["breakdown_prob"]
        raw_recommended_price = rec["opening_offer_price"]
        recommended_offer_price = (
            None if raw_recommended_price is None else min(raw_recommended_price, ceiling_price)
        )
        floor_price_est = round(post_mean * target_price, 2)
        bayesian_confidence = compute_belief_confidence(confidence_prior_std, post_std, post_mean)

        # 3. 記錄對手發言輪次
        # [假資料修正 §5.7] 把本輪**真實**算出的遙測值一併持久化進 turn.metadata，
        # 讓後續主管指令確認卡 (_dispatch_supervisor_command) 能還原同一組數字。
        # 舊版只存 p_break / posterior_mean / posterior_std，於是 bayesian_confidence
        # 與 expected_margin 在主管卡只好退回寫死常數 (0.85) 或「預算剩餘」
        # (ceiling - settled) 冒充單件毛利 —— 同一 session 兩張外觀相同的決策卡
        # 互相矛盾（實測同 session：/round conf 0.835 / margin 2.97，
        # 主管卡 conf 85.0% / margin 0.40）。
        turn_opp = NegotiationTurn(
            round_index=current_round,
            sender="opponent",
            raw_text=action.get_effective_raw_text(),
            parsed_price=opp_price,
            p_break=p_break,
            posterior_mean=post_mean,
            posterior_std=post_std,
            metadata={
                # 對方**實際報出**的非價格條款（抽不到就留 None，不虛構）。
                # 下游 (_dispatch_supervisor_command) 從這裡取對方真實模具費；
                # 缺此鍵時它會退回以 raw_text 重新抽取，結果等價。
                "entities": {
                    "mold_fee": getattr(inbound_classification, "demanded_mold_fee", None),
                    "moq": offered_moq,
                    "lead_days": action.offered_lead_days,
                    "deposit_pct": action.deposit_pct,
                    "currency": stage1.mentioned_currency,
                    "quoted_amount": stage1.quoted_amount,
                    "currency_note": stage1.price_currency_note,
                },
                "bayesian_confidence": float(bayesian_confidence),
                "expected_margin": float(rec.get("expected_margin_price", 0.0)),
                "estimated_floor_price": float(floor_price_est),
                # [假資料修正 §5.6] 比價錨定價必須有憑據才成立；我方從未提供比價
                # 來源，故一律記 None（不把 target_price * 1.05 這種內部公式值
                # 冒充成「備選供應商真實報價」）。
                "competing_lead_price": None,
                "recommended_offer_price": rec.get("opening_offer_price"),
                "push_offer_price": rec.get("push_offer_price"),
                "push_half_price": rec.get("push_half_price"),
                "push_double_price": rec.get("push_double_price"),
                "reserve_source": rec.get("reserve_source"),
                "reserve_note": rec.get("prior_note"),
                "heuristic": rec.get("heuristic"),
                "markup_applied": False,
                "audit": round_audit,
            },
        )
        try:
            sess = session_mgr.append_turn(session_id, turn_opp, expected_version=action.expected_version)
        except OptimisticLockConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # 4. 安全熔斷矩陣評估
        # The decision itself moved to src/app/round/circuit_breaker.py so it can be
        # asserted directly instead of only through a full session. The notify and
        # the state transition below stay here: those are the effects, not the check.
        trip_reason = circuit_breaker_reason(
            RoundSafetyInput(
                recommended_price=raw_recommended_price,
                requires_escalation=bool(inbound_classification.requires_escalation),
                primary_intent=inbound_classification.primary_intent,
                tactical_rationale=str(inbound_classification.tactical_rationale or ""),
                breakdown_probability=float(p_break),
                risk_tolerance=action.risk_tolerance,
                breakdown_trip_threshold=float(model.BREAKDOWN_TRIP_THRESHOLD),
                opponent_price=opp_price,
                ceiling_price=float(ceiling_price),
            )
        )

        if trip_reason and raw_recommended_price is None:
            notifier.notify_circuit_breaker(
                session_id=session_id,
                product=sess.spec.product_name,
                opponent_offer=opp_price or target_price,
                p_break=p_break,
                advice_text=prop["advice_text"],
                proposed_price=None,
            )
            if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
                try:
                    sess = session_mgr.transition_state(
                        session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=trip_reason
                    )
                except Exception as _trip_err:
                    logger.warning(
                        "Circuit-breaker state transition failed for session %s: %s",
                        session_id, _trip_err,
                    )
            v2_belief = robust_bayes_update(
                prior_mean=post_mean,
                prior_std=post_std,
                observed_offer=None,
            )
            return {
                "session": sess,
                "autopilot_state": sess.state.value,
                "circuit_tripped": True,
                "pending_human_approval": True,
                "trip_reason": trip_reason,
                "recommendation": rec,
                "proposal": prop,
                "action_object": None,
                "reply_text": None,
                "tco_breakdown": tco_breakdown,
                "effective_opp_price": effective_opp_price,
                "audit": round_audit,
                "bayesian_belief_state": v2_belief.model_dump(),
            }

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
                tactical_mode=(
                    TacticalModeEnum.FIRM_HOLD
                    if not model.breakdown_circuit_tripped(p_break, action.risk_tolerance)
                    else TacticalModeEnum.WALK_AWAY
                ),
                product_name=sess.spec.product_name,
                lead_days=action.offered_lead_days,
                deposit_pct=(
                    action.deposit_pct
                    if action.deposit_pct is not None
                    else float(BASELINE_DEPOSIT_PCT)
                ),
                moq=offered_moq or sess.spec.moq,
                counterpart_intent=inbound_classification.primary_intent,
                counterpart_offered_price=opp_price,
                circuit_tripped=True,
                trip_reason=trip_reason,
            )
            if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
                # [修正] 熔斷是安全機制，不應因狀態機缺口而讓整個請求 500。
                # 狀態機已補上 copilot 路徑的轉移；此處再加一層防禦，確保即使
                # 未來新增狀態忘了開放轉移，熔斷仍會生效（記錄警告，不中斷回應）。
                try:
                    sess = session_mgr.transition_state(
                        session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=trip_reason
                    )
                except Exception as _trip_err:
                    import logging as _logging
                    _logging.getLogger("tradeflow.round").warning(
                        "Circuit-breaker state transition failed for session %s: %s "
                        "(the trip is still reported in the response)",
                        session_id, _trip_err,
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
            # [P0 FIX] post_mean/post_std 已包含本輪報價，不可再觀測一次
            # （否則熔斷分支的後驗會與 history 不一致；見第 5.8 節的完整說明）。
            v2_belief = robust_bayes_update(
                prior_mean=post_mean,
                prior_std=post_std,
                observed_offer=None,
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
                "audit": round_audit,
                "bayesian_belief_state": v2_belief.model_dump(),
            }

        # 5. 多維契約條款置換 (Logrolling Pareto Optimization)
        logrolling_data = None
        from ..engine.logrolling import (
            LogrollingEngine,
            CorporateMandate,
            SupplierCurrentOffer,
            ASTMandateViolationError,
        )
        # 若有設定或自動生成預設 CorporateMandate，且價格處於僵局或高於目標價 8%
        mandate_obj = None
        if sess.spec.corporate_mandate:
            try:
                mandate_fields = dict(sess.spec.corporate_mandate)
                if "max_allowed_moq" not in mandate_fields and sess.spec.moq:
                    mandate_fields["max_allowed_moq"] = int(sess.spec.moq) * int(MOQ_HEADROOM_MULTIPLE)
                mandate_obj = CorporateMandate(**mandate_fields)
            except Exception:
                mandate_obj = None

        # [假資料修正 §5.5] 若對方**從未報出**任何模具費，就不該在 mandate 上主張
        # 「要求免除模具費」—— 否則下方的 BuyerScriptGenerator 會憑空向對方要求
        # 減免一筆對方從未開過的價（舊版即以寫死的 3000.0 運作）。
        if mandate_obj is not None and (inbound_classification.demanded_mold_fee or 0.0) <= 0.0:
            mandate_obj = mandate_obj.model_copy(
                update={"mold_fee_exemption_demanded": False}
            )

        # 判定是否觸發多維條款置換 (當對手不降價或哭窮抬價時)
        should_logroll = (
            mandate_obj is not None
            and opp_price is not None
            and opp_price > target_price * STAGNATION_PREMIUM
            and current_round >= 2
            and mandate_obj.baseline_lead_days is not None
            and mandate_obj.max_acceptable_lead_days is not None
            and mandate_obj.min_desired_lead_days is not None
            and mandate_obj.baseline_moq is not None
            and mandate_obj.max_allowed_moq is not None
            and (action.offered_lead_days is not None or mandate_obj.baseline_lead_days is not None)
            and (offered_moq or sess.spec.moq or mandate_obj.baseline_moq)
        )

        if should_logroll:
            # [假資料修正 — 見 DEEP_DEFECT_AUDIT.md §5.5] 舊版把對方模具費寫死為
            # `mold_fee=3000.0`：系統毫不知情對方實際開價，就憑空發明一個「¥3000
            # 模具費」，再向對方宣稱「全額減免」—— 這是一筆由程式碼虛構出來的金錢承諾。
            # 現改為只採用**對方實際報出**的模具費（分類器由原文抽取）；對方沒提，
            # 就不許憑空主張一個金額。供應商沒報模具費時 mold_fee=0，
            # `demand_mold_waiver` 亦為 False，置換策略改為只用有依據的條款。
            supplier_mold_fee = inbound_classification.demanded_mold_fee or 0.0
            # [RETIRED] ``demand_mold_waiver`` was computed here in the original
            # main.py and never read anywhere; ruff F841 flags what was already
            # dead in the unreachable engine path below the confirm gate.
            # [假資料修正 §5.8] SupplierCurrentOffer 的欄位語意是「供應商**報出**的
            # 條件」（lead_days = 供應商報出之交期、deposit_pct = 供應商要求之定金比例）。
            # 舊版在此寫 `action.offered_lead_days or 25`、`action.deposit_pct or 0.30`：
            # 當對方只說「最低 15.80」（從未提交期、從未提定金）時，這兩個欄位被填入
            # 從未觀測過的 25 / 0.30，下游 BuyerScriptGenerator 便以對方名義輸出
            # 「交期必須由 25 天全速壓縮至 12 天內交付」「特批預付 50% 定金」——
            # 把系統的買方側備援假設，說成對手已開出的條件。這是歸因錯誤（fabricated
            # attribution），比單純的常數更難被察覺，因為它在對外文本裡是**引述語氣**。
            #
            # 修正原則（採 (b)：25/30 保留為我方規劃基線，但不再冒充對手報價）：
            #   * `supp_offer_obj` 只帶**真實觀測到**的條款。對方未報交期/定金時，
            #     不再以 `or 25` / `or 0.30` 填值，改用 mandate 的 baseline（語意為
            #     「我方以什麼基準規劃」，不是「對方開出什麼」），讓下游的比值判斷
            #     （`l < offer.lead_days`）仍可運作，但引述對象改為我方規劃基線。
            #   * 對外文本不得再以「貴廠開出／對方報價」語氣轉述這兩個數字；需要
            #     這類引述時必須换成「我方以 X 天為規劃基準」的誠實表述。
            #     -> BuyerScriptGenerator 的具體改法見回報中的協調建議（logrolling.py）。
            supp_offer_obj = SupplierCurrentOffer(
                price=opp_price,
                lead_days=(
                    action.offered_lead_days
                    if action.offered_lead_days is not None
                    else mandate_obj.baseline_lead_days
                ),
                deposit_pct=(
                    action.deposit_pct
                    if action.deposit_pct is not None
                    else mandate_obj.baseline_deposit_pct
                ),
                moq=offered_moq or sess.spec.moq,
                mold_fee=supplier_mold_fee,
                is_price_stagnant=True,
            )
            try:
                lr_res = LogrollingEngine.evaluate(mandate_obj, supp_offer_obj)
                best_prop = lr_res.best_recommendation
                logrolling_data = best_prop.model_dump()
                # [假資料修正 §5.8] `buyer_script_zh` 由 BuyerScriptGenerator 以
                # `offer.lead_days` / `offer.deposit_pct` 產生「交期必須由 X 天全速
                # 壓縮至 Y 天」這類**以對方為前提的引述**。當對方從未報出交期/定金時，
                # X 只是我方規劃基線（mandate.baseline_*），把它放進對外文本即成
                # 「把買方假設說成對手開出的條件」。
                #
                # 在本代理的可改範圍（main.py）內，能做的誠實處置是：
                #   1. 標註每項條款的真實來源（observed / assumed）；
                #   2. 若某項條件**從未由對方或主管提供**，就從對外話術中移除對它的
                #      引述句（改由話術端只陳述我方訴求），而不是繼續輸出引述語氣。
                # BuyerScriptGenerator 的重構（把 `由 {offer.lead_days} 天` 改為
                # 有條件才輸出）需要改 logrolling.py，已列入回報的協調建議；
                # 在此之前，此處先確保 API 回傳明確揭露哪些數字沒有來源。
                terms_observed = {
                    "lead_days": action.offered_lead_days is not None,
                    "deposit_pct": action.deposit_pct is not None,
                    "moq": bool(offered_moq),
                }
                logrolling_data["terms_provenance"] = terms_observed
                logrolling_data["terms_provenance_note"] = (
                    "False = this term was never stated by the counterpart or the supervisor; "
                    "the number shown is a buyer-side planning baseline, NOT the supplier's offer."
                )
                auto_reply_text = best_prop.buyer_script_zh
            except ASTMandateViolationError:
                # [FIX] 原本裸吞一切異常＝假成功且零日誌；現在只接可預期的護欄違規
                # （有明確降級語義），其餘異常放行交給 500 handler，不再無聲失效。
                logger.warning(
                    "Logrolling proposal rejected by AST mandate guardrail (session=%s); "
                    "suppressing auto-reply.",
                    session_id,
                )
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
            # [假資料修正] waive amount 一律取**對方實際報出的**模具費；
            # 舊版在這裡再寫死一次 3000.0，與上面發明的值連成一條虛構鏈。
            waive_mold_amount = float(supplier_mold_fee) if mold_exempt else 0.0
        else:
            t_mode = (
                TacticalModeEnum.LEAN_IN
                if p_break < CAUTION_RISK_THRESHOLD
                else (
                    TacticalModeEnum.CROSS_ANCHOR
                    if not model.breakdown_circuit_tripped(p_break, action.risk_tolerance)
                    else TacticalModeEnum.SIDELINE
                )
            )
            auth_price = recommended_offer_price
            # [假資料修正 §5.8] 與上方 supp_offer_obj 同一原則：這裡的 lead_days /
            # deposit_pct 是「本輪我方採用的交期/定金條件」，不得由 `or 25` /
            # `or 0.30` 補一個從未觀測的值冒充成對方開出的條件。對方報了就報了，
            # 沒報就沿用 mandate 的規劃基線（語意：我方規劃基準，非對手報價）。
            lead_days_val = action.offered_lead_days
            deposit_pct_val = (
                action.deposit_pct
                if action.deposit_pct is not None
                else (mandate_obj.baseline_deposit_pct if mandate_obj is not None else None)
            )
            moq_val = offered_moq or sess.spec.moq
            if deposit_pct_val is None:
                deposit_pct_val = float(BASELINE_DEPOSIT_PCT)
            # A quoted mold fee is the supplier's number. Lead time and MOQ stay
            # unset until the buyer provides them, and that absence does not drop
            # the fee the supplier already wrote down.
            quoted_mold_fee = float(inbound_classification.demanded_mold_fee or 0.0)
            mold_exempt = bool(
                mandate_obj is not None
                and mandate_obj.mold_fee_exemption_demanded
                and quoted_mold_fee > 0.0
            )
            waive_mold_amount = quoted_mold_fee if mold_exempt else 0.0

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
            # [假資料修正 §5.6] 舊版寫 `competing_lead_price=target_price * 1.05`，
            # 把「我方採購目標價」乘出來的數字當成「備選供應商真實錨定價」，再以
            # 「合格樣品已給到 ¥12.60」這種已取得樣品報價的既成事實語氣送給談判對手。
            # 這個數字沒有任何來源：對方從未聽過、我方從未詢價、系統也從未收到過
            # 任何比價資料；它只是 target_price 的 1.05 倍（target ¥20→¥21.00、
            # ¥50→¥52.50、¥100→¥105.00 皆同構，實測可證）。對外宣稱一筆不存在的
            # 報價是本系統最危險的一類缺陷：一旦對方要求提供樣品或比價單，整套
            # 議價策略當場失信。
            # 現改為 None，讓渲染落到「同規格同行報價」的誠實分支（不帶金額）。
            # 注意：除非使用者/呼叫端明確提供**有憑據的比價來源**，否則此欄位
            # 一律保持 None，不得由任何內部價格（target/ceiling/offer）公式推算。
            # 目前 RoundActionRequest 與 SessionSpec 皆無比價來源欄位，故不渲染數字。
            competing_lead_price=None,
            circuit_tripped=False,
        )

        # 5.6 AST 不變量硬護欄驗證單一真相源。
        # [REGRESSION] 舊版直接呼叫、無 try/except：任何違反授權不變量的輸入都會讓
        # GuardrailViolationError 穿出路由變成 HTTP 500（並附帶內部 traceback）。
        # 安全護欄應「延後/阻斷」請求，而不是讓端點崩潰。違規時改為以確定性的
        # 熔斷回應（circuit_tripped=True）回報，並保留 pending_human_approval，
        # 讓人類決定下一步 —— 既不 500，也不解除安全機制。
        try:
            ActionObjectASTGuardrail.verify(action_obj)
        except GuardrailViolationError as gv:
            logger.warning("Round guardrail violation for session %s: %s", session_id, gv)
            guard_reason = f"Guardrail invariant violation: {gv}"
            if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
                try:
                    sess = session_mgr.transition_state(
                        session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=guard_reason
                    )
                except Exception as _trip_err:
                    logger.warning(
                        "Circuit-breaker transition failed for session %s after guardrail "
                        "violation: %s (the trip is still reported in the response)",
                        session_id, _trip_err,
                    )
            return {
                "session": sess,
                "autopilot_state": sess.state.value,
                "circuit_tripped": True,
                "pending_human_approval": True,
                "trip_reason": guard_reason,
                "guardrail": {"passed": False, "reason": str(gv)},
                "recommendation": rec,
                "proposal": prop,
                "action_object": None,
                "reply_text": None,
                "tco_breakdown": tco_breakdown,
                "effective_opp_price": effective_opp_price,
            }

        # 5.7 雙向表面渲染
        # 中文表面渲染 (面向供應商對話)：沿海地道行話，徹底消除外商溢價
        auto_reply_text = render_supplier_reply(action_obj, raw_text)

        # 文本層數值護欄複核 (防止任何渲染偏差)
        try:
            NumericGuardrailValidator.validate_reply(
                auto_reply_text,
                authorized_price=auth_price,
                walk_away_ceiling=ceiling_price,
            )
        except GuardrailViolationError:
            auto_reply_text = f"經核算，我方首批訂單確定意向價為 ¥{auth_price:.2f}，現款現結走官方系統，請確認排單。"

        # [REGRESSION] 英文高管決策卡過去**完全未經護欄**，而它正是被推播到主管手機、
        # 主管據以授權的產物。w2-guardrails 提供了 validate_bilingual(zh, en, ...) 雙表面
        # 校驗器；存在時用它一次驗證中英文兩面，否則退回既有的單面 validate_reply。
        # 任一表面違規即同時處置兩面：中文換安全模板，英文卡換成不含價格承諾的
        # 安全文字（避免「壞價格」被送到主管手機上授權）。
        executive_card_en = EnglishExecutiveRenderer.render_executive_card(action_obj)
        english_card_unverified = False
        validate_bilingual = getattr(NumericGuardrailValidator, "validate_bilingual", None)
        try:
            if callable(validate_bilingual):
                validate_bilingual(
                    auto_reply_text,
                    executive_card_en,
                    authorized_price=auth_price,
                    walk_away_ceiling=ceiling_price,
                )
            else:
                # 後備：至少對英文卡套用既有的數值上限校驗。
                NumericGuardrailValidator.validate_reply(
                    executive_card_en,
                    authorized_price=auth_price,
                    walk_away_ceiling=ceiling_price,
                )
        except GuardrailViolationError as gv:
            logger.warning("Bilingual surface guardrail violation for session %s: %s", session_id, gv)
            english_card_unverified = True
            auto_reply_text = f"經核算，我方首批訂單確定意向價為 ¥{auth_price:.2f}，現款現結走官方系統，請確認排單。"
            executive_card_en = (
                "GUARDRAIL REVIEW REQUIRED — the rendered executive card failed numeric "
                f"validation and is withheld. Authorized price: ¥{auth_price:.2f} "
                f"(ceiling ¥{ceiling_price:.2f}). Approve or override via /approve."
            )

        # 英文高管決策卡與遙測摘要 (面向西方採購主管 / CPO 儀表板)
        telemetry_eng = EnglishExecutiveRenderer.render_telemetry_summary(action_obj)

        # 5.8 v5 Should-Cost 與校準貝氏混合推斷增強
        # [P0 REGRESSION — 引擎審計發現] 舊版寫 `observed_offer=opp_price/target_price`
        # 再配 `prior_mean=post_mean`，但 post_mean/post_std **已經包含本輪這筆報價**
        # （上面第 2 步的 bayes_update）。等於同一筆證據被更新第二次，使得
        # bayesian_belief_state.posterior_mean (1.0157) 與會話 history 的
        # posterior_mean (1.0257) 不一致 —— 同一輪兩個後驗。
        # 現在改為：直接把本輪已算出的後驗傳入，不再重複觀測（observed_offer=None）。
        v2_belief = robust_bayes_update(
            prior_mean=post_mean,
            prior_std=post_std,
            observed_offer=None,
            our_next_offer=auth_price / target_price if auth_price else None,
        )
        # 6. 分流：採購決策副駕駛 (Copilot) vs 全自主巡航 (Autopilot)
        # [REGRESSION] 舊版預設 mode="autopilot"（現在改為 copilot），且 autopilot
        # 分支會在**零人工核可**下一輪就進入終止態 DEAL_CLOSED，並回傳一段可直接
        # 複製發送的成交確認話術（pending_human_approval=False）。這違反 README 的
        # 「系統不代發要約 —— 締約權必須保留在人類手上」。
        # 現在採購模式一律停在等待人類決策；只有呼叫端「明確」要求 autopilot 時才
        # 走自駕分支，且該分支即使判定成交也不直接進入 DEAL_CLOSED。
        if not action.is_autopilot():
            if sess.state != AutopilotState.AWAITING_HUMAN_DECISION:
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.AWAITING_HUMAN_DECISION,
                    reason="Copilot decision radar prepared; awaiting human buyer review and authorization.",
                )
            # [P0 REGRESSION] copilot 模式不寫 buyer_agent 輪次，因此「引擎建議價」
            # 原本完全沒有被持久化；/approve 只能退回 target_price（見 approve 端點
            # 的說明）。這裡把本輪真正算出的建議價寫回剛追加的對手輪 metadata，
            # 讓後續 /approve、SSE 與決策面板都能讀到同一個數字。
            try:
                session_mgr.annotate_last_turn(
                    session_id,
                    {
                        "recommended_offer_price": float(auth_price),
                        "recommended_tactical_mode": getattr(t_mode, "value", str(t_mode)),
                        # [UI AUDIT FIX — 2026-10-17] copilot 模式不寫 buyer_agent 輪次，
                        # 生成話術只存在於 /round 的 HTTP 回應裡。使用者一重新整理，
                        # 儀表板「Latest Generated Reply (human sends manually)」就變成
                        # 「—」，而這段話正是要人工貼回聊天室的內容。一併持久化。
                        "generated_reply": auto_reply_text,
                        "draft_language": supplier_message_language(raw_text),
                    },
                )
                # 回傳最新快照（含剛才寫入的 metadata）。
                refreshed = session_mgr.get_session(session_id)
                if refreshed is not None:
                    sess = refreshed
            except Exception:
                logger.warning(
                    "Could not persist recommended_offer_price for session %s; "
                    "/approve will fall back to the last known offer.",
                    session_id, exc_info=True,
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
                "executive_card_unverified": english_card_unverified,
                "action_object": action_obj.model_dump(),
                "recommendation": rec,
                "proposal": prop,
                "recommended_offer_price": auth_price,
                "price_source": "explicit_field",
                "llm_price_ignored": True,
                "audit": round_audit,
                "amd_live": {
                    "requested": bool(action.amd_live),
                    "decides_price": False,
                    "note": "POST /api/v1/quotes/read does the parallel AMD reading. This price is the solver's.",
                },
                "reply_text": auto_reply_text,
                "bayesian_belief_state": v2_belief.model_dump(),
            }

        # ---- Autopilot 巡航模式分支（僅在呼叫端明確要求時）----
        if sess.state == AutopilotState.IDLE:
            sess = session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING)

        is_deal_closed = False
        awaiting_human_approval = False
        if opp_price is not None:
            if opp_price <= target_price * NEAR_DEAL_RATIO:
                is_deal_closed = True
            elif (
                current_round >= sess.spec.max_rounds
                and opp_price <= ceiling_price
                and abs(opp_price - recommended_offer_price) <= (PRICE_MATCH_RATIO * target_price)
            ):
                is_deal_closed = True

        if is_deal_closed and sess.state != AutopilotState.DEAL_CLOSED:
            # [REGRESSION] autopilot 判定成交時**不**進入 DEAL_CLOSED。它產生一份
            # 「待人類核可」的成交建議（awaiting_human_approval=True），實際締約仍由
            # /approve 執行。因此 pending_human_approval 不再是誤導性的 False。
            awaiting_human_approval = True
            try:
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.AWAITING_HUMAN_DECISION,
                    reason="Autopilot detected a deal candidate; awaiting human buyer authorization before closing.",
                )
            except (InvalidStateTransitionError, OptimisticLockConflictError) as _st_err:
                logger.warning(
                    "Could not park autopilot deal candidate for session %s at "
                    "AWAITING_HUMAN_DECISION: %s", session_id, _st_err,
                )
            # Operator alert only. The text tells the buyer the contract is not sent.
            # Do not call Evolus (or any other outbound drafter) here: this branch
            # runs before /approve, and a model call that produces reply text is not
            # a human send.
            notifier.notify_deal_closed(
                session_id=session_id,
                product=sess.spec.product_name,
                final_price=opp_price,
                savings_pct=(ceiling_price - opp_price) / ceiling_price,
                rounds=current_round,
            )

        # [假資料修正 §5.7] 我方輪次同樣帶上本輪真實遙測值，讓「最近一輪」無論
        # 取自對手輪或我方輪，主管卡還原出的都是同一組數字（同一 session 不因
        # 端點不同而出現兩個 bayesian_confidence）。
        turn_our = NegotiationTurn(
            round_index=current_round,
            sender="buyer_agent",
            raw_text=auto_reply_text,
            parsed_price=auth_price,
            p_break=p_break,
            posterior_mean=post_mean,
            posterior_std=post_std,
            metadata={
                "bayesian_confidence": float(bayesian_confidence),
                "expected_margin": float(rec.get("expected_margin_price", 0.0)),
                "estimated_floor_price": float(floor_price_est),
                "competing_lead_price": None,
            },
            logrolling_proposal=logrolling_data,
        )
        sess = session_mgr.append_turn(session_id, turn_our)

        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": False,
            # [REGRESSION] 舊版固定回 False —— 即使 autopilot 已宣告成交。現在誠實
            # 反映「成交候選仍在等人類核可」。
            "pending_human_approval": awaiting_human_approval,
            "awaiting_human_approval": awaiting_human_approval,
            "turn": turn_our,
            "inbound_intent": inbound_classification.primary_intent.value,
            "tco_breakdown": tco_breakdown,
            "logrolling_proposal": logrolling_data,
            "effective_opp_price": effective_opp_price,
            "telemetry_summary_en": telemetry_eng,
            "executive_card_en": executive_card_en,
            "executive_card_unverified": english_card_unverified,
            "action_object": action_obj.model_dump(),
            "recommendation": rec,
            "proposal": prop,
            "recommended_offer_price": auth_price,
            "price_source": "explicit_field",
            "llm_price_ignored": True,
            "audit": round_audit,
            "amd_live": {
                "requested": bool(action.amd_live),
                "decides_price": False,
                "note": "POST /api/v1/quotes/read does the parallel AMD reading. This price is the solver's.",
            },
            "reply_text": auto_reply_text,
            "bayesian_belief_state": v2_belief.model_dump(),
        }


