#!/usr/bin/env python3
"""Time the negotiation solver on this machine's array backend.

On an AMD GPU box, with ROCm and CuPy installed:

    TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py

On a CPU machine the same command still runs. It records wall-clock time for
NumPy and, separately, a theoretical roofline lower-bound estimate, not measured.
The formula and the datasheet citation live in results/mi300x_roofline.json.

``ran_on_amd_gpu`` is true only when CuPy executed and the device name looks
like an AMD GPU. This script does not invent that flag.

Writes results/amd_gpu_benchmark.json. Exit 0 when the solve finishes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.engine.backend import describe_solver_backend, resolve_use_gpu  # noqa: E402
from src.engine import model  # noqa: E402
from src.engine.roofline import (  # noqa: E402
    formula as roofline_formula,
    label as roofline_label,
    load as load_roofline,
    rounded_lower_bound_seconds,
)
from src.app.demo_data import self_play_bundle  # noqa: E402
from src.engine.model import bind_archetypes  # noqa: E402
from src.engine.solver import solve  # noqa: E402
from src.sim.archetypes import OPPONENT_TYPE_NAMES, OPPONENT_TYPE_ROWS  # noqa: E402

bind_archetypes(OPPONENT_TYPE_ROWS, OPPONENT_TYPE_NAMES)
_DEMO = self_play_bundle()
_VALUE_CEILING = _DEMO["ceiling"] / _DEMO["target"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _measure(samples: int, use_gpu: bool) -> dict:
    opens, scheds = model.strategy_grid(ceiling=_VALUE_CEILING)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        solve(
            n_opp_samples=samples,
            opens=opens,
            scheds=scheds,
            use_gpu=use_gpu,
            value_ceiling=_VALUE_CEILING,
        )
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    working_set_mb = peak / (1024 * 1024)
    # tracemalloc sees host allocations. On a GPU run this is not device memory.
    host_only = not use_gpu
    reported_mb = round(working_set_mb, 2) if host_only else None
    transfer_s = None
    if reported_mb:
        # Bound the figure we store, so a reader who only has host_working_set_mb
        # recomputes the same lower bound.
        transfer_s = rounded_lower_bound_seconds(reported_mb)
    return {
        "samples": int(samples),
        "grid": int(opens.shape[0] * scheds.shape[0]),
        "elapsed_seconds": round(elapsed, 6),
        "elapsed_kind": "wall_clock",
        "host_working_set_mb": reported_mb,
        "host_working_set_note": (
            "tracemalloc peak during solve(); host allocations only."
            if host_only
            else "Not reported: tracemalloc does not measure device memory."
        ),
        "mi300x_hbm3_transfer_lower_bound_seconds": (
            round(transfer_s, 9) if transfer_s is not None else None
        ),
        "mi300x_bound_is_estimate": True,
        "mi300x_bound_is_measurement": False,
        "mi300x_bound_label": roofline_label(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=2000,
        help="Opponent samples for the single measured solve (default 2000).",
    )
    parser.add_argument(
        "--evidence",
        default=os.path.join(ROOT, "results", "amd_gpu_benchmark.json"),
    )
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="Ignore TRADEFLOW_SOLVER_BACKEND and time NumPy.",
    )
    args = parser.parse_args(argv)
    if args.samples < 1 or args.samples > 50_000:
        print("samples must be in 1..50000", file=sys.stderr)
        return 2

    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env"), override=False)
    except Exception:
        pass

    info = describe_solver_backend()
    use_gpu = False if args.force_cpu else resolve_use_gpu(None)
    # describe_solver_backend() reports the configured request. Re-read actual
    # after a forced CPU run so the file does not claim a GPU that we did not use.
    if args.force_cpu:
        info = dict(info)
        info["use_gpu"] = False
        info["actual"] = "numpy"
        info["ran_on_amd_gpu"] = False
        info["note"] = "Forced NumPy via --force-cpu."

    row = _measure(args.samples, use_gpu=use_gpu and info.get("actual") == "cupy")
    spec = load_roofline()
    report = {
        "generated_at_utc": _utc_now(),
        "ran_on_amd_gpu": bool(info.get("ran_on_amd_gpu")),
        "backend": info,
        "measurement": row,
        "roofline_constant": {
            "name": "AMD Instinct MI300X HBM3 peak bandwidth",
            "label": spec["label"],
            "formula": spec["formula"],
            "tb_per_s": spec["peak_bandwidth_tb_s"],
            "bytes_per_s": spec["peak_bandwidth_bytes_per_s"],
            "hbm3_capacity_gb": spec["hbm3_capacity_gb"],
            "datasheet_url": spec["datasheet_url"],
            "datasheet_quote_bandwidth": spec["datasheet_quote_bandwidth"],
            "datasheet_quote_capacity": spec["datasheet_quote_capacity"],
            "is_estimate_input": True,
            "source_file": "results/mi300x_roofline.json",
        },
        "disclaimer": (
            "elapsed_seconds is a wall-clock measurement on this process. "
            f"mi300x_hbm3_transfer_lower_bound_seconds is a {roofline_label()}. "
            f"Formula: {roofline_formula()}. "
            "Do not divide one by the other and call the ratio a speedup. "
            "ran_on_amd_gpu is false unless CuPy saw an AMD device."
        ),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.evidence)), exist_ok=True)
    with open(args.evidence, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    print(f"ran_on_amd_gpu: {report['ran_on_amd_gpu']}")
    print(f"backend_actual: {info.get('actual')}")
    print(f"device_name: {info.get('device_name')}")
    print(f"samples: {row['samples']}  elapsed_seconds: {row['elapsed_seconds']}")
    if row["mi300x_hbm3_transfer_lower_bound_seconds"] is not None:
        print(
            "theoretical roofline lower-bound estimate, not measured: "
            f"{row['mi300x_hbm3_transfer_lower_bound_seconds']} s"
        )
    print(report["disclaimer"])
    print(f"evidence: {args.evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
