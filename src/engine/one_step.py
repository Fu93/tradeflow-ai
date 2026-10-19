"""One supplier, one next offer.

The price is the analytic expected surplus of the one-step accept-or-reject
model on this quote, this belief, and the session ceiling. It is not a
five-round concession script, and the Monte Carlo sample does not choose it.
"""
from __future__ import annotations

import numpy as np

from src.config.heuristics import DEFAULT_SOLVER_SEED, DESK_N_GRID, ROUND_N_SAMPLES, WEITZMAN_SEARCH_COST
from src.engine.coupled import solve_coupled


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
    )
    raw = float(solved["offers"][0]) * float(target)
    blocked = bool(solved["blocked"][0])
    offer = None if blocked else min(raw, float(ceiling), float(quote))
    return {
        "opening_offer_price": offer,
        "opening_offer_norm": None if offer is None else offer / float(target),
        "concession_curve": [],
        "concession_step": 0.0,
        "expected_margin_price": float(solved["expected_surplus_by_supplier"][0]) * float(target),
        "expected_margin_norm": float(solved["expected_surplus_by_supplier"][0]),
        "breakdown_prob": float(solved["hazards"][0]),
        "breakdown_definition": "walk-away hazard of this one-step offer",
        "blocked": blocked,
        "solver": "analytic expected surplus of the one-step accept-or-reject model",
        "counter_relation": solved["counter_relation"][0],
        "n_samples": samples,
        "posterior_reserve_mean": float(mean),
        "posterior_reserve_std": float(std),
        "p_accept": float(solved["p_accept"][0]),
    }
