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

# 多輪談判預錄回價（歸一化，1.0=目標價）。展示信念更新。
SUPPLIER_REPLIES = [
    1.25,   # 首輪：供應商回 ¥15（高於預期）→ 底價後驗上移
    1.18,   # 二輪：讓到 ¥14.16
    1.12,   # 三輪：讓到 ¥13.44
]
