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
    DEPOSIT_HARD_CAP,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
)
from ..session.schema import AutopilotState
from .state import (
    _legacy_route,
    _validate_session_id_path,
    logger,
    session_mgr,
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

        opp_price = action.get_effective_offer_price()
        raw_text = action.get_effective_raw_text()

        # 1. 供應商套路意圖分類與潛台詞反編譯
        #    效能說明：Stage-1（正則 + 端側 ONNX）實測 ~1ms。
        #    Stage-2（AMD tokenfactory LLM）實測 2–13 秒，故預設不啟用，避免阻塞同步 HTTP 請求。
        #    呼叫端若需要更深語意理解時，前端併行呼叫 /classify/semantic。
        #    The unit price is only the caller's confirmed opponent_offer_price.
        from ..engine.bilingual import InboundIntentClassifier

        InboundIntentClassifier.classify_stage1_only(raw_text)  # warm + validate the text pipeline
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
