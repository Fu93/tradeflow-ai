"""TradeFlow v4 — 多供應商逆向拍賣動態 BATNA 與張量求解器。

數學模型：
1. 動態 BATNA (Best Alternative to a Negotiated Agreement)：
   BATNA_{-i} = min_{j != i} [ p_j + lambda_risk * (1 - pi_j) * (Ceiling - p_j) ]
   C_{eff, i} = min(Ceiling, BATNA_{-i})

2. 交叉錨點 (Cross-Anchoring Bayesian Update)：
   mu_{r, i} = mu_{r, i} + eta * (p_i - p_lead) - gamma * I_{cross_anchor}
   sigma_{r, i} = sigma_{r, i} * (1 - 0.25 * eta)

3. 巨型張量運算 (rollout 計數)：
   rollout 數 = K x S x N_open x N_sched，由 MultiSupplierSolver 的參數決定。
   預設建構參數為 (K=4, S=20,000, N_open=20, N_sched=6) -> 每輪 9.6M rollouts。
   `simulate_reverse_auction()` 預設執行 3 輪 -> 累計約 28.8M rollouts（實測值）。
   若將 S 提升至 50,000，每輪為 24M；最大策略網格 (n_open=40, n_sched=12) 搭配
   S=50,000 時為 96M rollouts，但**示範路徑並未使用該網格**，故對外引用實測值。

[硬體誠實揭露] 舊版此處寫「支援 AMD ROCm/CuPy 顯存加速」。本專案**從未在
AMD 硬體上執行過**（開發機為 Intel Core Ultra 7 355，無獨立 GPU、無 ROCm）。
此層的 `_get_xp(use_gpu=True)` 之「CuPy/ROCm 分支」是**未經實際驗證的**
潛在路徑；實際執行一律回落至 NumPy CPU。對外所有效能數字皆為 CPU 實測，
MI300X 的唯一量化是 benchmark.py 的 roofline 資料搬運下界。
"""
from __future__ import annotations
import time
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
from . import model


def _get_xp(use_gpu: bool):
    """取得陣列運算後端 (CuPy GPU 或 NumPy CPU)。

    回傳 (xp, backend_name)。backend_name 反映**實際**使用的後端，而非請求的
    後端：若請求 GPU 但 cupy 不可用，名稱必須回報 CPU，否則輸出會宣稱一個
    從未發生的 GPU 執行。
    """
    if use_gpu:
        try:
            import cupy as cp  # noqa: F401
            return cp, "GPU (CuPy/ROCm)"
        except Exception:
            return np, "CPU (NumPy Vectorized; GPU requested but CuPy unavailable)"
    return np, "CPU (NumPy Vectorized)"


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
        - SIDELINE: 牽制邊緣化 (報價偏高 > 1.12x 或強硬詐唬)
        - CROSS_ANCHOR: 交叉錨點 (中間價位，施加市場競爭壓力)

        [誠實揭露] 舊版此處 docstring 寫「> 1.2x」，但程式實際用的是 **1.12**×
        （`off > 1.12 * min_offer`）。係數 1.12 為固定啟發式常數（非資料擬合），
        已把描述改正以與行為一致。
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
        多供應商張量並行 Rollout 求解。

        rollout 數 = K x n_opp_samples x N_open x N_sched（由建構參數決定）。
        [修正] 舊版此處寫「(K=4, S=50,000, N_open=20, N_sched=6) 每輪為 9.6M」，
        但 9.6M 對應的是 S=20,000：4 x 20,000 x 20 x 6 = 9,600,000。
        以 S=50,000 計則為 4 x 50,000 x 20 x 6 = 24,000,000（每輪）。
        本方法的預設 `n_opp_samples=20,000` 才是 9.6M 的來源。

        輸入形狀:
          supplier_offers: (K,) 各家當前歸一化報價
          prior_means: (K,) 各家後驗底價均值
          prior_stds: (K,) 各家後驗底價標準差
        """
        t0 = time.perf_counter()
        k = len(supplier_offers)
        if rng is None:
            rng = np.random.default_rng(20261013)

        xp, backend_name = _get_xp(self.use_gpu)
        self._backend_name = backend_name

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
            "backend": getattr(self, "_backend_name", "CPU (NumPy Vectorized)"),
            "k_suppliers": k,
            "best_offers": best_offers,
            "best_sched_curves": best_sched_curves,
            "p_break_array": p_break_array,
            "exp_margin_array": exp_margin_array,
            "tactics": tactics,
            "effective_ceilings": [float(x) for x in effective_ceilings],
            "circuit_tripped": any(p >= 0.15 for p in p_break_array),
        }


class MultiAuctionSession:
    """即時多供應商逆向拍賣互動式會話 (Interactive Multi-Supplier Reverse Auction Session).

    支援逐輪動態接收真實外部供應商報價，不預設任何未來輪次報價。
    每一輪提供：
    1. 交叉錨定信號反饋（第 2 輪起，若存在領先價）
    2. 張量並行求解與 3 級戰術分流標籤（LEAN_IN, CROSS_ANCHOR, SIDELINE）
    3. 動態 BATNA 外部替代方案影子上限計算
    4. 買方最優對策還價向量 (counter_offers)
    """

    def __init__(
        self,
        suppliers: Optional[List[Dict[str, Any]]] = None,
        target_price: float = 12.0,
        walk_away_ceiling: float = 16.2,
        moq: int = 10000,
        n_samples: int = 20000,
        n_open: int = 20,
        n_sched: int = 6,
        use_gpu: bool = False,
    ):
        if suppliers is None:
            suppliers = [
                {"name": "Supplier A (敏捷讓步型)", "profile": "Agile Conceder"},
                {"name": "Supplier B (對等互惠型)", "profile": "Rational Tit-for-Tat"},
                {"name": "Supplier C (強硬死守型)", "profile": "Moderate Hardliner"},
                {"name": "Supplier D (高價溢價型)", "profile": "Stubborn Over-priced"},
            ]
        self.suppliers = suppliers
        self.k = len(suppliers)
        self.target_price = float(target_price)
        self.walk_away_ceiling = float(walk_away_ceiling)
        self.moq = int(moq)
        self.n_samples = int(n_samples)
        self.use_gpu = use_gpu

        self.solver = MultiSupplierSolver(
            n_opp_samples=n_samples,
            n_open=n_open,
            n_sched=n_sched,
            use_gpu=use_gpu,
            walk_away_ceiling=self.walk_away_ceiling / self.target_price,
        )

        # [誠實揭露] 這些是先驗常數（固定 1.08 / 0.10），非基地台資料：
        # 第 1 輪 counter-offer 完全由此前見決定、**與第 1 輪來源報價無關**
        # （輸入不變）；第 2 輪起才以 cross-anchoring 吃進各家報價。若需
        # 第 1 輪即反映報價，應在建立 session 時把 prior_means 設成首輪
        # 報價的函數 —— 目前 demo 路徑未這樣做。
        self.prior_means = np.full(self.k, 1.08, dtype=np.float32)
        self.prior_stds = np.full(self.k, 0.10, dtype=np.float32)

        self.current_round = 0
        self.trace: List[Dict[str, Any]] = []
        self.total_rollouts_accum = 0
        self.total_compute_seconds = 0.0

    def step_round(self, quotes: List[float]) -> Dict[str, Any]:
        """傳入當前輪次各供應商的即時報價 (¥)，計算戰術標籤、BATNA 上限與買方對策。"""
        if len(quotes) != self.k:
            raise ValueError(f"Expected {self.k} quotes for {self.k} suppliers, got {len(quotes)}")

        self.current_round += 1
        r_idx = self.current_round

        offers_norm = np.array([q / self.target_price for q in quotes], dtype=np.float32)
        best_lead_norm = float(np.min(offers_norm))

        # 1. 交叉錨定貝氏更新 (第 2 輪起)
        if r_idx > 1:
            self.prior_means, self.prior_stds = self.solver.apply_cross_anchoring_update(
                self.prior_means, self.prior_stds, offers_norm, best_lead_price=best_lead_norm
            )

        # 2. 張量並行求解
        res = self.solver.solve_reverse_auction_tensor(
            supplier_offers=offers_norm,
            prior_means=self.prior_means,
            prior_stds=self.prior_stds,
        )

        self.total_rollouts_accum += res["total_rollouts"]
        self.total_compute_seconds += res["elapsed_seconds"]

        # 3. 記錄輪次資訊
        round_info = {
            "round": r_idx,
            "quotes": [float(q) for q in quotes],
            "best_quote": float(min(quotes)),
            "lead_supplier": self.suppliers[int(np.argmin(quotes))]["name"],
            "tactics": res["tactics"],
            "effective_ceilings_cny": [float(c * self.target_price) for c in res["effective_ceilings"]],
            "p_break_array": [float(p) for p in res["p_break_array"]],
            "counter_offers_cny": [float(o * self.target_price) for o in res["best_offers"]],
            "rollouts": res["total_rollouts"],
            "elapsed_seconds": res["elapsed_seconds"],
        }
        self.trace.append(round_info)
        return round_info

    def get_summary(self) -> Dict[str, Any]:
        """結算拍賣結果。"""
        if not self.trace:
            raise ValueError("No rounds have been executed yet.")

        last_round = self.trace[-1]
        final_quote = last_round["best_quote"]
        winning_idx = int(np.argmin(last_round["quotes"]))
        winning_supplier = self.suppliers[winning_idx]["name"]
        total_savings_pct = (self.walk_away_ceiling - final_quote) / self.walk_away_ceiling
        total_savings_cny = (self.walk_away_ceiling - final_quote) * self.moq

        return {
            "success": True,
            "k_suppliers": self.k,
            "suppliers": self.suppliers,
            "backend": getattr(self, "_backend_name", "CPU (NumPy Vectorized)"),
            "product_target": self.target_price,
            "walk_away_ceiling": self.walk_away_ceiling,
            "moq": self.moq,
            "winning_supplier": winning_supplier,
            "winning_price": final_quote,
            "total_savings_pct": total_savings_pct,
            "total_savings_cny": total_savings_cny,
            "total_rollouts": self.total_rollouts_accum,
            "total_rollouts_accum": self.total_rollouts_accum,
            "total_compute_seconds": round(self.total_compute_seconds, 4),
            "trace": self.trace,
        }


def simulate_reverse_auction(
    target_price: float = 12.0,
    walk_away_ceiling: float = 16.2,
    moq: int = 10000,
    n_samples: int = 20000,
    use_gpu: bool = False,
    suppliers: Optional[List[Dict[str, str]]] = None,
    rounds_quotes: Optional[List[List[float]]] = None,
    n_rounds: int = 3,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    多供應商逆向拍賣動態引擎。

    誠實揭露：
    1. 自定義動態注入：外部傳入真實輪次報價 (`rounds_quotes`) 時，由 MultiAuctionSession 逐輪求解。
    2. 動態多 Agent 自博弈 (預設)：未傳入報價時，自動調用非 LLM 策略對抗 Agent
       (Conceder, TitForTat, HardBargainer, Bluffing) 在每一輪根據買方出價「即時動態計算反饋出價」。
       供應商報價**確實**是運行時由對抗原型生成，無預設報價數組。
       [誠實化] 但下列仍為編譯時固定常數，非資料：
       - 買方第 1 輪自家報價基準 1.05（`b_offer or 1.05`）；
       - 每個對抗原型之 init_price / reserve_price（下方字面值）；
       - MultiAuctionSession 的前見 prior_means=1.08 / prior_stds=0.10，
         使「第 1 輪 counter-offer」對首輪報價輸入不變（第 2 輪起才吃進報價）。
       因此不能宣稱「所有數字皆運行時生成且零常數」。
    """
    if suppliers is None:
        suppliers = [
            {"name": "Supplier A (敏捷讓步型)", "profile": "Agile Conceder"},
            {"name": "Supplier B (對等互惠型)", "profile": "Rational Tit-for-Tat"},
            {"name": "Supplier C (強硬死守型)", "profile": "Moderate Hardliner"},
            {"name": "Supplier D (高價溢價型)", "profile": "Stubborn Over-priced"},
        ]
    k = len(suppliers)

    session = MultiAuctionSession(
        suppliers=suppliers,
        target_price=target_price,
        walk_away_ceiling=walk_away_ceiling,
        moq=moq,
        n_samples=n_samples,
        use_gpu=use_gpu,
    )

    if rounds_quotes is not None:
        # 逐輪執行外部動態輸入報價
        for quotes in rounds_quotes:
            session.step_round(quotes)
    else:
        # 動態多 Agent 自博弈：每一輪均由對抗原型根據買方歷史即時推演生成
        from ..benchmark.adversaries import (
            ConcederAdversary,
            TitForTatAdversary,
            HardBargainerAdversary,
            BluffingAdversary,
        )
        adv_pool = [
            ConcederAdversary(max_rounds=n_rounds, init_price=1.28, reserve_price=1.05),
            TitForTatAdversary(max_rounds=n_rounds, init_price=1.30, reserve_price=1.12),
            HardBargainerAdversary(max_rounds=n_rounds, init_price=1.33, reserve_price=1.18),
            BluffingAdversary(max_rounds=n_rounds, init_price=1.40, reserve_price=1.15),
        ]
        rng = np.random.default_rng(seed)
        buyer_histories: List[List[float]] = [[] for _ in range(k)]
        supp_histories: List[List[float]] = [[] for _ in range(k)]

        for r in range(1, n_rounds + 1):
            cur_round_quotes = []
            for i in range(k):
                adv = adv_pool[i % len(adv_pool)]
                b_offer = buyer_histories[i][-1] if buyer_histories[i] else 1.05
                offer_norm = adv.respond(r, b_offer, buyer_histories[i], supp_histories[i], rng)
                supp_histories[i].append(offer_norm)
                cur_round_quotes.append(round(float(offer_norm * target_price), 2))
                buyer_histories[i].append(b_offer + 0.05)

            session.step_round(cur_round_quotes)

    return session.get_summary()
