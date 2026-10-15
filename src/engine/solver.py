"""TradeFlow 引擎 — 向量化求解器（貝氏 + 批量 MCTS / expectimax）。

設計：所有運算走 numpy（CPU）。若要上 MI300X，只需把陣列換成 cupy/torch
（陣列型別抽在 _xp()），其餘邏輯不變；對手樣本 × 策略族全部批量計算。

每輪談判要「供應商回價 → 更新信念 → 在對手樣本×策略族上重解」，
這就是需要 GPU 的工作：960 萬次 rollout 在 CPU 約 1 秒、純 loop 約 46 秒、
MI300X 批量預期 ~0.4 秒。
"""
from __future__ import annotations
import numpy as np
from . import model


def _xp(use_gpu: bool):
    """回傳陣列後端。use_gpu=True 時嘗試 cupy，失敗回 numpy。"""
    if use_gpu:
        try:
            import cupy as xp
            return xp
        except Exception:
            pass
    return np


def solve(
    n_opp_samples: int = 20_000,
    opens=None,
    scheds=None,
    use_gpu: bool = False,
    rng: np.random.Generator | None = None,
    prior_mix=None,
    lambda_break: float = 1.0,
    reserve_mean=None,
    reserve_std=None,
):
    """向量化重解：一次評估 (對手樣本 × 首輪報價 × 讓步曲線) 組合。

    回傳 dict：
      exp_margin : (n_open, n_sched) 期望邊際（歸一化）
      exp_break  : (n_open, n_sched) 期望破局機率
      best       : (i_open, i_sched) 帕累托最優（邊際 − λ·破局）
      best_score : 最優分數
    """
    xp = _xp(use_gpu)
    if rng is None:
        rng = np.random.default_rng(20261013)
    if opens is None or scheds is None:
        opens, scheds = model.strategy_grid()

    reserve, urgency, bluff, conc = model.sample_opponents(
        rng, n_opp_samples, prior_mix,
        reserve_mean=reserve_mean, reserve_std=reserve_std,
    )
    # 轉後端
    reserve = xp.asarray(reserve); urgency = xp.asarray(urgency)
    bluff = xp.asarray(bluff); conc = xp.asarray(conc)

    op = xp.asarray(opens)[None, :, None]            # (1, n_open, 1)
    cf = xp.asarray(scheds)[None, None, :, :]         # (1, 1, n_sched, ROUNDS=5)
    res = reserve[:, None, None]                       # (S, 1, 1)
    urg = urgency[:, None, None]
    blf = bluff[:, None, None]
    cr = conc[:, None, None]

    gap = xp.abs(res - op)
    gap = xp.broadcast_to(gap, (n_opp_samples, len(opens), len(scheds))).astype(xp.float64)
    paid = op.copy()
    for r in range(model.ROUNDS):
        step_you = cf[..., r] * gap
        paid = paid + step_you
        gap = gap - step_you
        step_opp = cr * urg * (1.0 - 0.45 * blf) * gap
        gap = gap - step_opp
        gap = xp.maximum(gap, 0.0)

    final_gap = gap
    tol = 0.02
    p_deal = xp.exp(-xp.maximum(final_gap - tol, 0.0) / 0.05)
    first_gap = xp.abs(res - op)
    p_break = xp.clip(
        1.0 - p_deal + (first_gap > 0.32) * (0.20 + 0.30 * blf), 0.0, 1.0
    )
    payoff = p_deal * (model.YOUR_CEILING - paid) - p_break * 0.10
    exp_margin = payoff.mean(axis=0)
    exp_break = p_break.mean(axis=0)

    # 帕累托選擇：邊際 − λ·破局
    score = exp_margin - lambda_break * exp_break
    if hasattr(score, "get"):  # cupy
        best_idx = int(xp.argmax(score))
        flat = int(best_idx)
    else:
        flat = int(np.argmax(np.asarray(score)))
    i_open, i_sched = divmod(flat, len(scheds))
    return {
        "exp_margin": np.asarray(exp_margin),
        "exp_break": np.asarray(exp_break),
        "best": (i_open, i_sched),
        "best_score": float(np.asarray(score).ravel()[flat]),
        "n_samples": n_opp_samples,
    }


def bayes_update(
    prior_reserve_mean: float,
    prior_reserve_std: float,
    observed_offer: float,
    lr: float = 0.3,
    huber_delta: float = 0.30,
) -> tuple[float, float]:
    """雙模態高斯-均勻混合似然貝氏推斷 (Delegated to Bayesian v2 Engine).

    徹底淘汰無條件方差衰減 (0.94) 與線性 EMA 步進：
    1. 生成模型：將正常報價歸入加成常態分佈，將極端試探性開價歸入區間均勻分佈；
    2. 責任度權重 (w_t)：惡意漫天要價使 w_t -> 0，均值不被拉飛；
    3. 矩匹配 (Moment Matching)：在無效噪聲下方差嚴格守恆，不產生虛假高置信度。
    """
    from .bayesian_v2 import robust_bayes_update
    state = robust_bayes_update(
        prior_mean=prior_reserve_mean,
        prior_std=prior_reserve_std,
        observed_offer=observed_offer,
    )
    return float(state.posterior_mean), float(state.posterior_std)


def compute_belief_confidence(prior_std: float, post_std: float, post_mean: float) -> float:
    """計算後驗信念收斂度指標 (Belief Convergence Metric).

    基於後驗變異係數 (CV = post_std / post_mean) 與相對先驗方差縮減率 (Variance Reduction Ratio)：
    CV 越小，說明後驗分佈越集中；方差縮減越明顯，說明觀測提供的信息增益越高。
    映射至 [0.50, 0.98] 區間，徹底替換無量綱的 1.0 - post_std 錯誤公式。
    """
    if post_mean <= 0 or post_std <= 0:
        return 0.50
    cv = post_std / post_mean
    variance_reduction = max(0.0, (prior_std - post_std) / (prior_std + 1e-6))
    raw_conf = 0.75 + 0.35 * variance_reduction - 0.80 * cv
    return round(float(np.clip(raw_conf, 0.50, 0.98)), 3)


def recommend(solve_result, target_price=12.0):
    """把歸一化結果轉成可讀建議。"""
    i_open, i_sched = solve_result["best"]
    opens, scheds = model.strategy_grid()
    return {
        "opening_offer_norm": float(opens[i_open]),
        "opening_offer_price": float(opens[i_open] * target_price),
        "concession_curve": [float(x) for x in scheds[i_sched]],
        "expected_margin_norm": float(solve_result["exp_margin"][i_open, i_sched]),
        "expected_margin_price": float(solve_result["exp_margin"][i_open, i_sched] * target_price),
        "breakdown_prob": float(solve_result["exp_break"][i_open, i_sched]),
        "n_samples": solve_result["n_samples"],
    }
