"""Coupled sample-average expectimax for several suppliers at once.

One shared set of scenarios (common random numbers, one cost factor) is
drawn for the round. Each supplier's offer is chosen on a fixed grid. The
outside option inside that choice is the best rival landed quote, which is
recallable: a better rival quote raises the floor on our surplus and cannot
raise what we offer this supplier.

The search is receding-horizon. It picks the next offer, not a five-round
script. Reported Monte Carlo figures are the joint evaluation of that offer
vector: standard errors, P(deal), expected surplus, CVaR, and regret.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from src.config.heuristics import (
    BREAKDOWN_TRIP_THRESHOLD,
    COUPLED_SWEEPS,
    CVAR_TAIL_FRACTION,
    CVAR_WEIGHT,
    DEFAULT_SOLVER_SEED,
    OFFER_GRID_FLOOR,
    OFFER_TIE_BREAK,
    SAMPLE_AXIS_NOISE,
    SHARED_FACTOR_RHO,
    TACTIC_PRICE_TIE,
    TACTIC_SIDELINE_RATIO,
    WEITZMAN_BISECTION_ITERS,
    WEITZMAN_BRACKET_SIGMAS,
    WEITZMAN_SEARCH_COST,
)
from src.engine.hazard import (
    PROVENANCE,
    HazardBelief,
    hazard_logit,
    quote_gap,
    reserve_gap,
    sigmoid,
)
from src.engine.solver import EXPECTIMAX_SAMPLE_CHUNK
from src.sim.archetypes import OPPONENT_TYPE_ROWS


def risk_cap(risk_tolerance: float | None) -> float:
    """The user's risk tolerance is the hazard cap. The default is policy."""
    if risk_tolerance is None:
        return float(BREAKDOWN_TRIP_THRESHOLD)
    return float(risk_tolerance)


def cvar_weight(risk_tolerance: float | None) -> float:
    """Fixed weight. Risk tolerance is the hazard cap, not the CVaR tail."""
    del risk_tolerance
    return float(CVAR_WEIGHT)


def _phi(t: float) -> float:
    return math.exp(-(t * t) / 2) / math.sqrt(2 * math.pi)


def _Phi(t: float) -> float:
    return (1.0 + math.erf(t / math.sqrt(2))) / 2


def _expected_improvement(reservation: float, mean: float, sigma: float) -> float:
    """E[(reservation - X)+] for a normal cost X."""
    if sigma <= float(np.finfo(np.float64).tiny):
        return max(reservation - mean, 0.0)
    t = (reservation - mean) / sigma
    return (reservation - mean) * _Phi(t) + sigma * _phi(t)


def weitzman_reservation(mean: float, sigma: float, cost: float = WEITZMAN_SEARCH_COST) -> float:
    """Buyer reservation price. Lower means this supplier is worth pressing first.

    Solves E[(z - X)+] = cost for a normal belief over the reserve. One more
    exchange costs ``cost`` (a share of the target). The supplier with the
    lowest reservation is the one to push first.
    """
    sigma = max(float(sigma), float(np.finfo(np.float64).tiny))
    # The root is below the mean when cost is smaller than the expected
    # improvement at the mean (sigma / sqrt(2 pi) for a normal).
    span = float(WEITZMAN_BRACKET_SIGMAS) * sigma + abs(float(cost)) + 1.0
    lo = float(mean) - span
    hi = float(mean) + span
    for _ in range(int(WEITZMAN_BISECTION_ITERS)):
        mid = (lo + hi) / 2
        if _expected_improvement(mid, mean, sigma) > float(cost):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def decision_limit(delta_gain: float, h_safe: float, leverage: float, cap: float) -> float:
    """Highest push hazard that still beats the safe offer.

    h* = (ΔG + h_safe · L) / (ΔG + L), then capped by the user's tolerance.
    """
    denom = float(delta_gain) + float(leverage)
    if denom <= 0.0:
        return 0.0
    star = (float(delta_gain) + float(h_safe) * float(leverage)) / denom
    return min(star, float(cap))


def _mean_se(values: np.ndarray) -> tuple[float, float]:
    flat = np.asarray(values, dtype=np.float64).ravel()
    n = int(flat.size)
    mean = float(np.mean(flat)) if n else 0.0
    if n < 2:
        return mean, 0.0
    se = float(np.std(flat, ddof=1) / math.sqrt(n))
    return mean, se


def _cvar_tail(costs: np.ndarray, tail_fraction: float) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(full sample, worst tail)`` using the same cut as the CVaR mean."""
    flat = np.asarray(costs, dtype=np.float64).ravel()
    n = int(flat.size)
    if n == 0:
        empty = np.asarray([], dtype=np.float64)
        return empty, empty
    fraction = min(max(float(tail_fraction), float(np.finfo(np.float64).tiny)), 1.0)
    tail_n = max(1, int(math.ceil(fraction * n)))
    if tail_n >= n:
        return flat, flat
    worst = np.partition(flat, n - tail_n)[n - tail_n :]
    return flat, worst


def _cvar_upper(costs: np.ndarray, tail_fraction: float) -> float:
    """Mean of the worst tail of a cost sample."""
    _, worst = _cvar_tail(costs, tail_fraction)
    if worst.size == 0:
        return 0.0
    return float(np.mean(worst))


def _cvar_se(costs: np.ndarray, tail_fraction: float) -> float:
    """Influence-function standard error of the upper-tail CVaR.

    ``sd(tail) / sqrt(tail_n)`` ignores uncertainty in which observations
    fall in the tail and runs about a third low against the sampling
    standard deviation of this estimator. The asymptotic variance
    ``Var((X - q)_+) / (n τ²)`` uses the same tail cut as ``_cvar_upper``
    and is one pass, so the reported figure stays cheap. It is computed
    for the chosen offer vector only, not on every cell of the search.
    """
    flat, worst = _cvar_tail(costs, tail_fraction)
    n = int(flat.size)
    if n < 2 or worst.size == 0:
        return 0.0
    if worst.size >= n:
        return float(np.std(flat, ddof=1) / math.sqrt(n))
    tau = worst.size / n
    threshold = float(np.min(worst))
    excess = np.maximum(flat - threshold, 0.0)
    var = float(np.var(excess, ddof=1))
    if var <= 0.0:
        return 0.0
    return math.sqrt(var / (n * tau * tau))


def _tail_saturated_at_ceiling(costs: np.ndarray, ceiling: float, tail_fraction: float) -> bool:
    """True when the whole reported tail sits on the walk-away ceiling.

    That CVaR is the ceiling because at least the tail fraction of scenarios
    paid the ceiling. It is not a measured loss inside the tradable range.
    """
    _, worst = _cvar_tail(costs, tail_fraction)
    if worst.size == 0:
        return False
    span = max(abs(float(ceiling)), 1.0)
    return bool(np.max(np.abs(worst - float(ceiling))) <= span * 1e-8)


def sample_joint_scenarios(
    rng: np.random.Generator,
    means: np.ndarray,
    stds: np.ndarray,
    n_samples: int,
    rho: float = SHARED_FACTOR_RHO,
) -> dict[str, np.ndarray]:
    """One joint draw. Supplier i's reserve uses only i's belief and the shared noise.

    ``stds`` is the relative shock used by ``model.sample_opponents`` (a
    posterior standard deviation on a mean near 1 is treated as a fraction
    of that mean). Rival quotes do not enter this draw.
    """
    means = np.asarray(means, dtype=np.float64)
    stds = np.asarray(stds, dtype=np.float64)
    k = int(means.shape[0])
    n = int(n_samples)
    eps = rng.standard_normal((k, n))
    shared = rng.standard_normal(n)
    uniforms = rng.random((k, n))
    rows = np.asarray(OPPONENT_TYPE_ROWS, dtype=np.float64)
    type_ids = rng.integers(0, int(rows.shape[0]), size=(k, n))
    bluff = np.clip(
        rows[type_ids, 2] + SAMPLE_AXIS_NOISE * rng.standard_normal((k, n)),
        0.0,
        1.0,
    )
    idio = math.sqrt(max(0.0, 1.0 - float(rho) * float(rho)))
    common = math.sqrt(max(0.0, float(rho)))
    shock = stds[:, None] * (idio * eps + common * shared[None, :])
    reserves = np.maximum(means[:, None] * (1.0 + shock), np.finfo(np.float64).tiny)
    return {
        "reserves": reserves,
        "uniforms": uniforms,
        "bluff": bluff,
        "type_ids": type_ids,
        "shared_factor": shared,
    }


def _master_grid(floor: float, ceiling: float, n_grid: int) -> np.ndarray:
    n_grid = max(int(n_grid), 1)
    if ceiling <= floor:
        return np.array([float(ceiling)], dtype=np.float64)
    return np.linspace(float(floor), float(ceiling), n_grid)


def _candidates(grid: np.ndarray, hi: float) -> np.ndarray:
    """Fixed grid points at or under ``hi``, plus ``hi`` itself when it is lower."""
    if hi < float(grid[0]):
        return np.array([float(hi)], dtype=np.float64)
    kept = grid[grid <= float(hi) + float(np.finfo(np.float64).eps)]
    if kept.size == 0 or kept[-1] < float(hi) - float(np.finfo(np.float64).eps):
        kept = np.unique(np.concatenate([kept, np.array([float(hi)], dtype=np.float64)]))
    return kept


def _best_other_landed(landed: np.ndarray, ceiling: float, index: int) -> float | None:
    others = [
        float(landed[j])
        for j in range(landed.shape[0])
        if j != index and float(landed[j]) <= float(ceiling) + float(np.finfo(np.float64).eps)
    ]
    if not others:
        return None
    return min(others)


def _score_candidates(
    *,
    reserves: np.ndarray,
    uniforms: np.ndarray,
    bluff: np.ndarray,
    quote: float,
    kappa: float,
    candidates: np.ndarray,
    batna_surplus: float,
    ceiling: float,
    effect: float,
    risk_tolerance: float | None,
) -> dict[str, np.ndarray]:
    """Vectorised expectimax of one supplier's offer grid. Chunked on scenarios."""
    s = int(reserves.shape[0])
    g = int(candidates.shape[0])
    surplus = np.empty((s, g), dtype=np.float64)
    hazard_sum = np.zeros(g, dtype=np.float64)
    deal_sum = np.zeros(g, dtype=np.float64)
    for start in range(0, s, EXPECTIMAX_SAMPLE_CHUNK):
        end = min(start + EXPECTIMAX_SAMPLE_CHUNK, s)
        res = reserves[start:end]
        g_res = reserve_gap(res[:, None], candidates[None, :])
        g_quote = quote_gap(quote, candidates[None, :])
        logit = hazard_logit(
            np,
            g_reserve=g_res,
            g_quote=g_quote,
            bluff=bluff[start:end, None],
            effect=effect,
        )
        hazard = sigmoid(np, logit)
        accept = (candidates[None, :] >= res[:, None]) & (uniforms[start:end, None] >= hazard)
        deal_surplus = float(ceiling) - candidates[None, :] - float(kappa)
        piece = np.where(accept, np.maximum(batna_surplus, deal_surplus), batna_surplus)
        surplus[start:end] = piece
        hazard_sum += np.asarray(hazard, dtype=np.float64).sum(axis=0)
        deal_sum += accept.sum(axis=0)
    mean_surplus, _ = _mean_se_rows(surplus)
    mean_hazard = hazard_sum / s
    p_accept = deal_sum / s
    weight = cvar_weight(risk_tolerance)
    tail = float(CVAR_TAIL_FRACTION)
    cvar = np.empty(g, dtype=np.float64)
    for column in range(g):
        # Loss is forgone surplus: the ceiling value minus the realised surplus.
        cvar[column] = _cvar_upper(float(ceiling) - surplus[:, column], tail)
    score = mean_surplus - weight * cvar - candidates * float(OFFER_TIE_BREAK)
    return {
        "score": score,
        "mean_surplus": mean_surplus,
        "mean_hazard": mean_hazard,
        "p_accept": p_accept,
        "cells": s * g,
    }


def _mean_se_rows(surplus: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = surplus.mean(axis=0)
    if surplus.shape[0] < 2:
        return mean, np.zeros(surplus.shape[1], dtype=np.float64)
    se = surplus.std(axis=0, ddof=1) / math.sqrt(surplus.shape[0])
    return mean, se


def _choose_offer(
    scored: dict[str, np.ndarray],
    candidates: np.ndarray,
    *,
    quote: float,
    reserve_mean: float,
    belief: HazardBelief,
    batna_surplus: float,
    cap: float,
    round_index: float,
) -> dict[str, Any]:
    """Apply the credible-bound cap and the decision-theoretic push limit."""
    g = int(candidates.shape[0])
    upper = np.empty(g, dtype=np.float64)
    decision_hazard = np.empty(g, dtype=np.float64)
    for j, offer in enumerate(candidates):
        pred = belief.predictive(
            quote=quote,
            offer=float(offer),
            reserve=reserve_mean,
            round_index=round_index,
        )
        upper[j] = pred["upper"]
        decision_hazard[j] = pred["mean"]
    # The feasible set is the user's risk tolerance only. A rival-dependent
    # limit would tighten when the outside option improves and could force a
    # higher offer. h* is still computed and returned; the Weitzman stop uses
    # it. It does not raise this supplier's offer.
    under_cap = upper <= float(cap) + float(np.finfo(np.float64).eps)
    if np.any(under_cap):
        safe = int(np.argmin(np.where(under_cap, decision_hazard, np.inf)))
        push = int(np.argmax(np.where(under_cap, scored["score"], -np.inf)))
        blocked = False
        chosen = push
    else:
        safe = int(np.argmin(decision_hazard))
        push = safe
        blocked = True
        chosen = safe
    delta = max(0.0, float(scored["mean_surplus"][push] - scored["mean_surplus"][safe]))
    leverage = max(0.0, float(scored["mean_surplus"][safe] - batna_surplus))
    limit = decision_limit(delta, float(decision_hazard[safe]), leverage, cap)
    return _choice(candidates, scored, chosen, decision_hazard, upper, blocked=blocked, limit=limit)


def _choice(candidates, scored, index, decision_hazard, upper, *, blocked: bool, limit: float) -> dict[str, Any]:
    return {
        "offer": float(candidates[index]),
        "hazard": float(decision_hazard[index]),
        "hazard_upper": float(upper[index]),
        "surplus": float(scored["mean_surplus"][index]),
        "p_accept": float(scored["p_accept"][index]),
        "blocked": blocked,
        "push_limit": float(limit),
    }


def classify_tactics(quotes: np.ndarray, ceiling: float) -> list[str]:
    """Display labels. The push order is reported separately."""
    if quotes.size == 0:
        return []
    cheapest = float(np.min(quotes))
    labels = []
    for quote in quotes:
        if abs(float(quote) - cheapest) < float(TACTIC_PRICE_TIE):
            labels.append("LEAN_IN")
        elif float(quote) > TACTIC_SIDELINE_RATIO * cheapest or float(quote) >= float(ceiling):
            labels.append("SIDELINE")
        else:
            labels.append("CROSS_ANCHOR")
    return labels


def evaluate_vector(
    offers: np.ndarray,
    scenarios: dict[str, np.ndarray],
    kappas: np.ndarray,
    ceiling: float,
    *,
    quotes: np.ndarray,
    beliefs: list[HazardBelief],
    reserve_means: np.ndarray,
    risk_tolerance: float | None,
    round_index: float,
) -> dict[str, Any]:
    """Joint Monte Carlo of a fixed offer vector. Chunked on the scenario axis."""
    reserves = scenarios["reserves"]
    uniforms = scenarios["uniforms"]
    bluff = scenarios["bluff"]
    k, n = reserves.shape
    offers = np.asarray(offers, dtype=np.float64)
    kappas = np.asarray(kappas, dtype=np.float64)
    surplus = np.empty(n, dtype=np.float64)
    deal = np.empty(n, dtype=np.bool_)
    oracle = np.empty(n, dtype=np.float64)
    per_hazard = np.zeros(k, dtype=np.float64)
    for start in range(0, n, EXPECTIMAX_SAMPLE_CHUNK):
        end = min(start + EXPECTIMAX_SAMPLE_CHUNK, n)
        width = end - start
        best = np.zeros(width, dtype=np.float64)
        got = np.zeros(width, dtype=np.bool_)
        oracle_best = np.zeros(width, dtype=np.float64)
        res = reserves[:, start:end]
        for i in range(k):
            g_res = reserve_gap(res[i], offers[i])
            g_quote = quote_gap(float(quotes[i]), offers[i])
            logit = hazard_logit(
                np,
                g_reserve=g_res,
                g_quote=g_quote,
                bluff=bluff[i, start:end],
                effect=beliefs[i].mean_effect(),
            )
            hazard = sigmoid(np, logit)
            per_hazard[i] += float(np.sum(hazard))
            accept = (offers[i] >= res[i]) & (uniforms[i, start:end] >= hazard)
            value = float(ceiling) - offers[i] - float(kappas[i])
            take = accept & (value > best)
            best = np.where(take, value, best)
            got = got | accept
            oracle_price = res[i] + float(kappas[i])
            oracle_value = float(ceiling) - oracle_price
            oracle_ok = oracle_price <= float(ceiling)
            oracle_best = np.where(oracle_ok & (oracle_value > oracle_best), oracle_value, oracle_best)
        surplus[start:end] = best
        deal[start:end] = got
        oracle[start:end] = oracle_best
    per_hazard /= n
    p_deal, p_deal_se = _mean_se(deal.astype(np.float64))
    expected, expected_se = _mean_se(surplus)
    regret = oracle - surplus
    regret_mean, regret_se = _mean_se(regret)
    del risk_tolerance
    tail = float(CVAR_TAIL_FRACTION)
    cost = float(ceiling) - surplus
    cvar = _cvar_upper(cost, tail)
    cvar_se = _cvar_se(cost, tail)
    saturated = _tail_saturated_at_ceiling(cost, float(ceiling), tail)
    hazards = []
    for i in range(k):
        pred = beliefs[i].predictive(
            quote=float(quotes[i]),
            offer=float(offers[i]),
            reserve=float(reserve_means[i]),
            round_index=round_index,
        )
        pred["scenario_mean"] = float(per_hazard[i])
        hazards.append(pred)
    return {
        "p_deal": p_deal,
        "p_deal_se": p_deal_se,
        "expected_surplus": expected,
        "expected_surplus_se": expected_se,
        "cvar_cost": cvar,
        "cvar_se": cvar_se,
        "tail_saturated_at_ceiling": saturated,
        "cvar_alpha": 1.0 - tail,
        "tail_fraction": tail,
        "regret": regret_mean,
        "regret_se": regret_se,
        "n_samples": n,
        "hazards": hazards,
    }


def solve_coupled(
    quotes: np.ndarray,
    means: np.ndarray,
    stds: np.ndarray,
    *,
    ceiling: float,
    kappas: np.ndarray | None = None,
    n_samples: int = 2000,
    n_grid: int = 20,
    risk_tolerance: float | None = None,
    beliefs: list[HazardBelief] | None = None,
    rng: np.random.Generator | None = None,
    round_index: int = 1,
    floor: float = OFFER_GRID_FLOOR,
    rho: float = SHARED_FACTOR_RHO,
) -> dict[str, Any]:
    """Choose one offer per supplier. Prices are normalised to the target."""
    started = time.perf_counter()
    quotes = np.asarray(quotes, dtype=np.float64)
    means = np.asarray(means, dtype=np.float64)
    stds = np.asarray(stds, dtype=np.float64)
    k = int(quotes.shape[0])
    if kappas is None:
        kappas = np.zeros(k, dtype=np.float64)
    else:
        kappas = np.asarray(kappas, dtype=np.float64)
    if beliefs is None:
        beliefs = [HazardBelief() for _ in range(k)]
    if rng is None:
        rng = np.random.default_rng(int(DEFAULT_SOLVER_SEED))
    cap = risk_cap(risk_tolerance)
    landed = quotes + kappas
    scenarios = sample_joint_scenarios(rng, means, stds, int(n_samples), rho=rho)
    grid = _master_grid(float(floor), float(ceiling), int(n_grid))
    offers = np.zeros(k, dtype=np.float64)
    choices: list[dict[str, Any]] = []
    cells = 0
    # One pass. The outside option is the rival quote, which does not move
    # when we write this supplier's offer, so a second pass would rescore
    # the same grid. Cells are the scenario-offer pairs of this pass.
    for i in range(k):
        other = _best_other_landed(landed, ceiling, i)
        if other is None:
            batna = 0.0
            rival_unit_cap = float(ceiling)
        else:
            batna = max(0.0, float(ceiling) - other)
            rival_unit_cap = other - float(kappas[i])
        hi = min(float(quotes[i]), float(ceiling), float(rival_unit_cap))
        hi = max(hi, float(np.finfo(np.float64).tiny))
        candidates = _candidates(grid, hi)
        scored = _score_candidates(
            reserves=scenarios["reserves"][i],
            uniforms=scenarios["uniforms"][i],
            bluff=scenarios["bluff"][i],
            quote=float(quotes[i]),
            kappa=float(kappas[i]),
            candidates=candidates,
            batna_surplus=batna,
            ceiling=float(ceiling),
            effect=beliefs[i].mean_effect(),
            risk_tolerance=risk_tolerance,
        )
        cells += int(scored["cells"])
        choice = _choose_offer(
            scored,
            candidates,
            quote=float(quotes[i]),
            reserve_mean=float(means[i]),
            belief=beliefs[i],
            batna_surplus=batna,
            cap=cap,
            round_index=float(round_index),
        )
        choice["batna_surplus"] = batna
        choice["hi"] = hi
        choice["effective_ceiling"] = min(float(ceiling), other if other is not None else float(ceiling))
        offers[i] = choice["offer"]
        choices.append(choice)
    metrics = evaluate_vector(
        offers,
        scenarios,
        kappas,
        float(ceiling),
        quotes=quotes,
        beliefs=beliefs,
        reserve_means=means,
        risk_tolerance=risk_tolerance,
        round_index=float(round_index),
    )
    reservations = []
    for i in range(k):
        # Same absolute scale as the scenario draw: shock = std * mean.
        sigma = abs(float(stds[i]) * float(means[i]))
        sigma = max(sigma, float(np.finfo(np.float64).tiny))
        # Kappa is a known adder, so the reservation that is compared with
        # a landed quote lives on the landed scale.
        reservations.append(
            weitzman_reservation(float(means[i]) + float(kappas[i]), sigma)
        )
    order = sorted(range(k), key=lambda i: (reservations[i], float(landed[i]), i))
    tactics = classify_tactics(quotes, ceiling)
    best = int(np.argmin(landed))
    other_reservations = [reservations[i] for i in range(k) if i != best]
    stop = False
    stop_reason = "keep negotiating; another round still has a Weitzman value"
    if other_reservations and float(landed[best]) <= min(other_reservations):
        stop = True
        stop_reason = (
            "the best live landed quote is already inside every other "
            "supplier's Weitzman reservation"
        )
    if choices and all(item["blocked"] for item in choices):
        stop = True
        stop_reason = "every continuation exceeds the risk-tolerance cap"
    push_first = order[0] if order else 0
    circuit = bool(choices and choices[push_first]["blocked"])
    elapsed = time.perf_counter() - started
    return {
        "offers": [float(x) for x in offers],
        "hazards": [float(item["hazard"]) for item in choices],
        "hazard_upper": [float(item["hazard_upper"]) for item in choices],
        "blocked": [bool(item["blocked"]) for item in choices],
        "push_limits": [float(item["push_limit"]) for item in choices],
        "effective_ceilings": [float(item["effective_ceiling"]) for item in choices],
        "tactics": tactics,
        "weitzman_index": [float(x) for x in reservations],
        "push_order": order,
        "push_first": int(push_first),
        "stop": stop,
        "stop_reason": stop_reason,
        "circuit_tripped": circuit,
        "p_accept": [float(item["p_accept"]) for item in choices],
        "expected_surplus_by_supplier": [float(item["surplus"]) for item in choices],
        "total_rollouts": int(cells),
        "cells_scored": int(cells),
        "elapsed_seconds": elapsed,
        "mc": metrics,
        "risk_tolerance": cap,
        "cvar_weight": cvar_weight(risk_tolerance),
        "hazard_provenance": dict(PROVENANCE),
        "prior_driven": all(item.prior_driven for item in beliefs),
        "n_samples": int(n_samples),
        "n_grid": int(grid.shape[0]),
        "sweeps": int(COUPLED_SWEEPS),
    }
