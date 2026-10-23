"""Walk-away hazard transferred from the eBay Best Offer fit.

The decision uses one gap: how far the offer sits below this supplier's ask.
A reserve-gap term is not added. Acceptance is the Beta distribution of D,
not one minus this hazard.

Both fitted outcomes live in the JSON. The decision uses the seller-ended
fit, because that is the supplier walking. Thread-level "no deal" includes
threads the buyer ended and is reported beside it, not added in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.config.heuristics import HAZARD_FIT_CACHE_MAXSIZE, HAZARD_LOGIT_CLIP, ZERO_GAP_HAZARD_CACHE_MAXSIZE

_FIT_PATH = Path(__file__).resolve().parents[2] / "data" / "hazard_ebay_bi.json"

NOTE = (
    "Walk-away hazard is transferred from eBay Best Offer, 2012–13, "
    "Business & Industrial. It is not a measured Chinese-factory rate. "
    "The fitted curve uses the seller-ended outcome and a small-gap hinge, "
    "because a straight line understated walk-away on small gaps. That hinge "
    "peaks at a zero gap, so it is not a causal increment: the risk cap uses "
    "the gap slope with the hinge held at zero, and a tighter cap can withhold "
    "a push. The published coefficients are unchanged. The fit is the first "
    "buyer offer of a thread and is applied per round to the latest ask. The "
    "thread-level no-deal fit includes threads the buyer ended and is shown "
    "beside it."
)


@dataclass(frozen=True)
class LogisticFit:
    intercept: float
    gap: float
    se_intercept: float
    se_gap: float
    n: int
    outcome: str
    small_gap: float = 0.0
    se_small_gap: float = 0.0
    small_gap_knot: float = 0.0


@dataclass(frozen=True)
class HazardFit:
    seller_ended: LogisticFit
    thread_no_deal: LogisticFit
    provenance: dict

    @property
    def intercept(self) -> float:
        return self.seller_ended.intercept

    @property
    def gap_coef(self) -> float:
        return self.seller_ended.gap

    @property
    def small_gap_coef(self) -> float:
        return self.seller_ended.small_gap

    @property
    def small_gap_knot(self) -> float:
        return self.seller_ended.small_gap_knot


def sigmoid(logit: float) -> float:
    clipped = max(min(float(logit), float(HAZARD_LOGIT_CLIP)), -float(HAZARD_LOGIT_CLIP))
    if clipped >= 0.0:
        z = _exp(-clipped)
        return 1.0 / (1.0 + z)
    z = _exp(clipped)
    return z / (1.0 + z)


def _exp(value: float) -> float:
    import math

    return math.exp(value)


def baseline_rates(intercept: float, tau: float) -> dict[str, float]:
    """Labelled sensitivity of the walk-away rate at a zero gap. No reserve-gap term."""
    centre = float(intercept)
    spread = abs(float(tau))
    return {
        "baseline": sigmoid(centre),
        "low": sigmoid(centre - spread),
        "high": sigmoid(centre + spread),
    }


def quote_gap(ask: float, offer: float) -> float:
    ask = max(float(ask), 0.0)
    if ask == 0.0:
        return 0.0
    return max(float(ask) - float(offer), 0.0) / ask


def hazard_at_gap(
    gap: float,
    effect: float,
    fit: HazardFit | None = None,
    *,
    intercept: float | None = None,
    gap_coef: float | None = None,
    small_gap_coef: float | None = None,
) -> float:
    loaded = fit if fit is not None else load_hazard_fit()
    b0 = loaded.intercept if intercept is None else float(intercept)
    slope = loaded.gap_coef if gap_coef is None else float(gap_coef)
    # A caller who replaces the linear terms is not also given the fitted hinge.
    if small_gap_coef is None and intercept is None and gap_coef is None:
        hinge_coef = loaded.small_gap_coef
        knot = loaded.small_gap_knot
    else:
        hinge_coef = 0.0 if small_gap_coef is None else float(small_gap_coef)
        knot = loaded.small_gap_knot
    hinge = hinge_coef * max(float(knot) - float(gap), 0.0)
    return sigmoid(b0 + slope * float(gap) + hinge + float(effect))


def incremental_hazard(
    ask: float,
    offer: float,
    effect: float = 0.0,
    fit: HazardFit | None = None,
    *,
    intercept: float | None = None,
    gap_coef: float | None = None,
) -> float:
    """Slope increment h(offer) − h(ask). An offer at the ask has increment 0.

    ``hazard_at_gap`` still returns the fitted curve, hinge included. The
    hinge is largest at a zero gap, so subtracting it makes a small discount
    look safer than the ask and the risk cap never binds. The cap therefore
    uses the gap slope with the hinge held at zero.

    [PERF 2026-10-23] ``base`` is the increment at a zero gap with the hinge
    suppressed, which reduces to ``sigmoid(b0 + effect)`` — it does not depend
    on the offer at all. It used to be recomputed through a full
    ``hazard_at_gap`` call (which re-read the fit file) on every evaluation:
    912 times per ``choose_offer``, 4,540 times per five-supplier solve. It is
    now computed directly and memoised on its two float inputs.
    """
    loaded = fit if fit is not None else load_hazard_fit()
    above = hazard_at_gap(
        quote_gap(ask, offer),
        effect,
        loaded,
        intercept=intercept,
        gap_coef=gap_coef,
        small_gap_coef=0.0,
    )
    b0 = loaded.intercept if intercept is None else float(intercept)
    base = _zero_gap_hazard(b0, float(effect))
    return max(above - base, 0.0)


@lru_cache(maxsize=ZERO_GAP_HAZARD_CACHE_MAXSIZE)
def _zero_gap_hazard(b0: float, effect: float) -> float:
    """sigmoid(b0 + effect). The hinge is zero at a zero gap by construction."""
    return sigmoid(b0 + effect)



def _logistic(row: dict) -> LogisticFit:
    return LogisticFit(
        intercept=float(row["intercept"]),
        gap=float(row["gap"]),
        se_intercept=float(row["se_intercept_clustered"]),
        se_gap=float(row["se_gap_clustered"]),
        n=int(row["n"]),
        outcome=str(row.get("outcome") or ""),
        small_gap=float(row.get("small_gap") or 0.0),
        se_small_gap=float(row.get("se_small_gap_clustered") or 0.0),
        small_gap_knot=float(row.get("small_gap_knot") or 0.0),
    )


def load_hazard_fit(path: Path | None = None) -> HazardFit:
    """Read the fitted coefficients. Memoised: the file is 7 KB and immutable.

    It was re-read 157 µs at a time on every hazard evaluation — once per
    supplier per decision, and once per grid point inside the hazard floor
    bisection. The file is a pinned input whose digest is recorded on every
    decision record, so caching it cannot change a decision.
    """
    return _load_hazard_fit(path)


@lru_cache(maxsize=HAZARD_FIT_CACHE_MAXSIZE)
def _load_hazard_fit(path: Path | None = None) -> HazardFit:
    source = _FIT_PATH if path is None else path
    payload = json.loads(source.read_text(encoding="utf-8"))
    return HazardFit(
        seller_ended=_logistic(payload["seller_ended"]),
        thread_no_deal=_logistic(payload["thread_no_deal"]),
        provenance=dict(payload.get("provenance") or {}),
    )


def decision_uses_one_gap(fit: HazardFit) -> bool:
    """The decision uses the quote gap plus the small-gap hinge, not a reserve gap."""
    terms = fit.provenance.get("decision_terms", ["quote_gap"])
    return (
        fit.seller_ended.gap == fit.gap_coef
        and "quote_gap" in terms
        and "reserve_gap" not in terms
    )
