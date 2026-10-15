"""Generate the hackathon submission demo video (MP4, ~75s) WITHOUT GPU / keys.

Narrative (3 Wows):
  0-12s   Wow#1 Belief update: 5 negotiation rounds, posterior shifts as supplier replies.
  12-30s  Wow#2 CPU vs MI300X: bar of 9.6M rollouts solve time.
  30-55s  Wow#3 Robustness: engine vs fixed baselines + prior sensitivity.
  55-72s  Close: tagline + repo.

Frames are produced from the live engine so every number is real (not faked).
Requires: matplotlib, imageio (+ imageio-ffmpeg bundled binary), numpy.

[審計修正]
  * 新增開場 provenance 畫面：揭露供應商回價為預錄腳本、對手報價為數學模型生成。
  * 字型改為 Microsoft YaHei —— DejaVu Sans 缺 CJK 字元，中文會渲染成方框。
  * Wow#2 標註改為「CPU (measured) vs MI300X (roofline lower bound)」，
    不再宣稱 MI300X 實測值。
  * Wow#3 原印「Prior ±50% miss: strategy stays stable」為已修正的假結論，
    現改為 DEGRADES（見 LIMITATIONS §十三）。
  * 收尾畫面原寫「vectorized MCTS on AMD MI300X」，本機無 AMD GPU，
    已改為「vectorized Expectimax (CPU measured)」。
"""
from __future__ import annotations
import os, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import imageio.v2 as imageio

# [審計修正] 指定支援中日韓的字型；DejaVu Sans 無 CJK 字元會渲染成方框。
for _cjk in ("Microsoft YaHei", "SimHei", "NSimSun", "MingLiU"):
    try:
        matplotlib.font_manager.findfont(_cjk, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_cjk, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        break
    except Exception:
        continue

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
    """Wow#2: CPU (measured) vs AMD MI300X roofline lower bound for 9.6M rollouts.

    [審計修正] 舊版假設 bench 是 list of dicts（`for b in bench`），但
    `benchmark.run()` 現在回傳 summary dict，迭代會得到字串鍵而拋 TypeError；
    且欄位已由 `mi300x_projected_seconds` 改名為 `mi300x_seconds`。
    舊版亦將 GPU 數字標為 "projected on ROCm"，現依 HARDCODED_DATA_AUDIT.md
    改標為 analytical roofline 下界（物理邊界，非預期實測值）。
    """
    paths = []
    grid = bench["single_supplier_grid"] if isinstance(bench, dict) else bench
    fig, bg = fig_blank()
    title_bar(bg, "Wow #2 — 9.6M rollouts: CPU (measured) vs MI300X roofline bound", BLUE)
    row = next((b for b in grid if b["samples"] == 20000), grid[-1])
    cpu = row["cpu_seconds"]
    # [審計修正] 新版 benchmark schema 不輸出 `mi300x_seconds`（那是「CPU ÷ 假加速比」
    # 的假實測值）。現在直接讀誠實的 roofline 資料搬運下界欄位。
    mi = row.get("mi300x_transfer_lower_bound_seconds", 0.0)
    mode = row.get("execution_mode", "")
    ax = inset_axes(fig, (0.55, 0.30, 0.41, 0.45))
    ax.barh([0.65], [cpu], height=0.20, color="#C44E52")
    ax.barh([0.25], [max(mi, 1e-9)], height=0.20, color=GREEN)
    ax.set_xscale("log")
    ax.set_xlim(min(mi, cpu) * 0.5, max(cpu, mi) * 4)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.65])
    ax.set_yticklabels(["MI300X (bound)", "CPU (measured)"], color=CREAM, fontsize=12)
    ax.tick_params(axis='x', colors=CREAM)
    cpu_lbl = f"{cpu:.2f}s" if cpu >= 0.01 else f"{cpu*1000:.1f}ms"
    mi_lbl = f"{mi*1000:.3f}ms" if mi < 0.01 else f"{mi:.2f}s"
    ax.text(cpu * 1.3, 0.65, cpu_lbl, color="white", va="center", fontsize=13)
    ax.text(max(mi, 1e-9) * 1.3, 0.25, mi_lbl, color="white", va="center", fontsize=13)
    for s in ax.spines.values(): s.set_visible(False)
    cap(bg, [
        "Workload: 20,000 opponent samples",
        "× 480 strategies = 9.6M rollouts",
        f"CPU measured ~{cpu:.2f}s",
        f"MI300X lower bound ~{mi_lbl}",
        "lower bound = working set ÷ 5.3 TB/s",
        "a physical bound, not a projection",
        "GPU runs the engine's rollouts,",
        "not chat tokens",
    ], y0=0.78, size=17, dy=0.05, x=0.05, wmax=0.45)
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
        "Prior ±50% miss: strategy DEGRADES (see LIMITATIONS §十三).",
    ], y0=0.80, size=18, dy=0.055, x=0.05, wmax=0.90)
    paths.append(save(fig, "rob.png"))
    return paths


def frames_disclaimer():
    """開場標示：說明哪些是真實數據、哪些是合成輸入。

    [審計修正] 舊版影片沒有揭露「供應商回價是預錄腳本、對手報價由數學模型生成」。
    影片是提交物，評審會直接觀看，因此必須自我標註（與 LIMITATIONS §零 一致）。
    """
    paths = []
    fig, bg = fig_blank()
    title_bar(bg, "What you are about to see", GREY)
    cap(bg, [
        "REAL (measured / verifiable):",
        "  • Bayesian solver & tensor rollouts — this machine, CPU",
        "  • Throughput & latency — results/bench.json",
        "  • Robustness gain vs fixed baseline — robustness.report()",
        "  • Model inference — AMD tokenfactory (Qwen3.8-Flash-Next)",
        "",
        "SYNTHETIC (not real-world data):",
        "  • Supplier replies — pre-recorded demo script",
        "  • Adversary quotes — mathematical models, not real factories",
        "",
        "MI300X figures are analytical roofline lower bounds,",
        "not measured runtimes (no AMD GPU on this host).",
    ], y0=0.86, size=17, dy=0.052, x=0.06, wmax=0.92)
    paths.append(save(fig, "disc.png"))
    return paths


def frames_close():
    paths = []
    fig, bg = fig_blank()
    title_bar(bg, "TradeFlow AI", AMBER)
    cap(bg, [
        "Procurement negotiation, quantified — not a chatbot.",
        "Bayesian belief + vectorized Expectimax (CPU measured).",
        "Model layer runs on AMD tokenfactory; engine is ROCm-portable.",
        "MI300X = roofline lower bound, not a measured runtime.",
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
    seq += frames_disclaimer()
    seq += hold(seq, 4)              # let the disclaimer be readable
    seq += frames_belief(trace, tp, replies)
    seq += hold(seq, 3)              # hold last belief frame
    seq += frames_cpu(bench)
    seq += hold(seq, 4)
    seq += frames_robustness(report)
    seq += hold(seq, 4)
    seq += frames_close()
    seq += hold(seq, 4)

    # [審計修正] 原用 imageio.get_writer(...) 寫 mp4。本環境未安裝 imageio-ffmpeg，
    # imageio 會回退到 tifffile writer 並拋
    # "TiffWriter.write() got an unexpected keyword argument 'fps'"。
    # 現改為：先把每幀存為 PNG（已由 save() 完成），再以系統 ffmpeg 組裝，
    # 並明確回報 ffmpeg 是否可用，不靜默失敗。
    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[warn] ffmpeg not found on PATH — skipping mp4/gif assembly.")
        print("       PNG frames remain at:", os.path.dirname(seq[0]))
        print("       Assemble manually: ffmpeg -framerate %d -i disc%%04d.png -c:v libx264 -pix_fmt yuv420p out.mp4" % FPS)
        return seq

    # 單一資料夾 + 連續命名，ffmpeg 才能用 -i %04d.png
    # 注意：save() 已把幀寫在 results/_frames/，不可先清空再 link（會連來源一起刪掉）。
    frames_dir = os.path.join(os.path.dirname(OUT_MP4), "_frames")
    os.makedirs(frames_dir, exist_ok=True)
    staged_dir = os.path.join(frames_dir, "seq")
    os.makedirs(staged_dir, exist_ok=True)
    for f in os.listdir(staged_dir):
        if f.endswith(".png"):
            os.remove(os.path.join(staged_dir, f))
    width = max(4, len(str(len(seq))))
    for i, p in enumerate(seq, start=1):
        os.link(p, os.path.join(staged_dir, f"frame{str(i).zfill(width)}.png"))

    cmd = [ffmpeg, "-y", "-framerate", str(FPS), "-i",
           os.path.join(staged_dir, f"frame%0{width}d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", OUT_MP4]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("[error] ffmpeg failed:", r.stderr[-800:])
        raise SystemExit(1)
    print("mp4:", OUT_MP4, os.path.getsize(OUT_MP4), "bytes; frames:", len(seq))

    # 較小的 gif 預覽版。concat demuxer 在部分 ffmpeg build 上對 gif muxer 不穩，
    # 改用 image2 序列輸入 + 取樣濾波器更可靠。
    step = max(1, len(seq) // 40)
    gcmd = [ffmpeg, "-y", "-framerate", str(FPS), "-start_number", "1",
            "-i", os.path.join(staged_dir, f"frame%0{width}d.png"),
            "-vf", f"select='not(mod(n\\,{step}))',setpts=N/{FPS}/TB,"
                   f"fps=8,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            OUT_GIF]
    rg = subprocess.run(gcmd, capture_output=True, text=True)
    if rg.returncode != 0:
        print("[warn] gif assembly failed (mp4 is fine):", rg.stderr[-400:])
    else:
        print("gif:", OUT_GIF, os.path.getsize(OUT_GIF), "bytes")
    return seq


if __name__ == "__main__":
    build()
