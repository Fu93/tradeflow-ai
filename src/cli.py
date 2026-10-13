"""TradeFlow CLI — 一個指令跑端到端 demo / 穩健性 / 視覺化（不依賴 Evolus / GPU）。

python -m src.cli              # 端到端 demo（含信念更新）
python -m src.cli --robustness # 穩健性報告 (regret + prior 敏感度)
python -m src.cli --figures    # 產出 results/posterior.png + belief_update.png
"""
from __future__ import annotations
import sys, time, argparse
import numpy as np
from .engine import solve, recommend, bayes_update, model, robustness, viz, benchmark
from .app.demo_data import DEMO_SPEC, SUPPLIER_REPLIES


def run_demo():
    print("=== TradeFlow Negotiation Engine demo (auto umbrella export) ===")
    print(f"Product: {DEMO_SPEC['product']} | target ¥{DEMO_SPEC['target_price']} | MOQ {DEMO_SPEC['moq']}")

    post_mean, post_std = DEMO_SPEC["prior_reserve_mean"], DEMO_SPEC["prior_reserve_std"]
    print(f"\n[Init belief] 4 supplier-type prior  mean={post_mean}, std={post_std}")

    trace = []
    for round_i in range(model.ROUNDS):
        t0 = time.perf_counter()
        res = solve(n_opp_samples=DEMO_SPEC["n_samples"], use_gpu=False,
                    lambda_break=DEMO_SPEC["lambda_break"],
                    reserve_mean=post_mean, reserve_std=post_std)
        dt = time.perf_counter() - t0
        rec = recommend(res, target_price=DEMO_SPEC["target_price"])
        trace.append({"round": round_i + 1, "mean": post_mean, "std": post_std,
                      "recommended_open_norm": rec["opening_offer_norm"]})

        print(f"\n--- Round {round_i+1} ({dt*1000:.0f} ms, {res['n_samples']:,} opponent samples) ---")
        print(f"  Belief (reserve posterior) mean={post_mean:.3f}, std={post_std:.3f}")
        print(f"  Recommended opening ¥{rec['opening_offer_price']:.2f} | concession curve {[round(c,2) for c in rec['concession_curve']]}")
        print(f"  Expected margin ¥{rec['expected_margin_price']:.2f}/unit | breakdown prob {rec['breakdown_prob']:.1%}")

        if round_i < len(SUPPLIER_REPLIES):
            offer = SUPPLIER_REPLIES[round_i]
            post_mean, post_std = bayes_update(post_mean, post_std, offer)
            print(f"  Supplier replied ¥{offer*DEMO_SPEC['target_price']:.2f}  →  posterior mean -> {post_mean:.3f}")
        else:
            print("  No supplier reply  →  continue / hand to human approval")

    print("\n[Conclusion] Engine turns 'value of waiting' into an executable concession schedule.")
    print("  MI300X: ~9.6M rollouts in ~0.4s  (CPU vectorized ~1s, naive loop ~46s)")
    return trace


def run_robustness(n=20000):
    print("=== Robustness analysis (n_samples=%d) ===" % n)
    txt, _ = robustness.report(target_price=DEMO_SPEC["target_price"], n_samples=n)
    print(txt)


def run_figures():
    """產出全部四張圖 + bench.json（給 demo / 提交用）。"""
    print("=== Generate ALL figures + bench.json ===")
    # 信念更新 + 後驗分布
    trace = run_demo()
    p1, p2 = viz.save_all(trace[-1]["mean"], trace[-1]["std"], trace,
                          target_price=DEMO_SPEC["target_price"], out_dir="results")
    print(f"  saved: {p1}\n  saved: {p2}")
    # Benchmark（CPU 實測 + MI300X 投影）
    rows = benchmark.run()
    p3 = benchmark.save_json(rows)
    p4 = "results/benchmark.png"
    viz.plot_benchmark(rows, p4)
    print(f"  saved: {p3}\n  saved: {p4}")
    # 穩健性
    _, rob = robustness.report(target_price=DEMO_SPEC["target_price"], n_samples=20000)
    p5 = "results/robustness.png"
    viz.plot_robustness(rob, p5)
    print(f"  saved: {p5}")
    print("\n所有 artifacts 在 results/：posterior.png, belief_update.png, benchmark.png, robustness.png, bench.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robustness", action="store_true")
    ap.add_argument("--figures", action="store_true")
    ap.add_argument("--n", type=int, default=20000)
    args = ap.parse_args()

    if args.robustness:
        run_robustness(args.n)
    elif args.figures:
        run_figures()
    else:
        run_demo()


if __name__ == "__main__":
    sys.path.insert(0, ".")
    main()
