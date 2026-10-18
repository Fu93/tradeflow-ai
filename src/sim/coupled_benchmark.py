"""Labelled simulation of coupled offers against independent sessions.

Suppliers follow a Faratin time-dependent concession and a walk-away
hazard whose coefficients are not the solver prior. The normalised buyer
surplus and its bootstrap interval are simulation statistics. They are
not factory outcomes and they are not a claim about live negotiations.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD, YOUR_CEILING
from src.engine.coupled import solve_coupled
from src.engine.hazard import HazardBelief
from src.engine.solver import bayes_update
from src.sim.misspecified import (
    FARATIN_BOULWARE,
    FARATIN_CONCEDER,
    FARATIN_LINEAR,
    MARKET_RHO,
    OPENING_MARKUP,
    RESERVE_SPREAD,
    faratin_price,
    ground_truth_hazard,
)

_BETAS = (FARATIN_BOULWARE, FARATIN_LINEAR, FARATIN_CONCEDER, FARATIN_BOULWARE, FARATIN_LINEAR)


def _reserves(rng: np.random.Generator, k: int) -> np.ndarray:
    shared = float(rng.standard_normal())
    eps = rng.standard_normal(k)
    rho = float(MARKET_RHO)
    idio = math.sqrt(max(0.0, 1.0 - rho * rho))
    common = math.sqrt(max(0.0, rho))
    shock = float(RESERVE_SPREAD) * (idio * eps + common * shared)
    centre = float(PRIOR_RESERVE_MEAN)
    return np.maximum(centre * (1.0 + shock), np.finfo(np.float64).tiny)


def _nbs(paid: float, oracle: float, ceiling: float) -> float:
    span = float(ceiling) - float(oracle)
    if span <= float(np.finfo(np.float64).tiny):
        return 0.0
    return (float(ceiling) - float(paid)) / span


def _play(
    policy: str,
    reserves: np.ndarray,
    walk_u: np.ndarray,
    *,
    horizon: int,
    n_samples: int,
    n_grid: int,
    seed: int,
) -> float:
    """Price paid, in units of the buyer's target. No deal pays the ceiling."""
    k = int(reserves.shape[0])
    ceiling = float(YOUR_CEILING)
    openings = reserves * (1.0 + float(OPENING_MARKUP))
    betas = [_BETAS[i % len(_BETAS)] for i in range(k)]
    live = np.ones(k, dtype=bool)
    means = np.full(k, PRIOR_RESERVE_MEAN, dtype=np.float64)
    stds = np.full(k, PRIOR_RESERVE_STD, dtype=np.float64)
    beliefs = [HazardBelief() for _ in range(k)]
    paid = ceiling
    for t in range(1, int(horizon) + 1):
        asks = np.full(k, np.inf, dtype=np.float64)
        for i in range(k):
            if not live[i]:
                continue
            asks[i] = faratin_price(t, horizon, float(reserves[i]), float(openings[i]), betas[i])
            mean, std = bayes_update(float(means[i]), float(stds[i]), float(asks[i]))
            means[i] = mean
            stds[i] = max(std, float(np.finfo(np.float64).tiny))
        active = [i for i in range(k) if live[i] and np.isfinite(asks[i])]
        if not active:
            break
        offers = np.full(k, ceiling, dtype=np.float64)
        stop = False
        if policy == "accept_best":
            offers[active] = asks[active]
        elif policy == "independent":
            for i in active:
                rng = np.random.default_rng(seed + t * 1009 + i)
                one = solve_coupled(
                    np.array([asks[i]]),
                    np.array([means[i]]),
                    np.array([stds[i]]),
                    ceiling=ceiling,
                    n_samples=n_samples,
                    n_grid=n_grid,
                    beliefs=[beliefs[i]],
                    rng=rng,
                    round_index=t,
                )
                offers[i] = one["offers"][0]
        else:
            rng = np.random.default_rng(seed + t * 1009)
            coupled = solve_coupled(
                asks[active],
                means[active],
                stds[active],
                ceiling=ceiling,
                n_samples=n_samples,
                n_grid=n_grid,
                beliefs=[beliefs[i] for i in active],
                rng=rng,
                round_index=t,
            )
            for local, i in enumerate(active):
                offers[i] = coupled["offers"][local]
            stop = bool(coupled["stop"])
        accepted = []
        time_fraction = t / float(horizon)
        for i in active:
            ask = float(asks[i])
            offer = float(min(offers[i], ask, ceiling))
            gap = max(ask - offer, 0.0) / max(ask, float(np.finfo(np.float64).tiny))
            if policy == "accept_best":
                gap = 0.0
            hazard = ground_truth_hazard(gap, time_fraction)
            if float(walk_u[t - 1, i]) < hazard:
                live[i] = False
                continue
            if offer + float(np.finfo(np.float64).eps) >= float(reserves[i]):
                accepted.append(offer)
        if accepted:
            paid = float(min(accepted))
            break
        if policy == "accept_best":
            continue
        if stop or t == int(horizon):
            surviving = [float(asks[i]) for i in range(k) if live[i] and float(asks[i]) <= ceiling]
            if surviving:
                paid = float(min(surviving))
            break
    return paid


def _ci(values: np.ndarray, rng: np.random.Generator, n_boot: int) -> tuple[float, float, float]:
    flat = np.asarray(values, dtype=np.float64)
    n = int(flat.size)
    mean = float(np.mean(flat)) if n else 0.0
    if n < 2:
        return mean, mean, mean
    draws = rng.integers(0, n, size=(int(n_boot), n))
    boots = flat[draws].mean(axis=1)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    return mean, float(lo), float(hi)


def run_markets(
    n_markets: int = 12,
    *,
    horizon: int = 3,
    k: int = 4,
    n_samples: int = 64,
    n_grid: int = 6,
    seed: int = 20261018,
    n_boot: int = 400,
) -> dict[str, Any]:
    """Normalised buyer surplus for three buyer policies on the same markets."""
    rng = np.random.default_rng(int(seed))
    ceiling = float(YOUR_CEILING)
    coupled_s = np.empty(int(n_markets), dtype=np.float64)
    indep_s = np.empty(int(n_markets), dtype=np.float64)
    accept_s = np.empty(int(n_markets), dtype=np.float64)
    for m in range(int(n_markets)):
        market_seed = int(rng.integers(0, 2**31 - 1))
        market = np.random.default_rng(market_seed)
        reserves = _reserves(market, int(k))
        walk_u = market.random((int(horizon), int(k)))
        oracle = float(np.min(reserves))
        if oracle > ceiling:
            oracle = ceiling
        for store, policy in (
            (coupled_s, "coupled"),
            (indep_s, "independent"),
            (accept_s, "accept_best"),
        ):
            paid = _play(
                policy,
                reserves,
                walk_u,
                horizon=int(horizon),
                n_samples=int(n_samples),
                n_grid=int(n_grid),
                seed=market_seed,
            )
            store[m] = _nbs(paid, oracle, ceiling)
    boot = np.random.default_rng(int(seed) + 1)
    c_mean, c_lo, c_hi = _ci(coupled_s, boot, n_boot)
    i_mean, i_lo, i_hi = _ci(indep_s, boot, n_boot)
    a_mean, a_lo, a_hi = _ci(accept_s, boot, n_boot)
    d_mean, d_lo, d_hi = _ci(coupled_s - indep_s, boot, n_boot)
    return {
        "simulated": True,
        "misspecified": True,
        "label": (
            "simulation; suppliers use Faratin time-dependent concession and a "
            "walk-away hazard that is not the solver prior"
        ),
        "n_markets": int(n_markets),
        "k_suppliers": int(k),
        "horizon": int(horizon),
        "n_samples": int(n_samples),
        "coupled_nbs_mean": c_mean,
        "coupled_nbs_ci95": [c_lo, c_hi],
        "independent_nbs_mean": i_mean,
        "independent_nbs_ci95": [i_lo, i_hi],
        "accept_best_nbs_mean": a_mean,
        "accept_best_nbs_ci95": [a_lo, a_hi],
        "coupled_minus_independent_mean": d_mean,
        "coupled_minus_independent_ci95": [d_lo, d_hi],
        "normalised_buyer_surplus": "(ceiling - paid) / (ceiling - oracle reserve)",
        "no_deal_paid": "walk-away ceiling",
        "reserve_prior_centre": float(PRIOR_RESERVE_MEAN),
        "suppliers_share_solver_reserve_centre": True,
        "suppliers_ignore_rival_competition": True,
        "honesty": (
            "Simulated reserves are centred on the solver's reserve-prior "
            "centre (PRIOR_RESERVE_MEAN). Suppliers concede on a clock and "
            "do not react to rival quotes, so the gap is not evidence about "
            "live competitive reactions. An 8-market or 24-market draw is "
            "too small to show that the coupled surplus differs from the "
            "independent surplus."
        ),
    }
