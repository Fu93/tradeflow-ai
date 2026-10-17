"""預錄 demo 情境（穩定錄影用）。自動雨傘出口案例。

歸一化：1.0 = 目標價 ¥12。供應商回價以歸一化表示。
"""
from __future__ import annotations

# [誠實揭露] 下方三個數字是**內建備援值**，只在 `seed_data/price_anchor.json`
# 缺失/損壞時才會被用到。它們不是市場量測結果。正常執行時 target/moq/product
# 由 seed_data 覆寫（見下方載入區塊）；若哪天 seed_data 消失，輸出仍可運行，
# 但應被視為預設假設而非行情。
FALLBACK_TARGET_PRICE = 12.0
FALLBACK_MOQ = 500
FALLBACK_PRODUCT = "auto umbrella (UV, compact)"

DEMO_SPEC = {
    "product": FALLBACK_PRODUCT,
    "target_price": FALLBACK_TARGET_PRICE,  # ¥ per unit
    "moq": FALLBACK_MOQ,
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
else:
    # 沒有種子檔時明確說出來，不要讓內建備援值冒充行情。
    import logging as _logging
    _logging.getLogger("tradeflow.demo_data").warning(
        "seed_data/price_anchor.json not found; DEMO_SPEC falls back to the built-in "
        "assumptions (target=%.2f, moq=%d). These are NOT market data.",
        FALLBACK_TARGET_PRICE, FALLBACK_MOQ,
    )

# 多輪談判預錄回價（歸一化，1.0=目標價）。展示信念更新。
# [誠實揭露] 這是**腳本化的 demo 回價**，只供 CLI 離線展示與錄影使用；
# 它不來自任何供應商，也不會被 API/儀表板當成真實對話。
SUPPLIER_REPLIES = [
    1.25,   # 首輪：供應商回 ¥15（高於預期）→ 底價後驗上移
    1.18,   # 二輪：讓到 ¥14.16
    1.12,   # 三輪：讓到 ¥13.44
]
