"""Backward-compatible v1 surface: /negotiate, /webhook/evolus, heuristic views.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. /negotiate and
/webhook/evolus are legacy-gated (registered only under TRADEFLOW_LEGACY=1);
the two heuristic views are live on every boot.
"""
from __future__ import annotations
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.config.heuristics import API_EXTRACTED_PRICE_CAP, API_MARKUP_CAP, API_NORM_CAP
from src.app.heuristics_view import public_payload as heuristics_payload
from src.app.security import require_webhook_token
from . import orchestrator
from .state import _legacy_route, logger, model


# ---- 既有端點 (向後相容) ----

class Spec(BaseModel):
    product: str = Field(default="", max_length=512)
    target_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    ceiling_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    moq: int | None = Field(default=None, ge=1, le=10_000_000)
    rounds: int = Field(default=model.ROUNDS, ge=1, le=100)
    n_samples: int = Field(default=2000, ge=1, le=50_000)
    lambda_break: float = Field(default=1.0, ge=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    prior_reserve_mean: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    prior_reserve_std: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    expected_markup: float | None = Field(default=None, ge=0.0, le=API_MARKUP_CAP, allow_inf_nan=False)
    observed_offer: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    hazard_intercept: float | None = Field(default=None, allow_inf_nan=False)
    hazard_tau: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    hazard_quote_gap: float | None = Field(default=None, allow_inf_nan=False)
    hazard_reserve_gap: float | None = Field(default=None, allow_inf_nan=False)
    bayes_obs_noise: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    hazard_credible_alpha: float | None = Field(default=None, gt=0.0, lt=1.0, allow_inf_nan=False)


_WEBHOOK_BOUNDED_NUMERIC_FIELDS = (
    "target_price", "n_samples", "lambda_break", "prior_reserve_mean", "prior_reserve_std",
)


class EvolusExtractedFields(BaseModel):
    """只接受 orchestrator.analyze 真正會消費的欄位，並附上硬界限。

    未知欄位一律忽略（保持 payload 寬鬆，不破壞 Email-in 工作流），但已知欄位
    的型別與範圍必須合法，否則 422。
    """
    model_config = ConfigDict(extra="ignore")

    target_price: Optional[float] = Field(default=None, gt=0.0, le=API_EXTRACTED_PRICE_CAP, allow_inf_nan=False)
    ceiling_price: Optional[float] = Field(default=None, gt=0.0, le=API_EXTRACTED_PRICE_CAP, allow_inf_nan=False)
    walk_away_ceiling: Optional[float] = Field(default=None, gt=0.0, le=API_EXTRACTED_PRICE_CAP, allow_inf_nan=False)
    n_samples: Optional[int] = Field(default=None, ge=1, le=20_000)
    lambda_break: Optional[float] = Field(default=None, ge=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    prior_reserve_mean: Optional[float] = Field(default=None, gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    prior_reserve_std: Optional[float] = Field(default=None, gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)


class EvolusWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    extracted: Optional[EvolusExtractedFields] = None
    offer: Optional[float] = Field(default=None, gt=0.0, allow_inf_nan=False)
    email_body: Optional[str] = Field(default=None, max_length=20_000)
    demo: bool = False



def register_negotiate_routes(app, api_v1) -> None:
    @api_v1.get("/heuristics")
    def documented_heuristics():
        """Safety bands and display quantisation. Not fitted rates."""
        return heuristics_payload()


    @api_v1.get("/simulation/mandates")
    def simulated_mandates():
        """Sandbox commodity mandates. Not factory quotes."""
        from src.app.demo_data import simulation_payload
        return simulation_payload()


    @_legacy_route(app.post)("/negotiate")
    def negotiate(spec: Spec):
        if spec.observed_offer is None:
            raise HTTPException(status_code=422, detail="A supplier quote is required.")
        if spec.ceiling_price < spec.target_price:
            raise HTTPException(status_code=422, detail="The ceiling must be at or above the target.")
        if spec.prior_reserve_mean is not None and spec.prior_reserve_std is None:
            raise HTTPException(
                status_code=422,
                detail="Set prior_reserve_std together with prior_reserve_mean.",
            )
        from src.desk.headline import audit_line, confirmed_prices

        posted_price = {
            "status": "review",
            "parsed": {"unit_price": float(spec.observed_offer)},
            "provenance": {"price_source": "explicit_field", "unconfirmed": True},
        }
        _kept_prices, unconfirmed_prices = confirmed_prices([posted_price])
        raise HTTPException(
            status_code=422,
            detail=(
                "A posted price is not a confirmation. Confirm the supplier unit price "
                "on the desk. " + audit_line(unconfirmed_prices)
            ),
        )


    @_legacy_route(app.post)("/webhook/evolus", dependencies=[Depends(require_webhook_token)])
    def webhook_evolus(payload: EvolusWebhookPayload):
        """Evolus workflow 回呼入口（email-in → extractor → 這裡）。"""
        try:
            body = payload.model_dump(exclude_none=True)
            result = orchestrator.on_webhook(body)
            # [REGRESSION] 第三方草稿是不可信內容（由外部 LLM 產生），不得被當成
            # 系統的真實建議直接呈現。加上明確標記，讓前端/審核者知道它需要人工
            # 覆核，且未經任何護欄驗證。
            draft = result.get("draft_email")
            if isinstance(draft, str):
                if "[DRAFT" not in draft:
                    result["draft_email"] = f"[DRAFT — UNVERIFIED THIRD-PARTY OUTPUT] {draft}"
                result["draft_source"] = "external_llm_unverified"
                result["draft_guardrail_passed"] = False
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("POST /webhook/evolus failed")
            raise HTTPException(status_code=500, detail="Webhook processing failed.") from exc


