#!/usr/bin/env python3
"""Regenerate the MI300X roofline estimate from measured working sets and the datasheet.

The only hardware inputs are figures quoted by AMD. The only workload input is
``working_set_mb`` already stored on each row of ``results/bench.json``. That
value is the tracemalloc peak from
``src/engine/benchmark.py:_measure_working_set_mb`` (peak bytes / (1024 * 1024)).
This script does not re-time the CPU and does not invent a host DDR speed.

Formula
-------
lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)

5.3e12 bytes/s is the datasheet's published "5.3 TB/s max. peak theoretical".
Footnote MI300-18 on the MI300 family page derives 5.325 TB/s as
``8192 bits * 5.2 Gbps / 8``. That product explains the rounding. The estimate
uses 5.3 TB/s, the figure the datasheet and the product page both print.

Writes ``results/mi300x_roofline.json`` and refreshes only the roofline fields
and ``mi300x_transfer_lower_bound_seconds`` in ``results/bench.json``.

    python scripts/regen_mi300x_roofline.py
    python scripts/regen_mi300x_roofline.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "results" / "bench.json"
OUT_PATH = ROOT / "results" / "mi300x_roofline.json"

LABEL = "theoretical roofline lower-bound estimate, not measured"

# Quoted from the AMD Instinct MI300X datasheet PDF:
# "5.3 TB/s max. peak theoretical" and "Up to 192 GB HBM3".
DATASHEET_URL = (
    "https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/"
    "data-sheets/amd-instinct-mi300x-data-sheet.pdf"
)
DATASHEET_QUOTE_BANDWIDTH = "5.3 TB/s max. peak theoretical"
DATASHEET_QUOTE_CAPACITY = "Up to 192 GB HBM3"

# Product page: Peak Memory Bandwidth 5.3 TB/s, Dedicated Memory Size 192 GB.
PRODUCT_URL = "https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html"
PRODUCT_QUOTE_BANDWIDTH = "Peak Memory Bandwidth 5.3 TB/s"
PRODUCT_QUOTE_CAPACITY = "Dedicated Memory Size 192 GB"

# Footnote MI300-18. Derivation of the published rounding, not a second bandwidth.
FOOTNOTE_URL = "https://www.amd.com/en/products/accelerators/instinct/mi300.html"
FOOTNOTE_FORMULA = "8192 bits * 5.2 Gbps / 8"
BUS_WIDTH_BITS = 8192
PIN_RATE_GBPS = float(Decimal("5.2"))
FOOTNOTE_QUOTED_TB_S = float(Decimal("5.325"))

PEAK_TB_S = float(Decimal("5.3"))
CAPACITY_GB = 192
PEAK_BYTES_PER_S = PEAK_TB_S * (10 ** 12)
FORMULA = "lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)"

WORKING_SET_DERIVATION = (
    "working_set_mb is the tracemalloc peak already stored on the matching "
    "samples row of results/bench.json. "
    "src/engine/benchmark.py:_measure_working_set_mb records peak bytes during "
    "solver.solve and divides by (1024 * 1024). This script does not re-time the CPU."
)


def footnote_product_gb_s() -> float:
    """8192 * 5.2 / 8, in GB/s. Integer arithmetic so 5.2 does not drift."""
    return (BUS_WIDTH_BITS * 52) / 80


def lower_bound_seconds(working_set_mb: float) -> float:
    return (float(working_set_mb) * 1024 * 1024) / PEAK_BYTES_PER_S


def rounded_lower_bound_seconds(working_set_mb: float) -> float:
    return round(lower_bound_seconds(working_set_mb), 9)


def _load_bench() -> dict:
    return json.loads(BENCH_PATH.read_text(encoding="utf-8"))


def build_payload(bench: dict | None = None) -> dict:
    bench = _load_bench() if bench is None else bench
    rows = []
    for row in bench["single_supplier_grid"]:
        mb = float(row["working_set_mb"])
        rows.append(
            {
                "samples": int(row["samples"]),
                "working_set_mb": mb,
                "working_set_bytes": mb * 1024 * 1024,
                "lower_bound_seconds": rounded_lower_bound_seconds(mb),
            }
        )
    return {
        "label": LABEL,
        "kind": LABEL,
        "formula": FORMULA,
        "formula_notes": (
            "working_set_mb is mebibytes (tracemalloc bytes / 1024 / 1024). "
            "Multiplying by 1024 * 1024 restores bytes. "
            "The denominator is 5.3e12 bytes/s, the datasheet's 5.3 TB/s."
        ),
        "working_set_derivation": WORKING_SET_DERIVATION,
        "peak_bandwidth_tb_s": PEAK_TB_S,
        "peak_bandwidth_gb_s": PEAK_TB_S * 1000,
        "peak_bandwidth_bytes_per_s": PEAK_BYTES_PER_S,
        "hbm3_capacity_gb": CAPACITY_GB,
        "datasheet_url": DATASHEET_URL,
        "datasheet_quote_bandwidth": DATASHEET_QUOTE_BANDWIDTH,
        "datasheet_quote_capacity": DATASHEET_QUOTE_CAPACITY,
        "product_page_url": PRODUCT_URL,
        "product_page_quote_bandwidth": PRODUCT_QUOTE_BANDWIDTH,
        "product_page_quote_capacity": PRODUCT_QUOTE_CAPACITY,
        "footnote": {
            "url": FOOTNOTE_URL,
            "id": "MI300-18",
            "formula": FOOTNOTE_FORMULA,
            "bus_width_bits": BUS_WIDTH_BITS,
            "pin_rate_gbps": PIN_RATE_GBPS,
            "product_gb_s": footnote_product_gb_s(),
            "quoted_rounding_tb_s": FOOTNOTE_QUOTED_TB_S,
            "role": (
                "Derivation of AMD's published rounding to 5.3 TB/s. "
                "The estimate denominator is the datasheet figure 5.3 TB/s, "
                "not this footnote product."
            ),
        },
        "rows": rows,
        "removed_because_unsourced": [
            "cpu_bandwidth_gb_s",
            "bandwidth_ratio_cpu_to_mi300x",
            "Any host DDR bandwidth figure. The bench file does not name a CPU.",
            "Any MI300X runtime or speedup whose inputs are not in this file.",
        ],
        "not_a_measurement": True,
    }


def roofline_specs() -> dict:
    payload = build_payload()
    return {
        "label": payload["label"],
        "formula": payload["formula"],
        "peak_bandwidth_tb_s": payload["peak_bandwidth_tb_s"],
        "peak_bandwidth_gb_s": payload["peak_bandwidth_gb_s"],
        "peak_bandwidth_bytes_per_s": payload["peak_bandwidth_bytes_per_s"],
        "hbm3_capacity_gb": payload["hbm3_capacity_gb"],
        "datasheet_url": payload["datasheet_url"],
        "datasheet_quote_bandwidth": payload["datasheet_quote_bandwidth"],
        "datasheet_quote_capacity": payload["datasheet_quote_capacity"],
        "product_page_url": payload["product_page_url"],
        "source_file": "results/mi300x_roofline.json",
        "note": (
            f"{LABEL}. Not a runtime and not a speedup. "
            "Host DDR bandwidth is omitted because this bench file does not "
            "name a CPU and the datasheet does not state one."
        ),
    }


def disclosure_text() -> str:
    return (
        "No AMD GPU was present on the measuring host unless is_gpu_active is true. "
        f"mi300x_transfer_lower_bound_seconds is a {LABEL}. "
        f"Formula: {FORMULA}. {WORKING_SET_DERIVATION} "
        f"{DATASHEET_QUOTE_BANDWIDTH} ({DATASHEET_URL}). "
        f"{DATASHEET_QUOTE_CAPACITY}. "
        "Do not divide the bound by cpu_seconds to claim a speedup."
    )


def apply_to_bench(bench: dict) -> dict:
    updated = json.loads(json.dumps(bench))
    specs = roofline_specs()
    # Drop keys whose inputs were never sourced to this host or the datasheet.
    for stale in ("cpu_bandwidth_gb_s", "bandwidth_ratio_cpu_to_mi300x", "mi300x_hbm3_bandwidth_gb_s"):
        specs.pop(stale, None)
    updated["roofline_specs"] = specs
    for row in updated["single_supplier_grid"]:
        row["mi300x_transfer_lower_bound_seconds"] = rounded_lower_bound_seconds(row["working_set_mb"])
        row["mi300x_transfer_lower_bound_label"] = LABEL
    updated["disclosure"] = disclosure_text()
    return updated


def canonical(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _problems(bench: dict | None = None) -> list[str]:
    problems: list[str] = []
    bench = _load_bench() if bench is None else bench
    payload = build_payload(bench)
    if not OUT_PATH.is_file():
        problems.append(f"missing {OUT_PATH.relative_to(ROOT).as_posix()}")
    else:
        on_disk = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        if canonical(on_disk) != canonical(payload):
            problems.append("results/mi300x_roofline.json diverges from scripts/regen_mi300x_roofline.py")
    expected_bench = apply_to_bench(bench)
    got_specs = bench.get("roofline_specs", {})
    if canonical(got_specs) != canonical(expected_bench["roofline_specs"]):
        problems.append("results/bench.json roofline_specs diverges from the regenerator")
    for stale in ("cpu_bandwidth_gb_s", "bandwidth_ratio_cpu_to_mi300x"):
        if stale in got_specs:
            problems.append(f"results/bench.json still has unsourced {stale}")
    by_samples = {row["samples"]: row for row in expected_bench["single_supplier_grid"]}
    for row in bench["single_supplier_grid"]:
        expected = by_samples[int(row["samples"])]["mi300x_transfer_lower_bound_seconds"]
        actual = row.get("mi300x_transfer_lower_bound_seconds")
        if actual != expected:
            problems.append(
                f"samples={row['samples']}: lower bound {actual} != regenerated {expected}"
            )
        if row.get("mi300x_transfer_lower_bound_label") != LABEL:
            problems.append(f"samples={row['samples']}: missing the roofline label")
    if bench.get("disclosure") != expected_bench["disclosure"]:
        problems.append("results/bench.json disclosure diverges from the regenerator")
    return problems


def write_outputs() -> None:
    bench = _load_bench()
    payload = build_payload(bench)
    OUT_PATH.write_text(canonical(payload), encoding="utf-8")
    updated = apply_to_bench(bench)
    BENCH_PATH.write_text(canonical(updated), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the committed JSON diverges. Do not write.",
    )
    args = parser.parse_args(argv)
    if args.check:
        problems = _problems()
        if problems:
            print("MI300X roofline source is stale:", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("regen_mi300x_roofline: committed JSON matches the formula.")
        return 0
    write_outputs()
    print(f"wrote {OUT_PATH.relative_to(ROOT).as_posix()}")
    print(f"updated roofline fields in {BENCH_PATH.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
