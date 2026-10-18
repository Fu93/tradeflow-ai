#!/usr/bin/env python3
"""Compare an offline LLM stand-in with the TradeFlow solver.

The default run does not call the network. ``--live`` still does not spend a
multi-episode AMD run: that is not a cheap call. One real chat probe is
``python scripts/verify_amd_endpoint.py``.

    python scripts/compare_llm_vs_tradeflow.py
    python scripts/compare_llm_vs_tradeflow.py --live

Writes results/llm_vs_tradeflow.json. The file says ran_on_amd false unless a
future change actually called the endpoint. This script does not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.engine.llm_config import real_secret  # noqa: E402
from src.sim.llm_vs_engine import run_comparison  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--samples", type=int, default=256)
    parser.add_argument(
        "--out",
        default=os.path.join(ROOT, "results", "llm_vs_tradeflow.json"),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Record that a live comparison was requested. This script still does not call AMD.",
    )
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env"), override=False)
    except Exception:
        pass

    payload = run_comparison(
        n_episodes=args.episodes,
        seed=args.seed,
        solver_samples=args.samples,
    )
    payload["live_requested"] = bool(args.live)
    payload["live_endpoint_used"] = False
    payload["ran_on_amd"] = False
    if args.live and real_secret(os.getenv("VLLM_API_KEY")):
        payload["live_note"] = (
            "A full comparison is not a cheap call, so this run stayed on the offline stand-in. "
            "One real chat is python scripts/verify_amd_endpoint.py."
        )
    elif args.live:
        payload["live_note"] = "VLLM_API_KEY is unset or a placeholder, so no AMD call was made."
    else:
        payload["live_note"] = "Offline stand-in. No AMD call was made."

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    buyer = payload["offline_llm"]
    engine = payload["tradeflow"]
    print(f"episodes: {payload['episode_count']} seed: {payload['seed']}")
    print(f"ran_on_amd: {payload['ran_on_amd']} buyer: {payload['llm_buyer']}")
    print(
        "tradeflow "
        f"violations {engine['budget_violation_rate_label']} "
        f"deals {engine['deal_rate_label']} "
        f"savings {engine['average_savings_label']}"
    )
    print(
        "offline_llm "
        f"violations {buyer['budget_violation_rate_label']} "
        f"deals {buyer['deal_rate_label']} "
        f"savings {buyer['average_savings_label']}"
    )
    print(payload["disclaimer"])
    print(f"wrote: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
