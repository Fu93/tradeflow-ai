"""TradeFlow 引擎 — 向量化求解器（貝氏 + 批量 expectimax）。

設計：陣列運算走 `_xp()`。預設是 NumPy（CPU）。`TRADEFLOW_SOLVER_BACKEND=cupy`
（或顯式 `use_gpu=True`）會改試 CuPy；CuPy 不在時仍回 NumPy，不會假裝跑過 GPU。

本儲存庫沒有 AMD GPU 實測。延遲以 `results/bench.json` 的 CPU 牆鐘為準。
`src/engine/benchmark.py` 裡的 MI300X 數字是工作集 ÷ 公開 HBM3 峰值頻寬的
資料搬運下界，不是執行時間。
"""
from __future__ import annotations
import numpy as np
from src.config.heuristics import (
    ANCHORING_BETA,
    ANCHORING_CLIP,
    ANCHORING_REF,
    BELIEF_CONF_BASE,
    BELIEF_CONF_CV,
    BELIEF_CONF_HI,
    BELIEF_CONF_LO,
    BELIEF_CONF_VAR,
    BREAK_PENALTY,
    CONCESSION_RATE_CAP,
    DEAL_DECAY_SCALE,
    DEAL_TOLERANCE,
    OPENING_BREAK_BASE,
    OPENING_BREAK_BLUFF,
    OPENING_GAP_BREAK,
    OPPONENT_GAP_SHARE,
)
from src.sim.scenarios import UMBRELLA
from . import model
from .backend import resolve_use_gpu

# Re-exported so tests and callers can still assign solver.ANCHORING_BETA = 0.
# The values live in src.config.heuristics.

# ============================================================================
# [誠實揭露] 對抗式開盤錨定反應係數 (Adversarial Anchoring Reaction)
#
# 舊版 payoff 完全不含「對手如何回應我方開盤」這一項，於是：
#   payoff = p_deal * (ceiling - paid) - p_break * 0.10
# 在低開盤區間 p_deal ≈ 1、paid ≈ op，使逐開盤檔最優分數單調上升後崩塌
# （實測 0.1361, 0.1410, …, 0.1507, …, -0.4457），「搜尋最佳開盤價」幾乎沒有張力
# （DEEP_DEFECT_AUDIT §3.5）。這裡補上對手對開盤的反應項，使開盤價真正進入目標函式。
#
# 模型直覺：
#   * 開盤越接近對手底價（first_gap 小）→ 對手視為接近成交／善意 → 讓步率上調；
#   * 開盤越低（first_gap 大，強硬還價）→ 對手視為被壓價 → 讓步率下調（hardening）。
# 數學形式（乘在對手每輪讓步率 cr 上）：
#   react  = clip(1 - BETA * (first_gap - REF), CLIP_LO, CLIP_HI)
#   cr_eff = clip(cr * react, 0, 0.95)
#
# 下列三個常數皆為**選定值**（啟發式，非資料擬合/訓練）：
#   ANCHORING_REF  = 0.10 : 中性開盤差距參考尺度（歸一化，1.0 = 目標價）；
#   ANCHORING_BETA = 1.00 : 差距每偏離參考 1.0，讓步率乘數的變動量；
#   ANCHORING_CLIP = (0.50, 1.50) : 乘數上下界，避免外推時讓步率失控/歸零。
# 設定 `solver.ANCHORING_BETA = 0.0` 可還原舊版（無錨定反應）的目標函式，
# 此槓桿即迴歸測試的依據（見 tests/test_solver_behavior.py）。
# ============================================================================
def anchoring_multiplier(xp, gap):
    """後端無關（NumPy/CuPy）的對手讓步率錨定反應乘數。

    react = clip(1 − BETA·(gap − REF), CLIP_LO, CLIP_HI)，見檔頭常數區的揭露。
    """
    react = 1.0 - ANCHORING_BETA * (gap - ANCHORING_REF)
    return xp.clip(react, ANCHORING_CLIP[0], ANCHORING_CLIP[1])


def _pareto_frontier_indices(margin: np.ndarray, break_prob: np.ndarray) -> list:
    """真正的前沿篩選：回傳 (margin 極大, break 極小) 二目標下的第一層非支配集。

    [修正 §3.4/§3.7] 舊版把 `argmax(margin − λ·break)` 稱為「帕雷托最優」，那是
    術語誤用：加權和純量化的 argmax 只會落在前沿上的某一點，並不等於「回傳前沿」。
    本函式才是真正的非支配篩選 (non-dominated filter)：策略 a 支配 b ⇔
    margin_a ≥ margin_b 且 break_a ≤ break_b，且至少一項嚴格。

    實作：一次廣播出 (n, n) 的支配關係矩陣（n = n_open × n_sched = 480），
    再取「不被任何人支配」者。回傳 [i_open, i_sched] 索引串列，依 margin 由高到低排序。
    """
    m = np.asarray(margin, dtype=np.float64).ravel()
    b = np.asarray(break_prob, dtype=np.float64).ravel()
    n = m.size
    if n == 0:
        return []
    # dominates[i, j] = i 支配 j
    ge = m[:, None] >= m[None, :]
    le = b[:, None] <= b[None, :]
    strict = (m[:, None] > m[None, :]) | (b[:, None] < b[None, :])
    dominated = (ge & le & strict).any(axis=0)
    keep = np.flatnonzero(~dominated)
    keep = keep[np.argsort(-m[keep])]
    shape = np.asarray(margin).shape
    return [[int(i), int(j)] for i, j in (np.unravel_index(int(f), shape) for f in keep)]


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
    use_gpu: bool | None = None,
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
      best       : (i_open, i_sched) 純量選取（邊際 − λ·破局 之 argmax）
      best_score : 最優分數
      pareto_frontier_size : (margin 極大, break 極小) 下的第一層非支配集大小

    [修正 §3.4] 舊版把這裡的 `best` 描述為「帕雷托最優」，但它是加權和標量化
    的 argmax。現在 docstring 與註解都改為「純量選取」，並另外提供真正的
    非支配前沿（`_pareto_frontier_indices`，其大小以 `pareto_frontier_size`
    回報）。`best` 索引與回傳鍵皆不變，僅新增 `pareto_frontier_size`。
    """
    use_gpu = resolve_use_gpu(use_gpu)
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

    # [誠實揭露] 下列 payoff / 對手讓步常數為編譯時固定之啟發式值，非經資料
    # 擬合/訓練調出：
    #   - 0.45  : 對手每輪吃掉的剩餘差距比例（bluff 越高吃越少）；
    #   - 0.02  : 成交容差閾值（差距 ≤ 2% 視為成交）；
    #   - 0.05  : p_deal 的指數衰減尺度（差距每多 5% 成交機率 e^-1）；
    #   - 0.32  : 首輪差距超過此值判定「開盤過高」；
    #   - 0.20/0.30*blf : 開盤過高時的破局額外懲罰（bluff 加重）；
    #   - 0.10  : 破局懲罰權重（payoff 中扣 0.10）。
    gap = xp.abs(res - op)
    gap = xp.broadcast_to(gap, (n_opp_samples, len(opens), len(scheds))).astype(xp.float64)
    paid = op.copy()
    # [修正 §3.5] 對手對我方開盤的錨定反應（見檔頭常數區的公式與揭露）。
    # first_gap 是固定量（隨 op 變、與輪次無關），所以乘數可逐 (n_open) 預算。
    first_gap = xp.abs(res - op)
    react = anchoring_multiplier(xp, first_gap)
    cr_eff = xp.clip(cr * react, 0.0, CONCESSION_RATE_CAP)
    for r in range(model.ROUNDS):
        step_you = cf[..., r] * gap
        paid = paid + step_you
        gap = gap - step_you
        step_opp = cr_eff * urg * (1.0 - OPPONENT_GAP_SHARE * blf) * gap
        gap = gap - step_opp
        gap = xp.maximum(gap, 0.0)

    final_gap = gap
    tol = DEAL_TOLERANCE
    p_deal = xp.exp(-xp.maximum(final_gap - tol, 0.0) / DEAL_DECAY_SCALE)
    p_break = xp.clip(
        1.0 - p_deal + (first_gap > OPENING_GAP_BREAK) * (OPENING_BREAK_BASE + OPENING_BREAK_BLUFF * blf),
        0.0,
        1.0,
    )
    payoff = p_deal * (model.YOUR_CEILING - paid) - p_break * BREAK_PENALTY
    exp_margin = payoff.mean(axis=0)
    exp_break = p_break.mean(axis=0)

    # [修正 §3.4] 純量選取：邊際 − λ·破局 之 argmax（**非**帕雷托選擇）
    score = exp_margin - lambda_break * exp_break
    if hasattr(score, "get"):  # cupy
        best_idx = int(xp.argmax(score))
        flat = int(best_idx)
    else:
        flat = int(np.argmax(np.asarray(score)))
    i_open, i_sched = divmod(flat, len(scheds))

    margin_np = np.asarray(exp_margin.get() if hasattr(exp_margin, "get") else exp_margin)
    break_np = np.asarray(exp_break.get() if hasattr(exp_break, "get") else exp_break)
    frontier = _pareto_frontier_indices(margin_np, break_np)

    return {
        "exp_margin": margin_np,
        "exp_break": break_np,
        "best": (i_open, i_sched),
        "best_score": float(np.asarray(score).ravel()[flat]),
        "n_samples": n_opp_samples,
        # 僅新增鍵（介面凍結允許）；標量化 argmax 與真前沿的量化差距。
        "pareto_frontier": frontier,
        "pareto_frontier_size": len(frontier),
    }


def bayes_update(
    prior_reserve_mean: float,
    prior_reserve_std: float,
    observed_offer: float,
    estimated_markup: float | None = None,
) -> tuple[float, float]:
    """雙模態高斯-均勻混合似然貝氏推斷 (Delegated to Bayesian v2 Engine).

    徹底淘汰無條件方差衰減 (0.94) 與線性 EMA 步進：
    1. 生成模型：將正常報價歸入加成常態分佈，將極端試探性開價歸入區間均勻分佈；
    2. 責任度權重 (w_t)：惡意漫天要價使 w_t -> 0，均值不被拉飛；
    3. 矩匹配 (Moment Matching)：在無效噪聲下方差嚴格守恆，不產生虛假高置信度。

    [修正/誠實揭露] 舊版帶有從未使用的死參數 `lr` 與 `huber_delta`
    （所有呼叫端都只靠預設值，函數本體也完全沒用到）——會誤導讀者以為
    更新步驟可調節。已刪除；實際更新演算法見 bayesian_v2.py。
    另補上 `estimated_markup` 透傳，使下游可覆寫 bayesian_v2 的預設加成率
    （舊版這條路徑完全無法覆寫，只能吃 0.08 硬編值）。
    """
    from .bayesian_v2 import robust_bayes_update
    kwargs = {}
    if estimated_markup is not None:
        kwargs["estimated_markup"] = estimated_markup
    state = robust_bayes_update(
        prior_mean=prior_reserve_mean,
        prior_std=prior_reserve_std,
        observed_offer=observed_offer,
        **kwargs,
    )
    return float(state.posterior_mean), float(state.posterior_std)


def compute_belief_confidence(prior_std: float, post_std: float, post_mean: float) -> float:
    """計算後驗信念收斂度指標 (Belief Convergence Metric).

    基於後驗變異係數 (CV = post_std / post_mean) 與相對先驗方差縮減率 (Variance Reduction Ratio)：
    CV 越小，說明後驗分佈越集中；方差縮減越明顯，說明觀測提供的信息增益越高。
    映射至 [0.50, 0.98] 區間，徹底替換無量綱的 1.0 - post_std 錯誤公式。

    [誠實揭露] 此映射為啟發式調校公式，係數 0.75 / 0.35 / 0.80 為編譯時固定常數，
    非來自資料擬合或訓練：raw = 0.75 + 0.35·variance_reduction − 0.80·CV，
    輸出 clip 至 [0.50, 0.98]。請勿把它誤讀成統計置信區間或模型校準度；
    它是供 UI 呈現的相對收斂度指標。
    """
    if post_mean <= 0 or post_std <= 0:
        return BELIEF_CONF_LO
    cv = post_std / post_mean
    variance_reduction = max(0.0, (prior_std - post_std) / (prior_std + 1e-6))
    raw_conf = BELIEF_CONF_BASE + BELIEF_CONF_VAR * variance_reduction - BELIEF_CONF_CV * cv
    return round(float(np.clip(raw_conf, BELIEF_CONF_LO, BELIEF_CONF_HI)), 3)


def recommend(solve_result, target_price=None, opens=None, scheds=None):
    """把歸一化結果轉成可讀建議。

    [修正] 舊版在 `solve()` 接受自訂 opens/scheds 時，`recommend()` 卻只
    重新呼叫 `model.strategy_grid()` 的**預設**網格來反查索引 —— 若呼叫端
    傳了自訂網格，best 索引會對到錯誤的數值。
    現在 recommend() 接受並沿用 solve() 使用的同一份 opens/scheds。
    """
    if target_price is None:
        # Omitted price means the simulated umbrella mandate, not a market quote.
        target_price = UMBRELLA.target
    i_open, i_sched = solve_result["best"]
    if opens is None or scheds is None:
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
