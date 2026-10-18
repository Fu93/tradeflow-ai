"""Suppliers whose behaviour is not the solver's model.

Faratin time-dependent concession (the family NegMAS time-dependent
agents use) plus an exogenous walk-away hazard with different coefficients
from ``src.config.heuristics``. Simulation only. These numbers are not
factory data and are not the solver's prior.
"""
from __future__ import annotations

import math

# Rationale: time-dependent concession exponents. Below 1 concedes late
# (Boulware), 1 is linear, above 1 concedes early. Faratin et al. tactics,
# not a fit to this repository.
FARATIN_BOULWARE = 0.35
FARATIN_LINEAR = 1.0
FARATIN_CONCEDER = 4.0

# Rationale: exogenous walk-away logit. The intercept, slope, and time
# coefficient differ from the solver's hazard prior on purpose, so a
# calibration check is against a misspecified generator.
GROUND_TRUTH_INTERCEPT = -1.6
GROUND_TRUTH_SLOPE = 2.1
GROUND_TRUTH_TIME = 0.9

# Rationale: spread of simulated reserves around the scenario centre,
# as a fraction of the buyer's target. A simulator knob.
RESERVE_SPREAD = 0.08

# Rationale: opening ask as a fraction above the true reserve. Simulator.
OPENING_MARKUP = 0.22

# Rationale: correlation of reserves inside one simulated market.
MARKET_RHO = 0.5


def faratin_price(round_index: int, horizon: int, floor: float, opening: float, beta: float) -> float:
    """Seller price at this round. Starts at ``opening`` and moves toward ``floor``."""
    horizon = max(int(horizon), 1)
    tau = min(max(int(round_index), 0), horizon) / horizon
    if tau <= 0.0:
        return float(opening)
    concession = tau ** (1.0 / float(beta))
    price = float(opening) - (float(opening) - float(floor)) * concession
    return max(float(floor), price)


def ground_truth_hazard(aggressiveness: float, time_fraction: float) -> float:
    """Walk-away probability used only by the simulator. Not the solver prior."""
    logit = (
        GROUND_TRUTH_INTERCEPT
        + GROUND_TRUTH_SLOPE * float(aggressiveness)
        + GROUND_TRUTH_TIME * float(time_fraction)
    )
    if logit > 20:
        return 1.0
    if logit < -20:
        return 0.0
    return 1.0 / (1.0 + math.exp(-logit))
