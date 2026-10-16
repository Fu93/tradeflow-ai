"""TradeFlow 引擎 — 對手行為模型與策略網格。

對手（供應商）行為模型：4 種類型，每型 4 個參數。
所有價格以「相對你的 walk-away 上限」歸一化；1.0 = 目標價。
"""
from __future__ import annotations
import numpy as np

# 你的 walk-away 上限（規格目標價 ¥12 的 1.35 倍）。實際金額在 demo 層乘上。
YOUR_CEILING = 1.35
ROUNDS = 5

# 對手類型：[reserve 底價乘數, urgency 急單度, bluff 詐唬機率, conc_rate 每輪讓步傾向]
OPP_TYPES = np.array(
    [
        [1.05, 0.90, 0.35, 0.55],   # 庫存壓力大
        [1.00, 0.50, 0.50, 0.35],   # 中間型
        [1.18, 0.30, 0.65, 0.25],   # 不缺單（強硬）
        [1.10, 0.70, 0.80, 0.30],   # 詐唬型
    ],
    dtype=float,
)
OPP_NAMES = ["庫存壓力大", "中間型", "不缺單(強硬)", "詐唬型"]


def strategy_grid(n_open: int = 40, n_sched: int = 12):
    """480 條候選策略 = n_open 首輪報價 × n_sched 讓步曲線。

    回傳 (opens, scheds)：
      opens: (n_open,) 你的開盤價（歸一化，1.0=目標價）
      scheds: (n_sched, ROUNDS) 每輪讓步比例

    [誠實揭露 — 開盤子問題的退化性 (DEEP_DEFECT_AUDIT §3.5)]
    本網格共 480 格，但「最佳開盤價」子問題的張力遠小於「480 條策略」的敘事：
    在 `solver.solve(n_opp_samples=20,000, seed=20261013)` 下，逐開盤檔的
    最佳分數約為 `[0.1361, 0.1410, …, 0.1507, …, -0.4457]` —— 前半段近乎平坦
    （最高與最低僅差約 10%），後半段（開盤 > ~1.10）**絕大多數為負分**。
    成因：`payoff = p_deal·(ceiling − paid) − p_break·0.10` 且 `paid ≈ op`，
    開盤越接近 walk-away 上限，邊際越小、破局率越高，分數迅速轉負。

    [修正方向] `solver.py` 現已加入「對手對我方開盤的錨定反應項」
    （ANCHORING_BETA，見 solver.py 檔頭），使目標函式對開盤價有真正的內部極值
    （峰頂由 index 5 移至 6，且峰前不再單調遞增）。但該項只改變峰頂附近
    （約 ±0.001~0.006），**並未消除高開盤區的崩落**——那部分反映的是
    「開盤逼近自身上限 → 幾無邊際且破局風險升高」的經濟現實，而非數值假象。

    因此本網格的有效搜索空間遠小於 480：真正有競爭力的開盤檔集中在
    `opens[0]` ~ `opens[i_peak]`（約前 7 檔），其餘 30+ 檔在基準先驗下為負分。
    引用「480 條策略網格」時必須一併說明此集中性。
    """
    opens = np.linspace(1.00, YOUR_CEILING - 0.02, n_open)
    base = np.array(
        [
            [0.05] * ROUNDS, [0.10] * ROUNDS, [0.15] * ROUNDS, [0.20] * ROUNDS,
            np.linspace(0.03, 0.27, ROUNDS), np.linspace(0.27, 0.03, ROUNDS),
            np.linspace(0.06, 0.20, ROUNDS), np.linspace(0.20, 0.06, ROUNDS),
            [0.08, 0.10, 0.12, 0.14, 0.16], [0.16, 0.14, 0.12, 0.10, 0.08],
            [0.10, 0.08, 0.12, 0.10, 0.10], [0.12, 0.14, 0.10, 0.12, 0.10],
        ],
        dtype=float,
    )
    return opens, base[:n_sched]


def sample_opponents(rng, n_samples: int, prior_mix=None, noise=0.08,
                     reserve_mean=None, reserve_std=None):
    """從 4 類型混合抽 n_samples 個對手，回傳參數陣列。

    prior_mix: (4,) 類型權重；None = 均權。
    reserve_mean / reserve_std: 若提供，底價後驗（貝氏更新結果）覆寫預設類型中心，
        使「供應商回價後」的信念能真正影響後續求解。
    回傳 reserve, urgency, bluff, conc_rate，形狀皆 (n_samples,)。
    """
    if prior_mix is None:
        prior_mix = np.ones(4) / 4
    t = rng.choice(4, size=n_samples, p=prior_mix)
    if reserve_mean is not None and reserve_std is not None:
        reserve = reserve_mean * (1 + rng.normal(0, reserve_std, n_samples))
    else:
        reserve = OPP_TYPES[t, 0] * (1 + rng.normal(0, noise, n_samples))
    urgency = np.clip(OPP_TYPES[t, 1] + rng.normal(0, 0.10, n_samples), 0.05, 1.0)
    bluff = np.clip(OPP_TYPES[t, 2] + rng.normal(0, 0.10, n_samples), 0.0, 1.0)
    conc = np.clip(OPP_TYPES[t, 3] + rng.normal(0, 0.08, n_samples), 0.05, 0.95)
    return reserve, urgency, bluff, conc
