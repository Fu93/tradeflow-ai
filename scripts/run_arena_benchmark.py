import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.benchmark.arena import AdversarialArena

def main():
    arena = AdversarialArena(max_rounds=5, n_opp_samples=500)
    print("Running 3-way tournament benchmark across 5 adversaries (50 matches each)...")
    res = arena.run_tournament(n_matches_per_adversary=50, seed=42)
    md_table = arena.export_markdown_table(res)

    header = (
        "# TradeFlow v4 Adversarial Baseline Tournament Results\n\n"
        f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "Matches per Archetype: 50 (Total 750 match simulations)\n"
        "Benchmark Methodology: NegotiationArena (ICLR 2024), AgenticPay & Cattle Trade Evaluation Protocol (Testing Constraint Adherence, Savings & Deal Rate)\n\n"
    )

    out_path = os.path.join("results", "self_play_benchmark.md")
    os.makedirs("results", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + md_table + "\n")

    print(f"Tournament completed. Report saved to {out_path}")
    print(md_table)

if __name__ == "__main__":
    main()
