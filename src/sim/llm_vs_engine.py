"""Offline pure-LLM stand-in versus the TradeFlow solver.

The stand-in accepts the supplier's opening. It is not a model call, not
Gemma, and not the Qwen log in results/real_llm_benchmark_100.json. Supplier
openings are parameterised from the archetype rows and the sandbox mandates.
They are not factory quotes.

Rationale: the accept-the-opening policy is the comparison's unconstrained
buyer. It can pay above the walk-away ceiling. The solver bid is capped at
that ceiling. Both rules are in the result file.
"""
from __future__ import annotations

from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
from src.engine.solver import bayes_update, recommend, solve
from src.sim.archetypes import OPPONENT_TYPE_ROWS
from src.sim.scenarios import MANDATES

DISCLAIMER = (
    "Simulation. The LLM buyer is an offline stand-in named offline_accept_opening. "
    "It is not an AMD call, not Gemma-4-31B-IT, and not the Qwen log in "
    "results/real_llm_benchmark_100.json."
)


def _pct(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def _savings_label(values: list[float]) -> str:
    if not values:
        return "n/a"
    mean = sum(values) / len(values)
    return f"{mean * 100:.2f}%"


def _supplier(index: int) -> dict:
    mandate = MANDATES[index % len(MANDATES)]
    reserve_multiple, _urgency, bluff, _concession = OPPONENT_TYPE_ROWS[index % len(OPPONENT_TYPE_ROWS)]
    reserve = mandate.target * reserve_multiple
    opening = reserve * (1.0 + bluff)
    return {
        "mandate": mandate.key,
        "target": mandate.target,
        "ceiling": mandate.ceiling,
        "reserve": reserve,
        "opening": opening,
    }


def _solver_bid(opening: float, target: float, ceiling: float, samples: int, seed: int) -> float:
    import numpy as np

    observed = opening / target
    post_mean, post_std = bayes_update(PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD, observed)
    solved = solve(
        n_opp_samples=samples,
        reserve_mean=post_mean,
        reserve_std=post_std,
        rng=np.random.default_rng(seed),
    )
    recommended = recommend(solved, target_price=target)
    bid = float(recommended["opening_offer_price"])
    if bid > ceiling:
        return ceiling
    return bid


def _summarize(rows: list[dict], bid_key: str) -> dict:
    deals = 0
    violations = 0
    savings: list[float] = []
    for row in rows:
        bid = row[bid_key]
        opening = row["opening"]
        if bid > row["ceiling"]:
            violations += 1
        if bid >= row["reserve"]:
            deals += 1
            if opening > 0:
                savings.append((opening - bid) / opening)
    count = len(rows)
    violation_rate = violations / count if count else 0.0
    deal_rate = deals / count if count else 0.0
    average = (sum(savings) / len(savings)) if savings else None
    return {
        "episodes": count,
        "deals": deals,
        "budget_violations": violations,
        "budget_violation_rate": violation_rate,
        "budget_violation_rate_label": _pct(violation_rate),
        "deal_rate": deal_rate,
        "deal_rate_label": _pct(deal_rate),
        "average_savings": average,
        "average_savings_label": _savings_label(savings),
        "average_savings_over": "deals_only",
    }


def run_comparison(n_episodes: int = 24, seed: int = 42, solver_samples: int = 256) -> dict:
    if n_episodes < 1:
        raise ValueError("n_episodes must be at least 1")
    rows = []
    for index in range(n_episodes):
        supplier = _supplier(index)
        solver_bid = _solver_bid(
            supplier["opening"],
            supplier["target"],
            supplier["ceiling"],
            solver_samples,
            seed + index,
        )
        rows.append(
            {
                "mandate": supplier["mandate"],
                "opening": supplier["opening"],
                "reserve": supplier["reserve"],
                "ceiling": supplier["ceiling"],
                "tradeflow_bid": solver_bid,
                "offline_llm_bid": supplier["opening"],
            }
        )
    return {
        "simulated": True,
        "ran_on_amd": False,
        "live_endpoint_used": False,
        "not_a_model_call": True,
        "llm_buyer": "offline_accept_opening",
        "episode_count": n_episodes,
        "seed": seed,
        "solver_samples": solver_samples,
        "definition": {
            "deal": "one shot; the bid is greater than or equal to the supplier reserve",
            "budget_violation": "the bid is greater than the walk-away ceiling",
            "average_savings": "mean of (opening - bid) / opening over deals only; n/a when there are no deals",
            "supplier": "reserve is mandate target times the archetype reserve multiple; opening is reserve times one plus the bluff probability",
            "tradeflow": "solver recommended opening, capped at the mandate ceiling",
            "offline_llm": "accepts the supplier opening and does not read the ceiling",
        },
        "tradeflow": _summarize(rows, "tradeflow_bid"),
        "offline_llm": _summarize(rows, "offline_llm_bid"),
        "disclaimer": DISCLAIMER,
    }
