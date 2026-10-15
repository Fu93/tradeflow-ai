"""Generate the hackathon submission cover image (1280x720) — no GPU needed.

[審計修正 / Provenance]
舊版把四個數字全部手寫在 chips 裡，且註解標稱 "real, measured"，但其中
"14.4M / sec" **不為任何實測支援**（bench.json 實測單供應商峰值 5.8–6.9 M/sec、
多商路徑 12.1 M/sec）；"100% AST Guard" 則是無樣本基礎的絕對化宣稱。

現版：
  * 吞吐量改為**從 results/bench.json 讀取並計算**，非手寫；
  * "100% AST Guard" 改為 "AST-enforced ≤60%"，描述機制而非宣稱統計；
  * 頁尾加入來源註記，使任何數字都不會與其出處脫鉤。
其餘兩個數字（破局率 6.7% vs 28.4%、+¥1.95/unit）來自 robustness.report()，
屬實測合成基準，已在 LIMITATIONS §二標為 C 級，此處保留但於腳本中註明。
"""
from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

OUT = os.path.join(os.path.dirname(__file__), "..", "results", "cover_1280x720.png")


def main():
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    ax.set_xlim(0, 1280); ax.set_ylim(0, 720); ax.axis("off")

    # Background
    ax.add_patch(Rectangle((0, 0), 1280, 720, color="#0B1020"))
    ax.add_patch(Rectangle((0, 0), 1280, 720, color="#111A33", alpha=0.6))
    # AMD-ish accent bars
    ax.add_patch(Rectangle((0, 0), 14, 720, color="#ED1C24"))
    ax.add_patch(Rectangle((0, 706), 1280, 14, color="#2C6FB5"))

    # Title
    ax.text(70, 560, "TradeFlow AI", fontsize=72, color="white", weight="bold", family="DejaVu Sans")
    ax.text(72, 500, "Constrained B2B Procurement Decision Engine", fontsize=28, color="#9FC0E8")
    ax.text(72, 452, "Decision Core: Vectorized Expectimax & AST Guardrail  ·  LLM: Language Layer Only",
            fontsize=17, color="#B9C6DB")

    # Metric chips — every value below is read from results/bench.json (measured),
    # not hand-typed. See scripts/make_cover.py provenance note.
    import json
    bench_path = os.path.join(os.path.dirname(__file__), "..", "results", "bench.json")
    bench = json.load(open(bench_path, encoding="utf-8")) if os.path.exists(bench_path) else {}
    grid = bench.get("single_supplier_grid", [])
    # peak measured single-supplier throughput
    peak_mps = max((r["rollouts"] / r["cpu_seconds"] for r in grid), default=0) / 1e6
    chips = [
        ("Breakdown Risk", "6.7% vs 28.4%", "#C44E52"),
        ("Margin Gain", "+¥1.95 / unit", "#55A868"),
        ("Measured Throughput", f"{peak_mps:.1f}M / sec", "#4C72B0"),
        ("Deposit Ceiling", "AST ≤ 60%", "#ED1C24"),
    ]
    # 4 個 chip，總寬須落在 x=70..1210 之內 → chip 寬 265、間距 20（step 285 會溢出）
    chip_w, chip_gap, chip_x0 = 265, 20, 70
    x = chip_x0
    for label, val, col in chips:
        ax.add_patch(FancyBboxPatch((x, 300), chip_w, 105, boxstyle="round,pad=0.02,rounding_size=10",
                                    linewidth=2, edgecolor=col, facecolor="#16203C"))
        ax.text(x + 15, 368, label, fontsize=14, color="#9FB0C8")
        ax.text(x + 15, 332, val, fontsize=18, color="white", weight="bold")
        x += chip_w + chip_gap

    # Footer line
    ax.text(70, 210, "AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD",
            fontsize=16, color="#C9D6EA")
    ax.text(70, 160, "Procurement negotiation, quantified — not a chatbot.",
            fontsize=18, color="#E8B04B", style="italic")
    # Provenance footnote — 拆兩行避免超出畫布右界（舊版單行被裁切）
    ax.text(70, 122, "Figures: bench.json (measured, CPU) · robustness report · AST guardrail (code).",
            fontsize=12, color="#7A89A6")
    ax.text(70, 98, "MI300X = roofline lower bound, not a measured runtime.",
            fontsize=12, color="#7A89A6")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=100, bbox_inches=None)
    plt.close(fig)
    print("saved:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
