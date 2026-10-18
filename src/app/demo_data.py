"""Offline walkthrough spec.

The built-in numbers come from src.sim.scenarios (a simulated mandate) and
src.config.heuristics. seed_data/price_anchor.json overrides the product,
target, and MOQ when that file is present. Neither source is a live quote.
"""
from __future__ import annotations

from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
from src.sim.scenarios import SUPPLIER_REPLIES, UMBRELLA

FALLBACK_TARGET_PRICE = UMBRELLA.target
FALLBACK_MOQ = UMBRELLA.moq
FALLBACK_PRODUCT = UMBRELLA.name

DEMO_SPEC = {
    "product": FALLBACK_PRODUCT,
    "target_price": FALLBACK_TARGET_PRICE,
    "moq": FALLBACK_MOQ,
    "n_samples": 20_000,
    "lambda_break": 1.0,
    "prior_reserve_mean": PRIOR_RESERVE_MEAN,
    "prior_reserve_std": PRIOR_RESERVE_STD,
}

# 動態從真實種子市場資料庫讀取基準設定
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ANCHOR_PATH = os.path.join(_HERE, "..", "..", "seed_data", "price_anchor.json")
if os.path.exists(_ANCHOR_PATH):
    try:
        with open(_ANCHOR_PATH, encoding="utf-8") as _f:
            _anchor = json.load(_f)
            if "target_price_cny" in _anchor:
                DEMO_SPEC["target_price"] = float(_anchor["target_price_cny"])
            if "moq" in _anchor:
                DEMO_SPEC["moq"] = int(_anchor["moq"])
            if "product" in _anchor:
                DEMO_SPEC["product"] = str(_anchor["product"])
    except Exception:
        pass
else:
    # 沒有種子檔時明確說出來，不要讓內建備援值冒充行情。
    import logging as _logging
    _logging.getLogger("tradeflow.demo_data").warning(
        "seed_data/price_anchor.json not found; DEMO_SPEC falls back to the built-in "
        "assumptions (target=%.2f, moq=%d). These are NOT market data.",
        FALLBACK_TARGET_PRICE, FALLBACK_MOQ,
    )

# Scripted normalised replies. Defined in src.sim.scenarios and re-exported
# so the CLI walkthrough keeps its import path. Not a supplier transcript.
