"""Pure desk decision. One supplier, one call to choose_offer. No I/O."""
from __future__ import annotations

from src.config.heuristics import MINOR_SCALE
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


# The figure a person chose to put in front of one supplier. "default" is the
# best price already on the table (this ask, or the best rival's). "push" is the
# solver's push-lower counter, which maximises expected surplus under the risk
# cap. Only a human event on the chain sets it; the recompute, the record check
# and the chain check all read it through these two functions.
OFFER_MODES = ("default", "push")


def offer_modes(entries: list[dict]) -> dict[str, str]:
    """Latest human-chosen offer mode per supplier, from the audit chain."""
    modes: dict[str, str] = {}
    for entry in entries:
        if entry.get("type") != "offer_mode":
            continue
        payload = entry.get("payload") or {}
        if payload.get("confirmed_by") != "human":
            continue
        supplier_id = str(payload.get("supplier_id") or "")
        mode = str(payload.get("mode") or "")
        if supplier_id and mode in OFFER_MODES:
            modes[supplier_id] = mode
    return modes


def issued_offer(choice: OfferChoice | None, ceiling: float, ask: float, mode: str) -> tuple[float | None, str]:
    """The number the supplier reads, and how it relates to the ask.

    A push that would lower expected value is withheld by the solver; then the
    default figure is issued and the relation says so.
    """
    from src.desk.money import quantise_down, quantise_offer, quantise_up

    if choice is None or choice.blocked or str(choice.counter_relation) == "withheld":
        return None, "withheld"
    floor = max(1.0 / float(MINOR_SCALE), quantise_up(choice.minimum_offer))
    if floor > quantise_down(min(float(ceiling), float(ask), float(choice.default_counter))):
        return None, "withheld"
    price_cap = min(float(ceiling), float(choice.default_counter))
    if mode == "push" and not choice.push_withheld:
        return max(floor, quantise_offer(float(choice.push_counter), price_cap, float(ask))), "push_lower"
    amount = max(floor, quantise_offer(float(choice.default_counter), price_cap, float(ask)))
    return amount, str(choice.counter_relation)
