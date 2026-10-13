"""TradeFlow v4 — 多供應商逆向拍賣動態 BATNA 與 96M 張量求解器。

數學模型：
1. 動態 BATNA (Best Alternative to a Negotiated Agreement)：
   BATNA_{-i} = min_{j != i} [ p_j + lambda_risk * (1 - pi_j) * (Ceiling - p_j) ]
   C_{eff, i} = min(Ceiling, BATNA_{-i})

2. 交叉錨點 (Cross-Anchoring Bayesian Update)：
   mu_{r, i} = mu_{r, i} + eta * (p_i - p_lead) - gamma * I_{cross_anchor}
   sigma_{r, i} = sigma_{r, i} * (1 - 0.25 * eta)

3. 巨型張量運算 (96,000,000 Rollouts)：
   張量維度 (K, S, N_open, N_sched) = (4 x 50,000 x 40 x 12)。
   支援 AMD ROCm/CuPy 顯存加速，並提供 NumPy CPU 高速向量化無縫降級。
"""
from __future__ import annotations
import time
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
from . import model


def _get_xp(use_gpu: bool):
    """取得陣列運算後端 (CuPy GPU 或 NumPy CPU)。"""
    if use_gpu:
        try:
            import cupy as cp
            return cp
        except Exception:
            pass
    return np


class MultiSupplierSolver:
    """多供應商逆向拍賣巨型張量求解器。"""

    def __init__(
        self,
        n_opp_samples: int = 50_000,
        n_open: int = 40,
        n_sched: int = 12,
        use_gpu: bool = False,
        walk_away_ceiling: float = 1.35,
        rounds: int = model.ROUNDS,
    ):
        self.n_opp_samples = n_opp_samples
        self.n_open = n_open
        self.n_sched = n_sched
        self.use_gpu = use_gpu
        self.walk_away_ceiling = walk_away_ceiling
        self.rounds = rounds

    def compute_dynamic_batna(
        self,
        current_offers: np.ndarray,
        deal_probs: np.ndarray,
        risk_lambda: float = 0.5,
    ) -> np.ndarray:
        """
        計算排除自身後的外部期望選項 (Dynamic BATNA_{-i})。
        current_offers: (K,) 各家當前歸一化報價
        deal_probs: (K,) 各家達成協議估計機率
        回傳: (K,) 各供應商對應之有效上限價格 C_{eff, i}
        """
        k = len(current_offers)
        effective_ceilings = np.full(k, self.walk_away_ceiling, dtype=np.float32)
        if k <= 1:
            return effective_ceilings

        # 計算每家保留期望值
        # reserve_val = p_j + lambda * (1 - pi_j) * (Ceiling - p_j)
        preserves = current_offers + risk_lambda * (1.0 - deal_probs) * (self.walk_away_ceiling - current_offers)

        for i in range(k):
            # 排除 i 之後其餘供應商之最優值
            other_indices = [j for j in range(k) if j != i]
            batna_minus_i = float(np.min(preserves[other_indices]))
            effective_ceilings[i] = min(self.walk_away_ceiling, batna_minus_i)

        return effective_ceilings

    def apply_cross_anchoring_update(
        self,
        prior_means: np.ndarray,
        prior_stds: np.ndarray,
        supplier_offers: np.ndarray,
        best_lead_price: float,
        eta: float = 0.30,
        gamma: float = 0.04,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        交叉錨點信號反饋後的底價後驗更新。
        向高於領先價的對手釋放市場競爭信號，使其底價預期受到壓制。
        """
        k = len(supplier_offers)
        post_means = prior_means.copy()
        post_stds = prior_stds.copy()

        for i in range(k):
            gap_to_lead = supplier_offers[i] - best_lead_price
            if gap_to_lead > 0.01:
                # 存在領先對手，施加競爭壓力
                post_means[i] = prior_means[i] + eta * gap_to_lead - gamma
                post_stds[i] = max(0.01, prior_stds[i] * (1.0 - 0.25 * eta))
            else:
                # 領先者維持常規更新
                signal = supplier_offers[i] - 1.0
                post_means[i] = prior_means[i] + eta * signal
                post_stds[i] = max(0.01, prior_stds[i] * (1.0 - 0.20 * eta))

        return post_means, post_stds

    def classify_tactics(
        self,
        supplier_offers: np.ndarray,
        post_means: np.ndarray,
    ) -> List[str]:
        """
        將供應商劃分為三大博弈戰術類別：
        - LEAN_IN: 首選深化 (全場最優且底價樂觀)
        - SIDELINE: 牽制邊緣化 (報價偏高 > 1.2x 或強硬詐唬)
        - CROSS_ANCHOR: 交叉錨點 (中間價位，施加市場競爭壓力)
        """
        k = len(supplier_offers)
        if k == 0:
            return []
        min_offer = float(np.min(supplier_offers))
        tactics = []
        for i in range(k):
            off = supplier_offers[i]
            if abs(off - min_offer) < 1e-4:
                tactics.append("LEAN_IN")
            elif off > 1.12 * min_offer or off >= self.walk_away_ceiling:
                tactics.append("SIDELINE")
            else:
                tactics.append("CROSS_ANCHOR")
        return tactics

    def solve_reverse_auction_tensor(
        self,
        supplier_offers: np.ndarray,
        prior_means: np.ndarray,
        prior_stds: np.ndarray,
        rng: Optional[np.random.Generator] = None,
    ) -> Dict[str, Any]:
        """
        多供應商 96,000,000 次 Rollout 巨型張量運算求解。
        輸入形狀:
          supplier_offers: (K,) 各家當前歸一化報價
          prior_means: (K,) 各家後驗底價均值
          prior_stds: (K,) 各家後驗底價標準差
        """
        t0 = time.perf_counter()
        k = len(supplier_offers)
        if rng is None:
            rng = np.random.default_rng(20261013)

        xp = _get_xp(self.use_gpu)

        # 候選策略網格 (N_open=40, N_sched=12)
        opens, scheds = model.strategy_grid(n_open=self.n_open, n_sched=self.n_sched)
        n_open = len(opens)
        n_sched = len(scheds)
        s = self.n_opp_samples

        total_rollouts = k * s * n_open * n_sched

        # 1. 抽樣 K 組供應商樣本 -> 形狀 (K, S)
        reserves = np.zeros((k, s), dtype=np.float32)
        urgencies = np.zeros((k, s), dtype=np.float32)
        bluffs = np.zeros((k, s), dtype=np.float32)
        concs = np.zeros((k, s), dtype=np.float32)

        for i in range(k):
            r, u, b, c = model.sample_opponents(
                rng, s, reserve_mean=prior_means[i], reserve_std=prior_stds[i]
            )
            reserves[i] = r.astype(np.float32)
            urgencies[i] = u.astype(np.float32)
            bluffs[i] = b.astype(np.float32)
            concs[i] = c.astype(np.float32)

        # 2. 轉移至張量後端 (CuPy GPU 或 NumPy CPU) - 使用 float32 顯存緊湊化
        res_t = xp.asarray(reserves)[:, :, None, None]      # (K, S, 1, 1)
        urg_t = xp.asarray(urgencies)[:, :, None, None]     # (K, S, 1, 1)
        blf_t = xp.asarray(bluffs)[:, :, None, None]        # (K, S, 1, 1)
        cnc_t = xp.asarray(concs)[:, :, None, None]         # (K, S, 1, 1)

        op_t = xp.asarray(opens, dtype=xp.float32)[None, None, :, None]       # (1, 1, O, 1)
        sc_t = xp.asarray(scheds, dtype=xp.float32)[None, None, None, :, :]   # (1, 1, 1, C, ROUNDS)

        # 初始價差張量廣播 -> (K, S, O, C)
        gap = xp.abs(res_t - op_t)
        gap = xp.broadcast_to(gap, (k, s, n_open, n_sched)).copy()
        paid = xp.broadcast_to(op_t, (k, s, n_open, n_sched)).copy()

        # 3. 5 輪向量化併發 Rollout
        for r_idx in range(self.rounds):
            step_buyer = sc_t[..., r_idx] * gap
            paid = paid + step_buyer
            gap = gap - step_buyer
            step_opp = cnc_t * urg_t * (1.0 - 0.45 * blf_t) * gap
            gap = gap - step_opp
            gap = xp.maximum(gap, 0.0)

        # 4. 結算成交機率與破局率
        tol = 0.02
        p_deal = xp.exp(-xp.maximum(gap - tol, 0.0) / 0.05)
        first_gap = xp.abs(res_t - op_t)
        p_break = xp.clip(1.0 - p_deal + (first_gap > 0.32) * (0.20 + 0.30 * blf_t), 0.0, 1.0)

        # 5. 計算期望邊際與破局機率 (沿 S 樣本軸平均) -> (K, O, C)
        payoff = p_deal * (self.walk_away_ceiling - paid) - p_break * 0.10
        exp_margin = payoff.mean(axis=1)    # (K, O, C)
        exp_break = p_break.mean(axis=1)     # (K, O, C)

        # 6. 為每家供應商求帕累托最優 (邊際 - 1.0 * 破局)
        scores = exp_margin - 1.0 * exp_break
        if hasattr(scores, "get"):  # CuPy
            scores_np = scores.get()
            exp_margin_np = exp_margin.get()
            exp_break_np = exp_break.get()
        else:
            scores_np = np.asarray(scores)
            exp_margin_np = np.asarray(exp_margin)
            exp_break_np = np.asarray(exp_break)

        best_offers = []
        best_sched_curves = []
        p_break_array = []
        exp_margin_array = []

        for i in range(k):
            flat_idx = int(np.argmax(scores_np[i]))
            i_open, i_sched = divmod(flat_idx, n_sched)
            best_offers.append(float(opens[i_open]))
            best_sched_curves.append([float(x) for x in scheds[i_sched]])
            p_break_array.append(float(exp_break_np[i, i_open, i_sched]))
            exp_margin_array.append(float(exp_margin_np[i, i_open, i_sched]))

        dt = time.perf_counter() - t0

        # 清理 CuPy 顯存池以防止洩漏
        if self.use_gpu:
            try:
                import cupy as cp
                cp.get_default_memory_pool().free_all_blocks()
            except Exception:
                pass

        # 戰術分類
        tactics = self.classify_tactics(supplier_offers, prior_means)

        # 動態 BATNA
        deal_probs_approx = 1.0 - np.array(p_break_array)
        effective_ceilings = self.compute_dynamic_batna(supplier_offers, deal_probs_approx)

        return {
            "total_rollouts": total_rollouts,
            "elapsed_seconds": round(dt, 4),
            "backend": "GPU (CuPy/ROCm)" if self.use_gpu else "CPU (NumPy Vectorized)",
            "k_suppliers": k,
            "best_offers": best_offers,
            "best_sched_curves": best_sched_curves,
            "p_break_array": p_break_array,
            "exp_margin_array": exp_margin_array,
            "tactics": tactics,
            "effective_ceilings": [float(x) for x in effective_ceilings],
            "circuit_tripped": any(p >= 0.15 for p in p_break_array),
        }


def simulate_reverse_auction(
    target_price: float = 12.0,
    walk_away_ceiling: float = 16.2,
    moq: int = 10000,
    n_samples: int = 20000,
    use_gpu: bool = False,
) -> Dict[str, Any]:
    """
    執行端到端 4 供應商逆向拍賣多輪協同模擬。
    展示動態 BATNA_{-i} 交叉壓制、3 級戰術分流與張量求解全流程。
    """
    suppliers = [
        {"name": "Supplier A (浙江頂尖雨具製造)", "profile": "Agile Conceder"},
        {"name": "Supplier B (深圳宏達實業)", "profile": "Rational Tit-for-Tat"},
        {"name": "Supplier C (義烏博創商貿)", "profile": "Moderate Hardliner"},
        {"name": "Supplier D (東莞榮耀五金)", "profile": "Stubborn Over-priced"},
    ]
    k = len(suppliers)

    # 3 輪多廠商報價演變數據 (金額 ¥)
    rounds_quotes = [
        [15.20, 15.60, 16.00, 17.20],  # Round 1
        [14.00, 14.40, 15.20, 16.80],  # Round 2
        [12.80, 13.60, 14.80, 16.80],  # Round 3
    ]

    solver = MultiSupplierSolver(
        n_opp_samples=n_samples,
        n_open=20,
        n_sched=6,
        use_gpu=use_gpu,
        walk_away_ceiling=walk_away_ceiling / target_price,
    )

    prior_means = np.full(k, 1.08, dtype=np.float32)
    prior_stds = np.full(k, 0.10, dtype=np.float32)

    total_rollouts_accum = 0
    total_compute_seconds = 0.0
    trace = []

    for r_idx, quotes in enumerate(rounds_quotes, start=1):
        offers_norm = np.array([q / target_price for q in quotes], dtype=np.float32)
        best_lead_norm = float(np.min(offers_norm))

        # 1. 交叉錨定貝氏更新
        if r_idx > 1:
            prior_means, prior_stds = solver.apply_cross_anchoring_update(
                prior_means, prior_stds, offers_norm, best_lead_price=best_lead_norm
            )

        # 2. 96M/張量並行求解
        res = solver.solve_reverse_auction_tensor(
            supplier_offers=offers_norm,
            prior_means=prior_means,
            prior_stds=prior_stds,
        )

        total_rollouts_accum += res["total_rollouts"]
        total_compute_seconds += res["elapsed_seconds"]

        # 3. 記錄輪次軌跡
        round_info = {
            "round": r_idx,
            "quotes": quotes,
            "best_quote": min(quotes),
            "lead_supplier": suppliers[int(np.argmin(quotes))]["name"],
            "tactics": res["tactics"],
            "effective_ceilings_cny": [c * target_price for c in res["effective_ceilings"]],
            "p_break_array": res["p_break_array"],
            "counter_offers_cny": [o * target_price for o in res["best_offers"]],
            "rollouts": res["total_rollouts"],
            "elapsed_seconds": res["elapsed_seconds"],
        }
        trace.append(round_info)

    # 4. 結算
    final_quote = min(rounds_quotes[-1])
    winning_idx = int(np.argmin(rounds_quotes[-1]))
    winning_supplier = suppliers[winning_idx]["name"]
    total_savings_pct = (walk_away_ceiling - final_quote) / walk_away_ceiling
    total_savings_cny = (walk_away_ceiling - final_quote) * moq

    return {
        "success": True,
        "product_target": target_price,
        "walk_away_ceiling": walk_away_ceiling,
        "moq": moq,
        "winning_supplier": winning_supplier,
        "winning_price": final_quote,
        "total_savings_pct": total_savings_pct,
        "total_savings_cny": total_savings_cny,
        "total_rollouts_accum": total_rollouts_accum,
        "total_compute_seconds": round(total_compute_seconds, 4),
        "trace": trace,
    }
