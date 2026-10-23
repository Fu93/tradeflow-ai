"""Turn a one-step counter into the fields a round response already shows.

The schedule is that single offer. A multi-round concession curve is not
attached, because the scored policy does not choose one.
"""
from __future__ import annotations

from src.legacy.model import BREAKDOWN_TRIP_THRESHOLD
from src.legacy.one_step import one_step_counter


def build_proposal(spec: dict, observed_offer: float | None = None):
    """``observed_offer`` is a normalised quote (1 = target) when provided.

    The posterior in ``spec`` is used as-is. This function does not update
    the belief again.
    """
    target = spec.get("target_price")
    ceiling = spec.get("walk_away_ceiling")
    if target is None or ceiling is None:
        raise ValueError("target_price and walk_away_ceiling are required")
    quote_price = spec.get("quote_price")
    if quote_price is None and observed_offer is not None:
        quote_price = float(observed_offer) * float(target)
    if quote_price is None:
        raise ValueError("a supplier quote is required")
    mean = spec.get("prior_reserve_mean")
    std = spec.get("prior_reserve_std")
    if mean is None or std is None:
        raise ValueError("a reserve belief is required")
    decision = one_step_counter(
        quote=float(quote_price),
        target=float(target),
        ceiling=float(ceiling),
        mean=float(mean),
        std=float(std),
        n_samples=spec.get("n_samples"),
        risk_tolerance=spec.get("risk_tolerance"),
        search_cost=spec.get("search_cost"),
    )
    offer = decision["opening_offer_price"]
    risk = "HUMAN_REVIEW" if decision["blocked"] or decision["breakdown_prob"] >= BREAKDOWN_TRIP_THRESHOLD else "APPROVE"
    if offer is None:
        text = (
            f"No counter. The walk-away hazard of every feasible offer exceeds "
            f"the cap (target ¥{float(target):.2f}, ceiling ¥{float(ceiling):.2f})."
        )
        prices = []
    else:
        text = (
            f"Recommended counter ¥{offer:.2f} (target ¥{float(target):.2f}, "
            f"walk-away ceiling ¥{float(ceiling):.2f}). "
            f"This is the next offer only. Expected surplus ¥{decision['expected_margin_price']:.2f}/unit, "
            f"walk-away hazard {decision['breakdown_prob']:.1%}. Decision: {risk}."
        )
        prices = [round(offer, 2)]
    return {
        "opening_offer_price": None if offer is None else round(offer, 2),
        "round_by_round_prices": prices,
        "ceiling_ref_multiple": round(float(ceiling) / float(target), 3),
        "concession_curve": [],
        "expected_margin_price": round(decision["expected_margin_price"], 2),
        "breakdown_prob": round(float(decision["breakdown_prob"]), 4),
        "posterior_reserve_mean": round(float(mean), 4),
        "posterior_reserve_std": round(float(std), 4),
        "risk_flag": risk,
        "advice_text": text,
        "solver": decision["solver"],
    }
