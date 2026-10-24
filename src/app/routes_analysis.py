"""Stateless analysis endpoints: TCO shadow cost, Bayesian update, quote reading.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. These endpoints never
set a price: quotes/read is the parallel AMD reading and says so in its body.
"""
from __future__ import annotations
from typing import Optional

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from src.config.heuristics import API_MARKUP_CAP, API_NORM_CAP, API_PRICE_CAP, BUYER_CAPITAL_RATE
from .state import TCOEngine, _legacy_route, robust_bayes_update


# ---- v5 條款 TCO 與貝氏推斷 Request Models ----

class TCOEvalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_price: float = Field(..., gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    baseline_lead_days: Optional[int] = Field(default=None, ge=1, le=3650)
    offered_lead_days: Optional[int] = Field(default=None, ge=1, le=3650)
    baseline_moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    offered_moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    wacc: float = Field(default=BUYER_CAPITAL_RATE, ge=0.0, le=1.0, allow_inf_nan=False)


class BayesianUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # [REGRESSION] prior_std 原無 gt=0；prior_std=0 會讓 bayesian_v2 出現
    # math domain error，並以裸 500 回應（FastAPI 的 ServerErrorMiddleware 會
    # 繞過 detail 包裝）。現在在邊界就回 422。
    # The mean and the width are the caller's. There is no silent 1.08 prior.
    prior_mean: float = Field(..., gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    prior_std: float = Field(..., gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    observed_offer: float = Field(..., gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    estimated_markup: float = Field(default=0.0, ge=0.0, le=API_MARKUP_CAP, allow_inf_nan=False)
    our_next_offer: Optional[float] = Field(default=None, gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)


class SemanticClassifyRequest(BaseModel):
    """Stage-2 語意分類預覽請求。"""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., max_length=10_000)
    enable_stage2: bool = True  # 是否實際呼叫 AMD 託管模型（2–13 秒）


class QuoteReadRequest(BaseModel):
    """Paste or upload a supplier quote. The response never contains our price."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=10_000)
    amd_live: bool = False
    image_base64: Optional[str] = Field(default=None, max_length=1_500_000)
    sample_id: Optional[str] = Field(default=None, max_length=64)



def register_analysis_routes(api_v1) -> None:
    @_legacy_route(api_v1.post)("/tco/evaluate")
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


    @_legacy_route(api_v1.post)("/bayesian/update")
    def calculate_bayesian_update(req: BayesianUpdateRequest):
        """Student-t reserve update. The caller supplies the prior."""
        res = robust_bayes_update(
            prior_mean=req.prior_mean,
            prior_std=req.prior_std,
            observed_offer=req.observed_offer,
            estimated_markup=req.estimated_markup,
            our_next_offer=req.our_next_offer,
        )
        return {"status": "ok", "belief_state": res.model_dump()}


    @api_v1.post("/quotes/read")
    def read_quote(body: QuoteReadRequest, request: Request):
        """Parallel AMD reading. Does not create a round and does not set a price."""
        from ..app.rate_limit import arm_amd_call
        from ..engine.quote_read import read_supplier_quote

        if body.amd_live:
            arm_amd_call(request)
        result = read_supplier_quote(
            body.text,
            amd_live=body.amd_live,
            image_base64=body.image_base64,
        )
        if body.sample_id:
            result["sample_id"] = body.sample_id
            result["simulated_sample"] = True
        result["buyer_price"] = None
        result["decides_price"] = False
        return result


    @api_v1.post("/classify/semantic")
    async def classify_semantic_preview(body: SemanticClassifyRequest, request: Request):
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
        stage2_error = None
        if body.enable_stage2:
            from ..app.rate_limit import arm_amd_call

            arm_amd_call(request)
            deep = await _Clf.classify_with_llm_async(text)
            if deep is not None:
                stage2 = {
                    "primary_intent": deep.primary_intent.value,
                    "confidence": deep.confidence,
                    "tactical_rationale": deep.tactical_rationale,
                    "requires_escalation": deep.requires_escalation,
                }
            else:
                # [P0 REGRESSION] 舊版只回 stage2_available=false，呼叫端無法分辨
                # 「模型說不需要深化」與「呼叫失敗／超時／輸出無法解析」。
                stage2_error = getattr(_Clf, "last_llm_error", None) or (
                    "Stage-2 model unavailable or returned no parsable result; see server logs."
                )

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
            # 明確區分「未請求」與「請求失敗」，並附上實測延遲（None = 未執行）。
            "stage2_requested": bool(body.enable_stage2),
            "stage2_error": stage2_error,
            "stage2_latency_ms": getattr(_Clf, "last_llm_latency_ms", None) if body.enable_stage2 else None,
        }


