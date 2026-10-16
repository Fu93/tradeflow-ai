"""Run the adversarial self-play tournament and write results/self_play_benchmark.md.

[審計修正 §4.3] 舊版把 header 字串寫死為
    "Matches per Archetype: 50 (Total 750 match simulations)"
無論實際傳入 `n_matches_per_adversary` 是多少。歷史上的 `results/self_play_benchmark.md`
曾只跑了 **12 matches/archetype（60 場）**，卻仍留下「50 / Total 750」的敘事痕跡，導致
README 以「750場錦標賽基線數據」連結該檔。決策核心修復後，該 artefact 已以本腳本的
預設值（20 matches × 5 原型 = **100 場**、seed=42）重新產出。

現版做法：header 由**實際參數**插值產生，總場次亦由對手原型數算出，不可能再與
artefact 不一致。`tests/test_benchmark_integrity.py::test_arena_header_is_not_hardcoded`
以 AST 強制此不變量。
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.benchmark.arena import AdversarialArena

# `src/benchmark/arena.py` 的 run_tournament 固定跑這 5 個對手原型。
ARCHETYPES = 5
# 與 `src/engine/benchmark.py` 及 README 敘事一致的預設；`src/cli.py` 用自己的預設。
DEFAULT_MATCHES_PER_ARCHETYPE = 20
DEFAULT_N_OPP_SAMPLES = 1000


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matches",
        type=int,
        default=DEFAULT_MATCHES_PER_ARCHETYPE,
        help="Matches per adversary archetype (default: %(default)s)",
    )
    parser.add_argument(
        "--n-opp-samples",
        type=int,
        default=DEFAULT_N_OPP_SAMPLES,
        help="Opponent belief samples per solve (default: %(default)s)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        default=os.path.join("results", "self_play_benchmark.md"),
        help="Output markdown path",
    )
    args = parser.parse_args(argv)

    matches = args.matches
    total_matches = matches * ARCHETYPES

    arena = AdversarialArena(max_rounds=5, n_opp_samples=args.n_opp_samples)
    print(
        f"Running 3-way tournament benchmark across {ARCHETYPES} adversaries "
        f"({matches} matches each, {total_matches} total)..."
    )
    res = arena.run_tournament(n_matches_per_adversary=matches, seed=args.seed)
    md_table = arena.export_markdown_table(res)

    # Header 由實際參數插值 —— 不再有寫死的「50 / Total 750」。
    header = (
        "# TradeFlow v4 Adversarial Baseline Tournament Results\n\n"
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Matches per Archetype: {matches} (Total {total_matches} match simulations "
        f"across {ARCHETYPES} archetypes)\n"
        f"Opponent belief samples per solve: {args.n_opp_samples} | Seed: {args.seed}\n"
        "Benchmark Methodology: NegotiationArena (ICLR 2024), AgenticPay & Cattle Trade "
        "Evaluation Protocol (Testing Constraint Adherence, Savings & Deal Rate)\n"
        "Data class: C-level (parametric behavioural simulator; NOT live LLM API calls)\n\n"
    )

    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + md_table + "\n")

    print(f"Tournament completed. Report saved to {out_path}")
    print(md_table)


if __name__ == "__main__":
    main()
