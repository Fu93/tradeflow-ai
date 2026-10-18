"""Load the regenerated MI300X roofline estimate.

Hardware constants live in ``results/mi300x_roofline.json``, which
``scripts/regen_mi300x_roofline.py`` rebuilds from the AMD datasheet quotes
and the measured ``working_set_mb`` rows. This module does not keep a second
copy of 5.3 TB/s.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "results" / "mi300x_roofline.json"


@lru_cache(maxsize=1)
def load() -> dict:
    with _PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def label() -> str:
    return str(load()["label"])


def formula() -> str:
    return str(load()["formula"])


def peak_bandwidth_bytes_per_s() -> float:
    return float(load()["peak_bandwidth_bytes_per_s"])


def lower_bound_seconds(working_set_mb: float) -> float:
    """Bytes in the measured working set divided by the datasheet peak.

    ``working_set_mb`` is mebibytes, matching ``_measure_working_set_mb``.
    """
    return (float(working_set_mb) * 1024 * 1024) / peak_bandwidth_bytes_per_s()


def rounded_lower_bound_seconds(working_set_mb: float) -> float:
    return round(lower_bound_seconds(working_set_mb), 9)
