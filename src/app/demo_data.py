"""LABELLED DEMO PATH.

These numbers are simulated mandates. Decision routes do not read them
unless the caller sets an explicit demo flag. seed_data/price_anchor.json
is not loaded: its target is not derived from the market block.
"""
from __future__ import annotations

from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
from src.sim.scenarios import SUPPLIER_REPLIES, UMBRELLA, public_payload as simulation_payload
from src.sim.self_play import BUYER_OPENING_NORM, SELF_PLAY_NAMES, SELF_PLAY_SPECS

FALLBACK_TARGET_PRICE = UMBRELLA.target
FALLBACK_MOQ = UMBRELLA.moq
FALLBACK_PRODUCT = UMBRELLA.name
FALLBACK_CEILING = UMBRELLA.ceiling

DEMO_SPEC = {
    "product": FALLBACK_PRODUCT,
    "target_price": FALLBACK_TARGET_PRICE,
    "ceiling_price": FALLBACK_CEILING,
    "moq": FALLBACK_MOQ,
    "n_samples": 20_000,
    "lambda_break": 1.0,
    "prior_reserve_mean": PRIOR_RESERVE_MEAN,
    "prior_reserve_std": PRIOR_RESERVE_STD,
    "label": "simulated",
}


def self_play_bundle() -> dict:
    """Scripted auction opponents. Simulated. Not a factory quote."""
    return {
        "names": [dict(item) for item in SELF_PLAY_NAMES],
        "specs": [dict(item) for item in SELF_PLAY_SPECS],
        "opening": float(BUYER_OPENING_NORM),
        "target": float(UMBRELLA.target),
        "ceiling": float(UMBRELLA.ceiling),
        "moq": int(UMBRELLA.moq),
        "label": "simulated",
    }

# Scripted normalised replies. Defined in src.sim.scenarios and re-exported
# so the CLI walkthrough keeps its import path. Not a supplier transcript.
