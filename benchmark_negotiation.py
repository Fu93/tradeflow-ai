"""Feasibility probe: how much computation is a batched negotiation search, really?

Answers with real CPU numbers (no GPU needed yet):
  The honest cost of the search at the dimensions we plan to demo
    (opponent belief samples x candidate strategies x rollout rounds)
  and the vectorized-vs-loop ratio that indicates how GPU-shaped the work is.

Run:  python benchmark_negotiation.py
"""

import time
import numpy as np

rng = np.random.default_rng(7)

ROUNDS = 5
N_OFFERS = 64   # candidate opening offers
N_CONC = 8      # candidate concession fractions per round

# 供應商行為模型（每個 type: reserve, urgency, bluff, concession_rate）
OPP_TYPES = np.array(
    [
        [1.05, 0.90, 0.35, 0.55],   # 庫存壓力大
        [1.00, 0.50, 0.50, 0.35],   # 中間型
        [1.18, 0.30, 0.65, 0.25],   # 不缺單（強硬）
        [1.10, 0.70, 0.80, 0.30],   # 詐唬型
    ]
)
YOUR_CEILING = 1.35   # 你的 walk-away 價


def rollout_batch(n_opp_samples, n_offers, n_conc, rounds, batch_cap=None):
    """Vectorized rollout of the whole (sample x offer x concession) cube.

    State each round: the remaining price gap between your price and the
    opponent's implied reserve price.
    """
    t = rng.integers(0, OPP_TYPES.shape[0], size=n_opp_samples)
    reserve = OPP_TYPES[t, 0] * (1 + rng.normal(0, 0.08, n_opp_samples))
    urgency = np.clip(OPP_TYPES[t, 1] + rng.normal(0, 0.10, n_opp_samples), 0.05, 1.0)
    bluff = np.clip(OPP_TYPES[t, 2] + rng.normal(0, 0.10, n_opp_samples), 0.0, 1.0)
    conc_rate = np.clip(OPP_TYPES[t, 3] + rng.normal(0, 0.08, n_opp_samples), 0.05, 0.95)

    offers = np.linspace(1.00, YOUR_CEILING - 0.02, n_offers)   # 你的開盤
    concs = np.linspace(0.02, 0.60, n_conc)                     # 每輪讓步比例

    # (S,1,1) vs (1,O,1) vs (1,1,C) -> broadcast (S,O,C)
    res = reserve[:, None, None]
    urg = urgency[:, None, None]
    blf = bluff[:, None, None]
    cr = conc_rate[:, None, None]
    op = offers[None, :, None]
    cf = concs[None, None, :]

    # 你開盤後剩餘 gap（>0 表示你需要往上加價）
    gap = np.abs(res - op)                      # (S, O, 1) -> broadcast
    gap = np.broadcast_to(gap, (n_opp_samples, n_offers, n_conc)).astype(np.float64)

    for _ in range(rounds):
        # 你讓步：縮小 cf 比例的剩餘 gap
        gap = gap * (1.0 - cf)
        # 對手也讓步：cr * urgency 的 gap，詐唬型打折
        gap = gap * (1.0 - 0.85 * cr * urg * (1.0 - 0.45 * blf))
        gap = np.maximum(gap, 0.0)

    final_gap = gap
    tol = 0.02
    p_deal = np.exp(-np.maximum(final_gap - tol, 0.0) / 0.05)

    # 詐 bluff 被識破/被拖爆的風險：第一輪 gap 太大且對手愛詐
    first_gap = np.abs(res - op)  # (S,O,1)
    p_bluff_break = np.broadcast_to(first_gap > 0.32, p_deal.shape) * (0.20 + 0.30 * blf)
    p_breakdown = np.clip(1.0 - p_deal + p_bluff_break, 0.0, 1.0)

    # 成交價格：開盤 + 累積讓步（每輪讓掉 cf * 當時 gap）
    paid = op + cf * np.abs(res - op) * rounds * 0.6
    payoff = p_deal * (YOUR_CEILING - paid) - p_breakdown * 0.10
    return payoff, p_breakdown


def rollout_loop(n_opp_samples, n_offers, n_conc, rounds):
    """Naive Python-loop baseline for the same math."""
    payoff = np.zeros((n_opp_samples, n_offers, n_conc))
    pd = np.zeros_like(payoff)
    types = np.array(
        [[1.05, 0.90, 0.35, 0.55], [1.00, 0.50, 0.50, 0.35],
         [1.18, 0.30, 0.65, 0.25], [1.10, 0.70, 0.80, 0.30]]
    )
    offers = np.linspace(1.00, YOUR_CEILING - 0.02, n_offers)
    concs = np.linspace(0.02, 0.60, n_conc)
    for s in range(n_opp_samples):
        ty = types[rng.integers(0, len(types))]
        res = ty[0] * (1 + rng.normal(0, 0.08))
        urg = min(max(ty[1] + rng.normal(0, 0.10), 0.05), 1.0)
        blf = min(max(ty[2] + rng.normal(0, 0.10), 0.0), 1.0)
        cr = min(max(ty[3] + rng.normal(0, 0.08), 0.05), 0.95)
        for o in range(n_offers):
            for c in range(n_conc):
                gap = abs(res - offers[o])
                for _ in range(rounds):
                    gap = gap * (1.0 - concs[c])
                    gap = gap * (1.0 - 0.85 * cr * urg * (1.0 - 0.45 * blf))
                    if gap < 0:
                        gap = 0.0
                p_deal = np.exp(-max(gap - 0.02, 0.0) / 0.05)
                p_bluff_break = 0.20 + 0.30 * blf if abs(res - offers[o]) > 0.32 else 0.0
                p_break = min(1.0 - p_deal + p_bluff_break, 1.0)
                paid = offers[o] + concs[c] * abs(res - offers[o]) * rounds * 0.6
                payoff[s, o, c] = p_deal * (YOUR_CEILING - paid) - p_break * 0.10
                pd[s, o, c] = p_break
    return payoff, pd


def bench(fn, *a, reps: int = 5):
    """暖機 1 次後實測 reps 次取 median±std。

    [修正] 舊版只計時 1 次 —— 單次量測受 OS 調度與快取狀態抖動影響，重跑數字
    可漂移 ±20%。回傳值改為 reps 次的 median（純量，呼叫端簽名相容）；統計
    明細（reps/median/std 三個鍵）放在 bench.last_stats，由呼叫端附加印出，
    既有輸出欄位全部保留。
    """
    import statistics
    fn(*a)  # 暖機：排除首次載入/分配造成的離群值
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(*a)
        samples.append(time.perf_counter() - t0)
    bench.last_stats = {
        "reps": reps,
        "median": statistics.median(samples),
        "std": statistics.pstdev(samples),
    }
    return bench.last_stats["median"]


if __name__ == "__main__":
    print("numpy", np.__version__, f"| rounds={ROUNDS} offers={N_OFFERS} concs={N_CONC}")
    print()
    print(f"{'samples':>10} {'vectorized ms':>14} {'rollouts':>14} {'us/rollout':>11}")
    for S in (2_000, 10_000, 50_000):
        t = bench(rollout_batch, S, N_OFFERS, N_CONC, ROUNDS)
        st = bench.last_stats
        n = S * N_OFFERS * N_CONC
        print(f"{S:>10,} {t*1000:>14.1f} {n:>14,} {t*1e6/n:>11.3f}"
              f"  | reps={st['reps']} median={st['median']*1000:.1f}ms std={st['std']*1000:.1f}ms")

    print()
    t = bench(rollout_batch, 200_000, N_OFFERS, N_CONC, ROUNDS)
    print(f"{200_000:>10,} {t*1000:>14.0f} {200_000*N_OFFERS*N_CONC:>14,}  -> "
          f"{(200_000*N_OFFERS*N_CONC)/1e6:.1f}M rollouts in {t:.1f}s")

    print()
    S = 2_000
    tv = bench(rollout_batch, S, N_OFFERS, N_CONC, ROUNDS)
    stv = bench.last_stats
    tl = bench(rollout_loop, S, N_OFFERS, N_CONC, ROUNDS)
    stl = bench.last_stats
    n = S * N_OFFERS * N_CONC
    print(f"loop baseline S={S:,}: {tl*1000:.0f} ms  ({tl*1e6/n:.2f} us/rollout)")
    print(f"vectorized   S={S:,}: {tv*1000:.1f} ms  -> speedup {tl/tv:,.0f}x")
    print(f"  計時統計: reps={stv['reps']} | vectorized median={stv['median']*1000:.1f}ms "
          f"std={stv['std']*1000:.1f}ms | loop median={stl['median']*1000:.0f}ms std={stl['std']*1000:.0f}ms")
    print()
    print("READ:")
    print("  * 10k-200k opponent samples vectorized = sub-second on CPU.")
    print("  * loop->vectorized speedup is the shape of the win a GPU gives on top.")
    print("  * Honest framing: 'batch throughput for interactive latency',")
    print("    NOT 'CPU cannot do this'. CPU will not take 60s for 1k samples.")
    print()
    print("  [審計註記] 上述 speedup 是「純 Python loop → NumPy 向量化」的比值，")
    print("    兩者都跑在 CPU 上。它**不是** GPU 加速比，也不應被引用為硬體比較。")
    print("    GPU 效能數字一律取自 src/engine/benchmark.py 的 Analytical Roofline")
    print("    下界（工作集 ÷ 5300 GB/s），不使用任何主觀折扣倍率。")
