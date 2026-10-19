"""Pure desk decision. One supplier, one call to choose_offer. No I/O."""
from __future__ import annotations

from src.engine.decision import OfferChoice, choose_offer
from src.engine.hazard_fit import HazardFit
from src.engine.reserve import ReserveState


def outside_option(ask: float, rivals: list[float], ceiling: float) -> float:
    """The best other confirmed quote, capped by the ceiling and this ask."""
    cap = float(ceiling)
    if rivals:
        cap = min(cap, min(float(item) for item in rivals))
    return min(float(ask), cap)


def decide_supplier(
    *,
    ask: float,
    rivals: list[float],
    ceiling: float,
    state: ReserveState,
    risk_cap: float,
    fit: HazardFit | None = None,
    intercept: float | None = None,
    gap_coef: float | None = None,
) -> OfferChoice:
    """Counter for one supplier. The reference ask on ``state`` is in currency units."""
    outside = outside_option(ask, rivals, ceiling)
    return choose_offer(
        ask=float(ask),
        outside=outside,
        state=state,
        effect=0.0,
        risk_cap=float(risk_cap),
        fit=fit,
        intercept=intercept,
        gap_coef=gap_coef,
    )
