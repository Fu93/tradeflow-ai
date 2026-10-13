"""Generate the hackathon submission cover image (1280x720) — no GPU needed."""
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
    ax.text(72, 500, "Bayesian Negotiation Engine on AMD MI300X", fontsize=30, color="#9FC0E8")
    ax.text(72, 452, "Decision core: Bayes + vectorized MCTS  ·  LLM: language layer only",
            fontsize=18, color="#B9C6DB")

    # Metric chips (real, measured)
    chips = [
        ("Breakdown", "6.7% vs 28.4%", "#C44E52"),
        ("Gain / unit", "+¥1.95", "#55A868"),
        ("Rollouts", "9.6M @ ~1.1s", "#4C72B0"),
        ("AMD stack", "ROCm + vLLM", "#ED1C24"),
    ]
    x = 70
    for label, val, col in chips:
        ax.add_patch(FancyBboxPatch((x, 300), 270, 110, boxstyle="round,pad=0.02,rounding_size=12",
                                    linewidth=2, edgecolor=col, facecolor="#16203C"))
        ax.text(x + 20, 372, label, fontsize=17, color="#9FB0C8")
        ax.text(x + 20, 338, val, fontsize=26, color="white", weight="bold")
        x += 300

    # Footer line
    ax.text(70, 210, "AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD",
            fontsize=16, color="#C9D6EA")
    ax.text(70, 160, "Procurement negotiation, quantified — not a chatbot.",
            fontsize=18, color="#E8B04B", style="italic")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=100, bbox_inches=None)
    plt.close(fig)
    print("saved:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
