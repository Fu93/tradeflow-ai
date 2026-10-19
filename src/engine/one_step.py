"""One supplier, one next offer.

The default counter is this supplier's ask, capped by the ceiling. There is
no rival on this endpoint. The push-lower counter is the one-dimensional
maximum of expected landed saving minus incremental walk risk. A mesh step
is not an input, and the Monte Carlo sample does not choose the price.
"""
from __future__ import annotations

import numpy as np

from src.config.heuristics import DEFAULT_SOLVER_SEED, DESK_N_GRID, ROUND_N_SAMPLES, WEITZMAN_SEARCH_COST
from src.desk.headline import AUDIT_LINE
from src.engine.coupled import solve_coupled
from src.engine.hazard import HazardBelief


def one_step_counter(
    *,
    quote: float,
    target: float,
    ceiling: float,
    mean: float,
    std: float,
    n_samples: int | None = None,
    n_grid: int | None = None,
    risk_tolerance: float | None = None,
    seed: int = DEFAULT_SOLVER_SEED,
    search_cost: float | None = None,
    round_index: float = 1,
    lambda_break: float = 1.0,
    belief: HazardBelief | None = None,
    credible_alpha: float | None = None,
) -> dict:
    if target <= 0 or ceiling <= 0 or quote <= 0:
        raise ValueError("quote, target, and ceiling must be positive")
    samples = int(ROUND_N_SAMPLES if n_samples is None else n_samples)
    grid = int(DESK_N_GRID if n_grid is None else n_grid)
    solved = solve_coupled(
        np.asarray([quote / target], dtype=np.float64),
        np.asarray([mean], dtype=np.float64),
        np.asarray([std], dtype=np.float64),
        ceiling=ceiling / target,
        n_samples=samples,
        n_grid=grid,
        risk_tolerance=risk_tolerance,
        rng=np.random.default_rng(int(seed)),
        search_cost=float(WEITZMAN_SEARCH_COST if search_cost is None else search_cost),
        beliefs=[belief if belief is not None else HazardBelief()],
        round_index=float(round_index),
        lambda_break=float(lambda_break),
        credible_alpha=credible_alpha,
    )
    blocked = bool(solved["blocked"][0])

    def priced(raw: float) -> float | None:
        if blocked:
            return None
        return min(float(raw) * float(target), float(ceiling), float(quote))

    offer = priced(float(solved["offers"][0]))
    return {
        "opening_offer_price": offer,
        "opening_offer_norm": None if offer is None else offer / float(target),
        "push_offer_price": priced(float(solved["push_offers"][0])),
        "push_half_price": priced(float(solved["push_half"][0])),
        "push_double_price": priced(float(solved["push_double"][0])),
        "concession_curve": [],
        "concession_step": 0.0,
        "expected_margin_price": float(solved["expected_surplus_by_supplier"][0]) * float(target),
        "expected_margin_norm": float(solved["expected_surplus_by_supplier"][0]),
        "breakdown_prob": float(solved["hazards"][0]),
        "breakdown_definition": "incremental walk hazard of the default counter, h(offer) minus h(ask)",
        "blocked": blocked,
        "solver": "one-dimensional expected landed saving minus incremental walk risk",
        "counter_relation": solved["counter_relation"][0],
        "n_samples": samples,
        "posterior_reserve_mean": float(mean),
        "posterior_reserve_std": float(std),
        "p_accept": float(solved["p_accept"][0]),
        "reserve_source": solved["prior_source"][0],
        "prior_note": solved["prior_note"][0],
        "heuristic": solved["heuristic"],
        "markup_applied": False,
        "audit": AUDIT_LINE,
    }
