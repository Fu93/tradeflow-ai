"""The documented-constants view the dashboard and the extension read.

Why this module exists
----------------------
``public_payload()`` used to live in ``src/config/heuristics.py``, and it
imported ``src.engine.hazard_fit`` to replace two constants with the fitted
values. That is a layering inversion: the lowest layer (pure configuration
constants) reached up into the engine, and ``hazard_fit`` imports
``heuristics`` at module scope, so the two formed a cycle that only worked
because the import happened inside a function body and was wrapped in
``except Exception: pass``.

The payload is a *view*: it is derived from configuration plus one fitted file,
and it is read by the HTTP layer. It belongs above both, not inside either. It
lives here so the dependency direction is `app → engine → config`, with no edge
pointing back down.

Behaviour is unchanged, including the fallback: if the fit file is missing the
view reports ``fit-missing`` and the documented constants, rather than failing.
"""
from __future__ import annotations

import math
from typing import Any

from src.config.heuristics import (
    BREAKDOWN_TRIP_THRESHOLD,
    CAUTION_RISK_THRESHOLD,
    CVAR_TAIL_FRACTION,
    HAZARD_CREDIBLE_ALPHA,
    HAZARD_INTERCEPT,
    HAZARD_MIN_EVENTS,
    HAZARD_QUOTE_GAP,
    HAZARD_SUPPLIER_TAU,
    LADDER_EDGE_HI,
    LADDER_EDGE_LO,
    LADDER_STEP_FINE,
    LADDER_STEP_MID,
    PRIOR_RESERVE_MEAN,
    PRIOR_RESERVE_STD,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
    STUDENT_T_DF,
    UNIT_SANITY_MULTIPLE,
    WEITZMAN_SEARCH_COST,
)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def public_payload() -> dict[str, Any]:
    """Numbers the dashboard and the extension are allowed to display."""
    intercept = float(HAZARD_INTERCEPT)
    gap = float(HAZARD_QUOTE_GAP)
    hazard_status = "fit-missing"
    hazard_note = "The eBay hazard file is not available in this process."
    try:
        from src.engine.hazard_fit import load_hazard_fit

        fit = load_hazard_fit()
        intercept = float(fit.intercept)
        gap = float(fit.gap_coef)
        hazard_status = "ebay_seller_ended"
        caveat = str((fit.provenance or {}).get("transfer_caveat") or "")
        hazard_note = (
            "Walk-away hazard is transferred from eBay Best Offer, 2012–13, "
            "Business & Industrial, seller-ended, with a small-gap hinge. "
            "It is not a measured Chinese-factory rate. A reserve-gap term is "
            "not in the decision. The risk cap applies to the incremental hazard "
            "of our offer. " + caveat
        ).strip()
    except Exception:
        # A missing or unreadable fit is reported, not raised: this endpoint is a
        # read-only documentation surface and must not 500 because a data file
        # is absent from a fresh checkout.
        pass
    return {
        "documented": True,
        "source": "src/config/heuristics.py",
        "breakdown_trip_threshold": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_default": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_floor": RISK_TOLERANCE_FLOOR,
        "risk_tolerance_max": RISK_TOLERANCE_MAX,
        "cvar_tail_fraction": CVAR_TAIL_FRACTION,
        "caution_risk_threshold": CAUTION_RISK_THRESHOLD,
        "hazard_status": hazard_status,
        "hazard_intercept": intercept,
        "hazard_tau": HAZARD_SUPPLIER_TAU,
        "hazard_quote_gap": gap,
        "hazard_baseline_rate": _sigmoid(intercept),
        "hazard_baseline_low": _sigmoid(intercept - HAZARD_SUPPLIER_TAU),
        "hazard_baseline_high": _sigmoid(intercept + HAZARD_SUPPLIER_TAU),
        "hazard_min_events": HAZARD_MIN_EVENTS,
        "prior_reserve_suggestion": PRIOR_RESERVE_MEAN,
        "prior_reserve_std_suggestion": PRIOR_RESERVE_STD,
        "student_t_df": STUDENT_T_DF,
        "dashboard_expected_markup": None,
        "unit_sanity_multiple": UNIT_SANITY_MULTIPLE,
        "hazard_credible_alpha": HAZARD_CREDIBLE_ALPHA,
        "weitzman_search_cost": WEITZMAN_SEARCH_COST,
        "hazard_note": hazard_note,
        "ladder_edge_lo": LADDER_EDGE_LO,
        "ladder_edge_hi": LADDER_EDGE_HI,
        "ladder_step_fine": LADDER_STEP_FINE,
        "ladder_step_mid": LADDER_STEP_MID,
        "note": "Chosen safety bands and display quantisation, not fitted rates.",
    }
