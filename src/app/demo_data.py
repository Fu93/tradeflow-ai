"""預錄 demo 情境（穩定錄影用）。自動雨傘出口案例。

歸一化：1.0 = 目標價 ¥12。供應商回價以歸一化表示。
"""
from __future__ import annotations

DEMO_SPEC = {
    "product": "auto umbrella (UV, compact)",
    "target_price": 12.0,      # ¥ per unit
    "moq": 500,
    "n_samples": 20_000,
    "lambda_break": 1.0,
    "prior_reserve_mean": 1.08,
    "prior_reserve_std": 0.10,
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

# 多輪談判預錄回價（歸一化，1.0=目標價）。展示信念更新。
SUPPLIER_REPLIES = [
    1.25,   # 首輪：供應商回 ¥15（高於預期）→ 底價後驗上移
    1.18,   # 二輪：讓到 ¥14.16
    1.12,   # 三輪：讓到 ¥13.44
]
