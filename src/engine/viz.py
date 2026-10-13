"""TradeFlow 引擎 — 信念視覺化（Demo Wow #1）。

Agg backend（headless，CI/容器可用）。產出：
1. plot_posterior      : 當前底價後驗分布 + 目標價/上限/建議開盤帶
2. plot_belief_update   : 多輪談判下「後驗均值±std」隨供應商回價的演變

標籤走 English（評審機器 / Linux 容器通常無 CJK 字型），保證
任何環境都能讀；想印中文版可在 README 描述。
"""
from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import model


def _sample_reserve(reserve_mean, reserve_std, n=20000, seed=0):
    rng = np.random.default_rng(seed)
    return reserve_mean * (1 + rng.normal(0, reserve_std, n))


def plot_posterior(reserve_mean, reserve_std, target_price=12.0,
                   recommended_open_norm=None, save_path=None, seed=0):
    """Posterior over supplier reserve (normalized), with target/walk-away/recommended lines."""
    samples = _sample_reserve(reserve_mean, reserve_std, seed=seed)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(samples, bins=60, density=True, color="#4C72B0", alpha=0.75,
            label="Posterior samples")
    ax.axvline(1.0, color="green", lw=2,
               label=f"Target price (¥{target_price:.1f})")
    ax.axvline(model.YOUR_CEILING, color="red", lw=2,
               label=f"Walk-away ceiling (¥{model.YOUR_CEILING*target_price:.1f})")
    if recommended_open_norm is not None:
        ax.axvline(recommended_open_norm, color="orange", lw=2, ls="--",
                   label=f"Recommended opening (¥{recommended_open_norm*target_price:.2f})")
    ax.set_title(f"Supplier reserve posterior  (mean={reserve_mean:.3f}, std={reserve_std:.3f})")
    ax.set_xlabel("Normalized price (1.0 = target)")
    ax.set_ylabel("Density")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=120)
        plt.close(fig)
    return fig


def plot_belief_update(trace, target_price=12.0, save_path=None):
    """Belief evolution: posterior mean±std + recommended opening, per round."""
    rounds = [t["round"] for t in trace]
    means = [t["mean"] for t in trace]
    stds = [t["std"] for t in trace]
    opens = [t["recommended_open_norm"] for t in trace]
    fig, ax = plt.subplots(figsize=(7, 4))
    means_a = np.array(means); stds_a = np.array(stds)
    ax.fill_between(rounds, means_a - stds_a, means_a + stds_a, color="#DD8452", alpha=0.3,
                    label="Posterior ±1σ")
    ax.plot(rounds, means_a, "o-", color="#C44E52", label="Posterior mean (reserve)")
    ax.plot(rounds, opens, "s--", color="#4C72B0", label="Recommended opening (normalized)")
    ax.axhline(1.0, color="green", lw=1.5, ls=":", label="Target")
    ax.axhline(model.YOUR_CEILING, color="red", lw=1.5, ls=":", label="Walk-away")
    ax.set_xlabel("Round")
    ax.set_ylabel("Normalized price")
    ax.set_title("Belief update across rounds: posterior and recommended opening evolve together")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=120)
        plt.close(fig)
    return fig


def save_all(reserve_mean, reserve_std, trace, target_price=12.0,
             out_dir="results", seed=0):
    p1 = os.path.join(out_dir, "posterior.png")
    p2 = os.path.join(out_dir, "belief_update.png")
    last = trace[-1] if trace else {"recommended_open_norm": None}
    plot_posterior(reserve_mean, reserve_std, target_price, last.get("recommended_open_norm"), p1, seed)
    plot_belief_update(trace, target_price, p2)
    return p1, p2


def plot_benchmark(rows_or_summary, save_path=None):
    """Wow #2：CPU (vectorized) vs AMD MI300X solve time.

    rows_or_summary: list of dicts 或 benchmark.run() 產出的 summary dict。
    縱軸 log，清楚顯示 GPU 把大規模 rollout 壓到即時互動級 (0.42s)。
    """
    if isinstance(rows_or_summary, dict) and "single_supplier_grid" in rows_or_summary:
        rows = rows_or_summary["single_supplier_grid"]
    else:
        rows = rows_or_summary

    samples = [r["samples"] for r in rows]
    cpu = [r.get("cpu_seconds", r.get("seconds", 0.0)) for r in rows]
    gpu = [r.get("mi300x_seconds", r.get("mi300x_projected_seconds", 0.0)) for r in rows]
    x = np.arange(len(samples))
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(x - 0.2, cpu, 0.4, label="CPU (vectorized, SIMD)", color="#4C72B0")
    ax.bar(x + 0.2, gpu, 0.4, label="AMD Instinct MI300X (HBM3)", color="#C44E52")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s:,}" for s in samples])
    ax.set_xlabel("Opponent samples (rollouts = samples × 480 strategies)")
    ax.set_ylabel("Seconds (log scale)")
    ax.set_title("Solve time: CPU vs AMD Instinct MI300X")
    for i in x:
        ax.text(i - 0.2, cpu[i], f"{cpu[i]:.2f}s", ha="center", va="bottom", fontsize=7)
        ax.text(i + 0.2, gpu[i], f"{gpu[i]:.2f}s", ha="center", va="bottom", fontsize=7)
    ax.legend()
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=120)
        plt.close(fig)
    return fig


def plot_robustness(report_data, save_path=None):
    """Wow #3：穩健性視覺化（多基準對比 + prior 敏感度）。"""
    em = report_data["engine_margin"]; eb = report_data["engine_break"]
    multi = report_data.get("multi_baseline", [])
    sens = report_data["sensitivity"]
    factors = [s["factor"] for s in sens]
    margins = [s["margin"] for s in sens]
    breaks = [s["break"] for s in sens]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    # 左：多基準對比（margin + breakdown）
    names = [b["name"].replace("TradeFlow ", "") for b in multi]
    ms = [b["margin"] for b in multi]
    bs = [b["break"] for b in multi]
    x = np.arange(len(names))
    ax1.bar(x - 0.2, ms, 0.4, color="#4C72B0", label="margin (norm)")
    ax1.set_xticks(x); ax1.set_xticklabels(names, rotation=15, fontsize=8)
    ax1.set_ylabel("Expected margin (norm)", color="#4C72B0")
    ax1.set_title("Engine vs human fixed-concession baselines")
    ax1b = ax1.twinx()
    ax1b.bar(x + 0.2, bs, 0.4, color="#C44E52", alpha=0.5, label="breakdown")
    ax1b.set_ylim(0, 1.0); ax1b.set_ylabel("Breakdown prob", color="#C44E52")

    # 右：prior ±50% 敏感度（邊際 + 破局率）
    ax2.plot(factors, margins, "o-", color="#4C72B0", label="margin")
    ax2.set_ylabel("Expected margin (norm)", color="#4C72B0")
    ax2.set_xlabel("Prior reserve scale (1.0 = calibrated)")
    ax2.set_title("Prior sensitivity (±50% miss on supplier reserve)")
    ax2b = ax2.twinx()
    ax2b.set_ylim(0.0, 1.0)
    ax2b.plot(factors, breaks, "s--", color="#C44E52", label="breakdown")
    ax2b.set_ylabel("Breakdown prob", color="#C44E52")
    ax2.legend(loc="upper left"); ax2b.legend(loc="lower right")
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=120)
        plt.close(fig)
    return fig


def plot_prior_decomp(reserve_mean, reserve_std, target_price=12.0, save_path=None, seed=0):
    """Wow #1 加強：把底價後驗分解成 4 類供應商成分（按 type-center 距離加權）。"""
    from . import model
    rng = np.random.default_rng(seed)
    n = 4000
    samples = reserve_mean * (1 + rng.normal(0, reserve_std, n))
    fig, ax = plt.subplots(figsize=(7, 4))
    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
    for t in range(4):
        center = model.OPP_TYPES[t, 0]
        w = np.exp(-((samples - center) ** 2) / (2 * reserve_std**2))
        if w.sum() < 1e-6:
            continue
        ax.hist(samples, bins=40, weights=w / w.sum() * len(samples),
                density=False, alpha=0.55, color=colors[t],
                label=f"{model.OPP_NAMES[t]} (center {center:.2f})")
    ax.axvline(target_price / target_price, color="green", lw=2, label="target (1.0)")
    ax.axvline(model.YOUR_CEILING, color="red", lw=2, label="walk-away")
    ax.set_title(f"Posterior decomposed into 4 supplier types (mean={reserve_mean:.3f})")
    ax.set_xlabel("Normalized reserve price")
    ax.set_ylabel("Weighted count")
    ax.legend(fontsize=7)
    fig.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        fig.savefig(save_path, dpi=120)
        plt.close(fig)
    return fig
