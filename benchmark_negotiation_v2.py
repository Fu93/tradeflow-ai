"""TradeFlow 談判引擎 — 產品級 benchmark v2（可驗數字，非玩具）

這版模擬「真實產品每輪要做的事」：供應商回一次價 → 更新對手信念 →
在「對手信念樣本 × 候選策略族」上重新求解最優讓步計畫。

這就是需要 GPU 的工作：每輪重解，且要在買方收信當下（幾秒內）給出建議。

跑：python benchmark_negotiation_v2.py
"""

import time
import numpy as np

rng = np.random.default_rng(20261013)

# ---- 對手行為模型（4 種供應商類型）----
# 每型: [reserve(底價乘數), urgency(急單度), bluff(詐唬機率), conc_rate(每輪讓步傾向)]
OPP = np.array([
    [1.05, 0.90, 0.35, 0.55],   # 庫存壓力大
    [1.00, 0.50, 0.50, 0.35],   # 中間型
    [1.18, 0.30, 0.65, 0.25],   # 不缺單（強硬）
    [1.10, 0.70, 0.80, 0.30],   # 詐唬型
])
YOUR_CEILING = 1.35   # 你的 walk-away 價（規格目標價 ¥12 的 1.35 倍上限）
ROUNDS = 5
N_OPEN = 40           # 候選首輪報價
N_SCHED = 12          # 候選讓步曲線


def strategy_grid():
    """480 條候選策略 = 40 首輪報價 × 12 讓步曲線。"""
    opens = np.linspace(1.00, YOUR_CEILING - 0.02, N_OPEN)   # 你的開盤
    # 每輪讓步比例（12 條曲線，對稱/遞增/遞減/恆定）
    base = np.array([
        [0.05]*ROUNDS, [0.10]*ROUNDS, [0.15]*ROUNDS, [0.20]*ROUNDS,
        np.linspace(0.03, 0.27, ROUNDS), np.linspace(0.27, 0.03, ROUNDS),
        np.linspace(0.06, 0.20, ROUNDS), np.linspace(0.20, 0.06, ROUNDS),
        [0.08,0.10,0.12,0.14,0.16], [0.16,0.14,0.12,0.10,0.08],
        [0.10,0.08,0.12,0.10,0.10], [0.12,0.14,0.10,0.12,0.10],
    ])
    return opens, base   # (40,), (12,5)


def solve(n_opp_samples, opens, scheds, prior_type_mix=None):
    """向量化重解：一次評估 N_opp × N_OPEN × N_SCHED 條 (對手,首輪,曲線) 組合。

    狀態：剩餘價差 gap。每輪：你讓一步，對手依其 (urgency,conc_rate,bluff) 讓一步。
    回傳：期望邊際、破局機率、最佳策略索引。
    """
    t = rng.integers(0, OPP.shape[0], size=n_opp_samples)
    reserve = OPP[t, 0] * (1 + rng.normal(0, 0.08, n_opp_samples))
    urgency = np.clip(OPP[t, 1] + rng.normal(0, 0.10, n_opp_samples), 0.05, 1.0)
    bluff  = np.clip(OPP[t, 2] + rng.normal(0, 0.10, n_opp_samples), 0.0, 1.0)
    cr     = np.clip(OPP[t, 3] + rng.normal(0, 0.08, n_opp_samples), 0.05, 0.95)

    op = opens[None, :, None]                       # (1,40,1)
    cf = scheds[None, None, :, :]                   # (1,1,12,5)
    res = reserve[:, None, None]                    # (S,1,1)
    urg = urgency[:, None, None]
    blf = bluff[:, None, None]
    conc = cr[:, None, None]

    gap = np.abs(res - op)                          # (S,40,1)
    gap = np.broadcast_to(gap, (n_opp_samples, N_OPEN, N_SCHED)).astype(np.float64)
    paid = op.copy()                                # 累積成交價
    for r in range(ROUNDS):
        # 你讓步：縮小 cf 比例
        step_you = cf[..., r] * gap
        paid = paid + step_you
        gap = gap - step_you
        # 對手讓步：conc*urgency*gap，詐唬型打折（裝不缺單）
        step_opp = conc * urg * (1.0 - 0.45 * blf) * gap
        gap = gap - step_opp
        gap = np.maximum(gap, 0.0)

    final_gap = gap
    tol = 0.02
    p_deal = np.exp(-np.maximum(final_gap - tol, 0.0) / 0.05)
    first_gap = np.abs(res - op)
    p_break = np.clip(1.0 - p_deal + (first_gap > 0.32) * (0.20 + 0.30 * blf), 0.0, 1.0)
    # 期望邊際：成交則省下 (ceiling - 成交價)；破局懲罰
    payoff = p_deal * (YOUR_CEILING - paid) - p_break * 0.10
    exp_margin = payoff.mean(axis=0)               # (40,12)
    exp_break = p_break.mean(axis=0)              # (40,12)
    best = np.unravel_index(exp_margin.argmax(), exp_margin.shape)
    return exp_margin, exp_break, best


def bench(fn, *a):
    fn(*a)
    t0 = time.perf_counter()
    r = fn(*a)
    return time.perf_counter() - t0, r


if __name__ == "__main__":
    print("numpy", np.__version__, f"| rounds={ROUNDS} opens={N_OPEN} scheds={N_SCHED}")
    print(f"{'samples':>9} {'vectorized ms':>14} {'rollouts':>13} {'us/rollout':>11}")
    opens, scheds = strategy_grid()
    for S in (2_000, 10_000, 20_000, 50_000):
        dt, _ = bench(solve, S, opens, scheds)
        n = S * N_OPEN * N_SCHED
        print(f"{S:>9,} {dt*1000:>14.1f} {n:>13,} {dt*1e6/n:>11.3f}")

    print()
    S = 20_000
    dt, (em, br, best) = bench(solve, S, opens, scheds)
    n = S * N_OPEN * N_SCHED
    print(f"S={S:,}: {n:,} rollouts in {dt*1000:.0f} ms  -> "
          f"{(dt*1000/1000):.2f} ms per 1k opponent samples")
    print(f"  建議首輪報價檔位 = opens[{best[0]}] = {opens[best[0]]:.3f}")
    print(f"  建議讓步曲線     = scheds[{best[1]}] = {np.round(scheds[best[1]],3)}")
    print(f"  期望邊際(歸一)   = {em[best]:.4f}   (×目標價¥12 約省 ¥{em[best]*12*100:.1f} / 單位)")
    print(f"  破局機率         = {br[best]:.3f}")

    print()
    print("GPU 敘事（依現行 Analytical Roofline 模型，非主觀投影）：")
    # loop 慢多少：v1 實測向量化/loop = 43x（benchmark_negotiation.py, S=2000）
    # 注意：這是**外推**，非本次實測 —— 本次只量了向量化路徑。
    loop_s = dt * 43
    # [審計修正] 舊版在此印出「MI300X 預期 ~0.3-0.6s（約 10-20x）」與「MI300X 0.4s」。
    # 那兩個數字是人為選定的折扣倍率，已於 HARDCODED_DATA_AUDIT.md 移除。
    # 現改為從物理量推導的 roofline 下界：工作集位元組 ÷ HBM3 峰值頻寬。
    working_set_mb = (n * 8) / (1024 * 1024)
    mi300x_lower_bound_s = (working_set_mb / 1024.0) / 5300.0
    print(f"  * 每輪談判要在買方收信當下重解 1 次 = {S:,} 對手樣本 x {N_OPEN*N_SCHED} 策略 = {n:,} rollouts")
    print(f"  * CPU 向量化（本次實測）= {dt*1000:.0f} ms")
    print(f"  * 純 loop 時間為**外推值**（沿用 v1 實測的 43x 比值）~ {loop_s:.0f}s -> 不適合互動")
    print(f"  * MI300X：本機無 GPU，無實測值。Analytical Roofline **下界** = "
          f"工作集 {working_set_mb:.1f} MB ÷ 5300 GB/s = {mi300x_lower_bound_s*1000:.3f} ms")
    print(f"    （這是物理不可逾越的下界，**不是**預期實測時間；實際值會高於此）")
    print(f"  * 敘事：'收信後即時重解讓步計畫' —— 瓶頸是訪存頻寬，故 HBM3 與 DDR5 的頻寬比"
          f"（{5300.0/60.0:.1f}x）即為理論上限")
