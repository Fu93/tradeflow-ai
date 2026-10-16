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


def bench(fn, *a, reps: int = 5):
    """暖機 1 次後實測 reps 次取 median±std。

    [修正] 舊版只計時 1 次 —— 單次量測受 OS 調度與快取狀態抖動影響，重跑數字
    可漂移 ±20%。回傳簽名不變（仍為 (median_seconds, result)）；統計明細
    （reps/median/std 三個鍵）放在 bench.last_stats，由呼叫端附加印出，
    既有輸出欄位全部保留。
    """
    import statistics
    fn(*a)  # 暖機：排除首次載入/分配造成的離群值
    samples = []
    r = None
    for _ in range(reps):
        t0 = time.perf_counter()
        r = fn(*a)
        samples.append(time.perf_counter() - t0)
    bench.last_stats = {
        "reps": reps,
        "median": statistics.median(samples),
        "std": statistics.pstdev(samples),
    }
    return bench.last_stats["median"], r


if __name__ == "__main__":
    print("numpy", np.__version__, f"| rounds={ROUNDS} opens={N_OPEN} scheds={N_SCHED}")
    print(f"{'samples':>9} {'vectorized ms':>14} {'rollouts':>13} {'us/rollout':>11}")
    opens, scheds = strategy_grid()
    for S in (2_000, 10_000, 20_000, 50_000):
        dt, _ = bench(solve, S, opens, scheds)
        st = bench.last_stats
        n = S * N_OPEN * N_SCHED
        print(f"{S:>9,} {dt*1000:>14.1f} {n:>13,} {dt*1e6/n:>11.3f}"
              f"  | reps={st['reps']} median={st['median']*1000:.1f}ms std={st['std']*1000:.1f}ms")

    print()
    S = 20_000
    dt, (em, br, best) = bench(solve, S, opens, scheds)
    n = S * N_OPEN * N_SCHED
    print(f"S={S:,}: {n:,} rollout positions (S x opens x scheds, parameterised, not an execution count) in {dt*1000:.0f} ms  -> "
          f"{(dt*1000/1000):.2f} ms per 1k opponent samples")
    print(f"  建議首輪報價檔位 = opens[{best[0]}] = {opens[best[0]]:.3f}")
    print(f"  建議讓步曲線     = scheds[{best[1]}] = {np.round(scheds[best[1]],3)}")
    print(f"  期望邊際(歸一)   = {em[best]:.4f}   (×目標價¥12 約省 ¥{em[best]*12*100:.1f} / 單位)")
    print(f"  破局機率         = {br[best]:.3f}")
    st = bench.last_stats
    print(f"  計時統計         = reps={st['reps']} median={st['median']*1000:.1f} ms std={st['std']*1000:.1f} ms")

    print()
    print("GPU 敘事（依現行 Analytical Roofline 模型，非主觀投影）：")
    # loop 慢多少：v1 實測向量化/loop 比值。v1 在不同 S 下量到 37x–43x；
    # 兩份腳本過去各印一個數字而造成「互相矛盾」的外觀，此處統一為實測區間並標明來源。
    # 注意：這是**外推**，非本次實測 —— 本次只量了向量化路徑。
    #
    # [審計修正 §4.4] 舊版第 124 行沿用 `(n * 8) / (1024*1024)` 估算工作集，而該公式
    # 正是 `src/engine/benchmark.py:57-66` 明文指出**低估 9.1 倍**、並已被 tracemalloc
    # 實測取代的公式（9.6M rollout 的實測峰值為 665.9 MB，舊公式只給 73.2 MB）。
    # 一個標榜「產品級、可驗數字」的 benchmark 不應再傳播它自己審計掉掉的數字。
    # 現改為呼叫正式引擎量測，或以該模組常量做明示對照。
    working_set_mb = None
    working_set_source = None
    try:
        from src.engine import benchmark as engine_benchmark
        from src.engine import model as engine_model

        e_opens, e_scheds = engine_model.strategy_grid()
        # 用與本腳本相同的 S；引擎的 tracemalloc 量測即為同一 solve() 家族。
        working_set_mb = engine_benchmark._measure_working_set_mb(
            S, e_opens, e_scheds, use_gpu=False
        )
        working_set_source = "tracemalloc 實測（src.engine.benchmark._measure_working_set_mb）"
    except Exception as exc:  # 引擎不可用時退回明示標註的舊估值
        legacy_mb = (n * 8) / (1024 * 1024)
        working_set_mb = legacy_mb
        working_set_source = (
            f"舊版 n×8 公式（已知低估約 9.1 倍；因引擎不可用而退回，{type(exc).__name__}）"
        )

    mi300x_lower_bound_s = (working_set_mb / 1024.0) / 5300.0
    print(f"  * 每輪談判要在買方收信當下重解 1 次 = {S:,} 對手樣本 x {N_OPEN*N_SCHED} 策略 = {n:,} rollout 位")
    print(f"  * CPU 向量化（本次實測）= {dt*1000:.0f} ms")
    print(f"  * 純 loop 時間為**外推值**（沿用 v1 實測的 37–43x 比值）~ {dt*37:.0f}–{dt*43:.0f}s -> 不適合互動")
    print(f"  * MI300X：本機無 GPU，無實測值。Analytical Roofline **下界** = "
          f"工作集 {working_set_mb:.1f} MB ÷ 5300 GB/s = {mi300x_lower_bound_s*1000:.3f} ms")
    print(f"    （工作集來源：{working_set_source}）")
    print(f"    （這是物理不可逾越的下界，**不是**預期實測時間；實際值會高於此）")
    print(f"  * 敘事：'收信後即時重解讓步計畫' —— 瓶頸是訪存頻寬，故 HBM3 與 DDR5 的頻寬比"
          f"（{5300.0/60.0:.1f}x）即為理論上限")
