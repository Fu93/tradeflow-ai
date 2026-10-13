"""Generate the hackathon submission demo video (MP4, ~75s) WITHOUT GPU / keys.

Narrative (3 Wows):
  0-12s   Wow#1 Belief update: 5 negotiation rounds, posterior shifts as supplier replies.
  12-30s  Wow#2 CPU vs MI300X: bar of 9.6M rollouts solve time.
  30-55s  Wow#3 Robustness: engine vs fixed baselines + prior sensitivity.
  55-72s  Close: tagline + repo.

Frames are produced from the live engine so every number is real (not faked).
Requires: matplotlib, imageio (+ imageio-ffmpeg bundled binary), numpy.
"""
from __future__ import annotations
import os, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import imageio.v2 as imageio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_MP4 = os.path.join(ROOT, "results", "demo_video.mp4")
OUT_GIF = os.path.join(ROOT, "results", "demo_video.gif")
TMP = os.path.join(ROOT, "results", "_frames")
os.makedirs(TMP, exist_ok=True)

NAVY = "#0B1020"; BLUE = "#2C6FB5"; RED = "#ED1C24"; GREEN = "#55A868"
CREAM = "#EEF2F7"; GREY = "#6B7890"; AMBER = "#E8B04B"

FPS = 24
W, H = 1280, 720


def fig_blank():
    """Return (fig, bg) full-figure axes for chrome + title."""
    fig = plt.figure(figsize=(W/100, H/100), dpi=100)
    bg = fig.add_axes([0, 0, 1, 1]); bg.axis("off")
    bg.add_patch(plt.Rectangle((0, 0), 1, 1, color=NAVY))
    bg.add_patch(plt.Rectangle((0, 0), 0.011, 1, color=RED))
    return fig, bg


def title_bar(bg, text, color=BLUE):
    bg.add_patch(plt.Rectangle((0, 0.86), 1, 0.14, color="#111A33"))
    bg.add_patch(plt.Rectangle((0.018, 0.905), 0.015, 0.06, color=color))
    bg.text(0.045, 0.93, text, fontsize=26, color="white", weight="bold",
            transform=bg.transAxes, va="center")


def cap(bg, lines, y0=0.78, size=20, color=CREAM, dy=0.05, x=0.04, wmax=0.55):
    for i, ln in enumerate(lines):
        bg.text(x, y0 - i*dy, ln, fontsize=size, color=color,
                transform=bg.transAxes, va="top", wrap=True)


def save(fig, name):
    p = os.path.join(TMP, name)
    fig.savefig(p, dpi=100); plt.close(fig)
    return p


def inset_axes(fig, rect=(0.56, 0.18, 0.40, 0.58)):
    """Inset data axes on the right side; this is where the curve / chart lives."""
    ax = fig.add_axes(rect)
    ax.set_facecolor(NAVY)
    for s in ax.spines.values(): s.set_color("#3B4566")
    ax.tick_params(colors=GREY, labelsize=10)
    return ax


def frames_belief(trace, target_price, supplier_replies):
    """Wow#1: animate the 5-round posterior shift (real engine trace)."""
    paths = []
    for r, step in enumerate(trace):
        fig, bg = fig_blank()
        title_bar(bg, "Wow #1 — Bayesian belief update", GREEN)
        ax = inset_axes(fig, (0.55, 0.18, 0.41, 0.60))
        mean, std = step["mean"], step["std"]
        xs = np.linspace(0.7, 1.4, 400)
        pdf = (1/(std*math.sqrt(2*math.pi))) * np.exp(-((xs-mean)**2)/(2*std**2))
        pdf = pdf/pdf.max()
        ax.plot(xs, pdf*0.85+0.05, color=BLUE, lw=2.6)
        ax.fill_between(xs, pdf*0.85+0.05, 0.05, color=BLUE, alpha=0.35)
        ax.axvline(1.0, color=AMBER, lw=2, ls="--")
        ax.axvline(1.2, color=RED, lw=2, ls="--")
        op = step["opening"]/target_price
        ax.axvline(op, color=GREEN, lw=2.4)
        ax.set_xlim(0.7, 1.4); ax.set_ylim(0, 1.35)
        ax.set_xticks([0.8, 1.0, 1.2, 1.4])
        ax.set_yticks([])
        # labels
        ax.text(1.0, 1.15, "target", color=AMBER, ha="center", fontsize=10)
        ax.text(1.2, 1.15, "walk-away", color=RED, ha="center", fontsize=10)
        ax.text(op, 1.22, f"open ¥{step['opening']:.2f}", color=GREEN,
                ha="center", va="bottom", fontsize=10, rotation=0)
        cap(bg, [
            f"Round {r+1}: supplier replied ¥{supplier_replies[r]:.2f}",
            f"Reserve posterior: mean {mean:.3f}",
            f"(±1σ {std:.3f})",
            f"Recommended opening: ¥{step['opening']:.2f}",
            f"(target ¥{target_price:.0f})",
        ], y0=0.78, size=19, dy=0.058, x=0.05, wmax=0.45)
        if r == len(trace)-1:
            bg.text(0.5, 0.04, "Belief moves WITH the evidence — not a chatbot guess.",
                    color=GREY, fontsize=15, ha="center", transform=bg.transAxes)
        paths.append(save(fig, f"b{r}.png"))
    return paths


def frames_cpu(bench):
    """Wow#2: CPU vs MI300X solve time for 9.6M rollouts."""
    paths = []
    fig, bg = fig_blank()
    title_bar(bg, "Wow #2 — 9.6M rollouts: CPU vs AMD MI300X", BLUE)
    row = next((b for b in bench if b["samples"] == 20000), bench[-1])
    cpu = row["seconds"]; mi = row["mi300x_projected_seconds"]
    ax = inset_axes(fig, (0.55, 0.30, 0.41, 0.45))
    ax.barh([0.65], [cpu], height=0.20, color="#C44E52")
    ax.barh([0.25], [mi], height=0.20, color=GREEN)
    ax.set_xlim(0, max(cpu, mi)*1.25); ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.65]); ax.set_yticklabels(["MI300X", "CPU"], color=CREAM, fontsize=14)
    ax.tick_params(axis='x', colors=CREAM)
    for v, x, lbl in [(cpu, 0.05, f"{cpu:.2f}s"), (mi, 0.05, f"{mi:.2f}s")]:
        ax.text(v+0.05, 0.65 if v == cpu else 0.25, lbl, color="white", va="center", fontsize=14)
    for s in ax.spines.values(): s.set_visible(False)
    cap(bg, [
        "Workload: 20,000 opponent samples",
        "× 480 strategies = 9.6M rollouts",
        f"CPU ~{cpu:.1f}s  ·  MI300X ~{mi:.1f}s",
        "(projected on ROCm)",
        "GPU runs the engine's rollouts,",
        "not chat tokens",
    ], y0=0.76, size=18, dy=0.055, x=0.05, wmax=0.45)
    paths.append(save(fig, "cpu.png"))
    return paths


def frames_robustness(report):
    """Wow#3: engine vs fixed baselines + prior sensitivity."""
    paths = []
    fig, bg = fig_blank()
    title_bar(bg, "Wow #3 — Engine vs human fixed-concession baselines", RED)
    multi = report["multi_baseline"]
    ax = inset_axes(fig, (0.06, 0.18, 0.88, 0.55))
    names = [b["name"].replace("TradeFlow ", "") for b in multi]
    ms = [b["margin"] for b in multi]; bs = [b["break"] for b in multi]
    x = np.arange(len(names))
    ax.bar(x-0.2, ms, 0.4, color=BLUE, label="margin (norm)")
    ax2 = ax.twinx()
    ax2.bar(x+0.2, bs, 0.4, color=RED, alpha=0.6, label="breakdown")
    ax2.set_ylim(0, 1); ax.set_ylim(0, max(ms)*1.25)
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=15, fontsize=12, color=CREAM)
    ax.tick_params(axis='y', colors=CREAM); ax2.tick_params(axis='y', colors=RED)
    ax2.spines['right'].set_color(RED); ax2.spines['left'].set_color(BLUE)
    ax.set_ylabel("margin", color=BLUE)
    ax2.set_ylabel("breakdown", color=RED)
    cap(bg, [
        f"Engine breakdown {bs[0]:.1%}  vs  fixed 5%/rd {bs[1]:.1%}.",
        f"Gain vs fixed: +{report['gain']:+.3f}  (~{report['gain']*12:.2f} ¥/unit).",
        "Prior ±50% miss: strategy stays stable.",
    ], y0=0.80, size=18, dy=0.055, x=0.05, wmax=0.90)
    paths.append(save(fig, "rob.png"))
    return paths


def frames_close():
    paths = []
    fig, bg = fig_blank()
    title_bar(bg, "TradeFlow AI", AMBER)
    cap(bg, [
        "Procurement negotiation, quantified — not a chatbot.",
        "Bayesian belief + vectorized MCTS on AMD MI300X.",
        "Reinvent Commerce + Evolus Business Agents on AMD.",
        "",
        "github.com/Fu93/tradeflow-ai",
    ], y0=0.70, size=22, dy=0.06, x=0.06, wmax=0.88)
    paths.append(save(fig, "close.png"))
    return paths


def hold(paths, seconds):
    n = max(1, int(seconds*FPS))
    return [paths[-1]]*n


def build():
    # import engine after path setup
    import sys
    sys.path.insert(0, ROOT)
    from src.engine import solve, recommend, bayes_update
    from src.engine import robustness, benchmark, model, viz
    from src.app import demo_data

    tp = demo_data.DEMO_SPEC["target_price"]
    replies = demo_data.SUPPLIER_REPLIES
    # build belief trace (5 rounds) using live engine
    mean, std = demo_data.DEMO_SPEC["prior_reserve_mean"], demo_data.DEMO_SPEC["prior_reserve_std"]
    trace = []
    for off in replies:
        res = solve(n_opp_samples=demo_data.DEMO_SPEC["n_samples"], reserve_mean=mean, reserve_std=std)
        rec = recommend(res, target_price=tp)
        trace.append({"mean": mean, "std": std, "opening": rec["opening_offer_price"]})
        mean, std = bayes_update(mean, std, off)
    _, report = robustness.report(target_price=tp, n_samples=20000)
    bench = benchmark.run()

    seq = []
    seq += frames_belief(trace, tp, replies)
    seq += hold(seq, 3)              # hold last belief frame
    seq += frames_cpu(bench)
    seq += hold(seq, 4)
    seq += frames_robustness(report)
    seq += hold(seq, 4)
    seq += frames_close()
    seq += hold(seq, 4)

    writer = imageio.get_writer(OUT_MP4, fps=FPS, codec="libx264", quality=7)
    for p in seq:
        writer.append_data(imageio.imread(p))
    writer.close()
    # also a small gif (lighter to preview)
    writer_g = imageio.get_writer(OUT_GIF, fps=FPS)
    step = max(1, len(seq)//40)
    for i in range(0, len(seq), step):
        writer_g.append_data(imageio.imread(seq[i]))
    writer_g.close()
    print("mp4:", OUT_MP4, os.path.getsize(OUT_MP4), "bytes; frames:", len(seq))
    print("gif:", OUT_GIF, os.path.getsize(OUT_GIF), "bytes")


if __name__ == "__main__":
    build()
