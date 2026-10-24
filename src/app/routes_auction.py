"""The multi-supplier reverse auction simulator endpoint.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. Not legacy-gated: the
dashboard's "Run auction" widget calls it, and the panel is labelled
"Simulated suppliers, not live factory quotes" on its face.
"""
from __future__ import annotations
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config.heuristics import (
    API_MARKUP_CAP,
    API_NORM_CAP,
    API_PRICE_CAP,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
)
from src.desk.names import supplier_name_key
from ..engine.multi_solver import simulate_reverse_auction
from .state import logger


class MultiAuctionSupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=256)
    profile: Optional[str] = Field(default="Standard", max_length=128)
    quote: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    freight: Optional[float] = Field(default=None, ge=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    duty_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    fees: Optional[float] = Field(default=None, ge=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    currency: Optional[str] = Field(default=None, max_length=8)
    fx_to_base: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)


class MultiAuctionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # [REGRESSION] 舊版 n_samples / n_rounds / rounds_quotes 全無上界，且端點
    # 無認證：單一請求即可配置任意大的張量工作集（n_samples=1e6 約需 32GB），
    # 一個便宜請求就能打爆記憶體。現在全部給定硬上界。
    target_price: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    walk_away_ceiling: Optional[float] = Field(default=None, gt=0.0, le=API_PRICE_CAP, allow_inf_nan=False)
    moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    demo: bool = False
    n_samples: int = Field(default=20000, ge=1, le=50_000)
    n_rounds: int = Field(default=3, ge=1, le=10)
    suppliers: Optional[List[MultiAuctionSupplierInput]] = Field(default=None, max_length=32)
    rounds_quotes: Optional[List[List[float]]] = Field(default=None, max_length=10)
    # Context only. simulate_reverse_auction does not receive these fields.
    hs_code: Optional[str] = None
    product_name: Optional[str] = Field(default=None, max_length=512)
    # Blank stays None so the server policy cap applies. The caller sets it.
    risk_tolerance: Optional[float] = Field(
        default=None,
        ge=RISK_TOLERANCE_FLOOR,
        le=RISK_TOLERANCE_MAX,
        allow_inf_nan=False,
    )
    base_currency: str = Field(default="CNY", min_length=3, max_length=8)
    # Fetched ECB and UK tariff figures enter the solve only when this is set.
    confirm_market_refs: bool = False
    round_outcomes: Optional[List[List[Optional[str]]]] = Field(default=None, max_length=10)
    prior_mean: Optional[float] = Field(default=None, gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    prior_std: Optional[float] = Field(default=None, gt=0.0, le=API_NORM_CAP, allow_inf_nan=False)
    expected_markup: Optional[float] = Field(default=None, ge=0.0, le=API_MARKUP_CAP, allow_inf_nan=False)
    hazard_intercept: Optional[float] = Field(default=None, allow_inf_nan=False)
    hazard_tau: Optional[float] = Field(default=None, gt=0.0, allow_inf_nan=False)
    hazard_quote_gap: Optional[float] = Field(default=None, allow_inf_nan=False)
    hazard_reserve_gap: Optional[float] = Field(default=None, allow_inf_nan=False)
    bayes_obs_noise: Optional[float] = Field(default=None, gt=0.0, allow_inf_nan=False)
    hazard_credible_alpha: Optional[float] = Field(default=None, gt=0.0, lt=1.0, allow_inf_nan=False)

    @field_validator("rounds_quotes")
    @classmethod
    def _bound_rounds_quotes(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        """限制逐輪報價矩陣的維度與數值範圍。

        [REGRESSION] rounds_quotes 原本完全不受限：輪數可無限、每輪列長可無限，
        且元素可以是非有限值（Infinity / NaN 會污染後續張量運算）。
        """
        if v is None:
            return None
        if len(v) > 10:
            raise ValueError("rounds_quotes may contain at most 10 rounds (n_rounds <= 10).")
        for idx, quotes in enumerate(v):
            if not isinstance(quotes, list) or not quotes:
                raise ValueError(f"rounds_quotes[{idx}] must be a non-empty list of quotes.")
            if len(quotes) > 32:
                raise ValueError(f"rounds_quotes[{idx}] has {len(quotes)} quotes; max 32 suppliers.")
            for q in quotes:
                if not isinstance(q, (int, float)) or isinstance(q, bool):
                    raise ValueError(f"rounds_quotes[{idx}] contains a non-numeric quote {q!r}.")
                fq = float(q)
                if fq != fq or fq in (float("inf"), float("-inf")):
                    raise ValueError(f"rounds_quotes[{idx}] contains a non-finite quote {q!r}.")
                if not (0.0 < fq <= 1_000_000.0):
                    raise ValueError(
                        f"rounds_quotes[{idx}] quote {fq} is outside the allowed range (0, 1e6]."
                    )
        return v

    @field_validator("round_outcomes")
    @classmethod
    def _bound_round_outcomes(cls, v):
        """Outcomes already observed. Row t is applied before deciding round t.

        Row 0 is ignored: nothing has been offered yet. Allowed labels are
        continue and walk_away. Null means the supplier has not answered.
        """
        if v is None:
            return None
        if len(v) > 10:
            raise ValueError("round_outcomes may contain at most 10 rounds.")
        allowed = {"continue", "walk_away"}
        for idx, row in enumerate(v):
            if row is None:
                continue
            if len(row) > 32:
                raise ValueError(f"round_outcomes[{idx}] has {len(row)} entries; max 32 suppliers.")
            for item in row:
                if item is not None and item not in allowed:
                    raise ValueError(
                        f"round_outcomes[{idx}] has {item!r}; expected continue, walk_away, or null."
                    )
        return v

    @model_validator(mode="after")
    def _target_not_above_ceiling(self):
        if (
            self.target_price is not None
            and self.walk_away_ceiling is not None
            and float(self.target_price) > float(self.walk_away_ceiling)
        ):
            raise ValueError("target_price is above walk_away_ceiling.")
        if self.suppliers:
            seen: dict[str, str] = {}
            for row in self.suppliers:
                name = row.name.strip()
                key = supplier_name_key(name)
                if key in seen:
                    raise ValueError(
                        f"Duplicate supplier name: {name}. Each supplier needs its own name."
                    )
                seen[key] = name
        return self


def _official_reference_bundle(suppliers: Optional[List[dict]], req: MultiAuctionRunRequest) -> dict:
    """ECB and UK tariff, fetched only when a comparison needs them.

    Figures stay reference-only unless ``confirm_market_refs`` is set. A
    confirmed ECB cross rate may be copied onto a supplier who did not
    already supply ``fx_to_base``. UK duty is never copied: a third-country
    rate is not origin-matched to every supplier who left duty blank.
    Freight and fees are never filled in.
    """
    from ..market.official_refs import fx_to_base, reference_bundle

    rows = suppliers or []
    foreign = []
    for row in rows:
        currency = row.get("currency")
        if not currency:
            continue
        if str(currency).strip().upper() == req.base_currency.strip().upper():
            continue
        if row.get("fx_to_base") is None:
            foreign.append(str(currency))
    needs_tariff = bool(req.hs_code) and any(row.get("duty_rate") is None for row in rows)
    if not foreign and not needs_tariff:
        return {
            "fx": {"status": "no data", "value": None, "reason": "not requested", "used_in_solver": False},
            "tariff": {"status": "no data", "value": None, "reason": "not requested", "used_in_solver": False},
            "used_in_solver": False,
            "note": "No foreign currency or commodity code needed a reference fetch.",
        }
    bundle = reference_bundle(foreign, req.base_currency, req.hs_code if needs_tariff else None)
    if not req.confirm_market_refs:
        return bundle
    rates = None
    fx = bundle.get("fx") or {}
    if fx.get("status") == "ok" and isinstance(fx.get("value"), dict):
        rates = fx["value"]
    fx_used = False
    for row in rows:
        currency = row.get("currency")
        if row.get("fx_to_base") is None and currency:
            rate = fx_to_base(rates, str(currency), req.base_currency)
            if rate is not None and str(currency).strip().upper() != req.base_currency.strip().upper():
                row["fx_to_base"] = rate
                fx_used = True
    if fx_used and isinstance(bundle.get("fx"), dict):
        bundle["fx"]["used_in_solver"] = True
    bundle["used_in_solver"] = bool(fx_used)
    bundle["tariff_note"] = (
        "UK third-country duty stays reference only. It is not copied onto "
        "suppliers who left duty blank."
    )
    return bundle



def register_auction_routes(api_v1) -> None:
    @api_v1.post("/multi-auction/run")
    def api_run_multi_auction(body: Optional[MultiAuctionRunRequest] = None):
        """觸發多供應商逆向拍賣。

        不傳報價時，改為執行動態多 Agent 自博弈競價（各家對抗 Agent 依據數學模型
        逐輪即時算出報價）；亦可傳入自定義供應商名單與逐輪報價矩陣。

        Not legacy-gated: the dashboard's "Run auction" widget calls this route,
        the panel is labelled "Simulated suppliers, not live factory quotes" on
        its face, and the discipline contract (test_discipline_invariants)
        mounts only /negotiate, /sessions, and the webhook behind
        TRADEFLOW_LEGACY. Leaving this gated made the default-boot dashboard
        show "Multi-auction rejected (HTTP 404)" on first click.
        """
        try:
            req = body or MultiAuctionRunRequest()
            has_quotes = bool(req.rounds_quotes) or bool(
                req.suppliers and all(item.quote is not None for item in req.suppliers)
            )
            demo_bundle = None
            if req.demo and not has_quotes:
                from src.app.demo_data import self_play_bundle

                demo_bundle = self_play_bundle()
                fill = {}
                if req.target_price is None:
                    fill["target_price"] = demo_bundle["target"]
                if req.walk_away_ceiling is None:
                    fill["walk_away_ceiling"] = demo_bundle["ceiling"]
                if req.moq is None:
                    fill["moq"] = demo_bundle["moq"]
                if fill:
                    req = req.model_copy(update=fill)
            if req.target_price is None or req.walk_away_ceiling is None:
                raise HTTPException(
                    status_code=422,
                    detail="target_price and walk_away_ceiling are required.",
                )
            if req.suppliers is not None and len(req.suppliers) == 0:
                raise HTTPException(
                    status_code=422,
                    detail="suppliers must not be an empty list.",
                )
            if not has_quotes and not (req.demo and demo_bundle):
                raise HTTPException(
                    status_code=422,
                    detail="Provide supplier quotes, or set demo=true for the labelled self-play path.",
                )
            if req.rounds_quotes and req.suppliers:
                bad_rows = [
                    i for i, row in enumerate(req.rounds_quotes) if len(row) != len(req.suppliers)
                ]
                if bad_rows:
                    raise HTTPException(
                        status_code=422,
                        detail="rounds_quotes rows must have one quote per supplier; "
                        f"mismatched rows (0-based): {bad_rows}",
                    )
            supp_list = [s.model_dump() for s in req.suppliers] if req.suppliers else None
            official = _official_reference_bundle(supp_list, req)
            res = simulate_reverse_auction(
                target_price=req.target_price,
                walk_away_ceiling=req.walk_away_ceiling,
                moq=req.moq,
                n_samples=req.n_samples,
                suppliers=supp_list,
                rounds_quotes=req.rounds_quotes,
                n_rounds=req.n_rounds,
                risk_tolerance=req.risk_tolerance,
                base_currency=req.base_currency,
                round_outcomes=req.round_outcomes,
                demo=bool(req.demo and demo_bundle and not has_quotes),
                self_play=demo_bundle if req.demo and demo_bundle and not has_quotes else None,
                prior_mean=req.prior_mean,
                prior_std=req.prior_std,
                expected_markup=req.expected_markup,
                hazard_intercept=req.hazard_intercept,
                hazard_tau=req.hazard_tau,
                hazard_quote_gap=req.hazard_quote_gap,
                hazard_reserve_gap=req.hazard_reserve_gap,
                bayes_obs_noise=req.bayes_obs_noise,
                hazard_credible_alpha=req.hazard_credible_alpha,
            )
            from ..market.trade_data import build_market_context

            # Attached after the solver returns. Not an argument of the simulator.
            res["market_context"] = build_market_context(req.hs_code, req.product_name or "")
            res["market_context_reference_only"] = True
            res["official_references"] = official
            return res
        except HTTPException:
            raise  # [FIX] 上方 422 校驗不得被兜底改寫成 500。
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            # [REGRESSION] 舊版 detail=str(exc)：numpy 的配置錯誤訊息（例如
            # "Unable to allocate 59.6 GiB"）會直接洩漏主機能力給呼叫端。
            logger.exception("multi-auction run failed")
            raise HTTPException(status_code=500, detail="Reverse auction simulation failed.") from None


