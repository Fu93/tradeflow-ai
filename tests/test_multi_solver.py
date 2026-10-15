"""單元測試：Phase 3 多供應商逆向拍賣動態 BATNA 與張量求解器。

註：rollout 數 = K x n_opp_samples x n_open x n_sched，由建構參數決定；
本測試刻意使用小樣本 (n_opp_samples=5,000) 以求快速回歸。
"""
import unittest
import numpy as np
from src.engine.multi_solver import MultiSupplierSolver


class TestMultiSolver(unittest.TestCase):
    def setUp(self):
        self.solver = MultiSupplierSolver(
            n_opp_samples=5_000,
            n_open=20,
            n_sched=6,
            use_gpu=False,
            walk_away_ceiling=1.35,
        )

    def test_dynamic_batna_computation(self):
        """測試動態 BATNA 是否根據競爭對手實時報價有效收緊上限。"""
        # 3 家供應商報價：A=1.15 (領先), B=1.28, C=1.32
        offers = np.array([1.15, 1.28, 1.32], dtype=np.float32)
        deal_probs = np.array([0.90, 0.70, 0.60], dtype=np.float32)

        ceilings = self.solver.compute_dynamic_batna(offers, deal_probs, risk_lambda=0.5)

        self.assertEqual(len(ceilings), 3)
        # 對 B 與 C 而言，外部替代方案來自 A (1.15)，因此其上限應收緊至低於初始 1.35
        self.assertLess(ceilings[1], 1.35)
        self.assertLess(ceilings[2], 1.35)
        # 對 A 而言，外部最優來自 B (1.28)，因此 A 的上限相對寬鬆
        self.assertGreater(ceilings[0], ceilings[1])

    def test_cross_anchoring_update(self):
        """測試交叉錨點信號反饋是否壓制高價對手的底價後驗。"""
        priors = np.array([1.10, 1.10, 1.10], dtype=np.float32)
        stds = np.array([0.10, 0.10, 0.10], dtype=np.float32)
        offers = np.array([1.15, 1.25, 1.30], dtype=np.float32)

        post_means, post_stds = self.solver.apply_cross_anchoring_update(
            priors, stds, offers, best_lead_price=1.15, eta=0.30, gamma=0.04
        )

        self.assertEqual(len(post_means), 3)
        # 領先者 (1.15) 未受交叉錨點壓制，高價者受到 gamma 壓制
        self.assertLess(post_stds[1], 0.10)
        self.assertLess(post_stds[2], 0.10)

    def test_tactics_classification(self):
        """測試三大戰術分類：LEAN_IN, SIDELINE, CROSS_ANCHOR。"""
        offers = np.array([1.10, 1.20, 1.40], dtype=np.float32)
        means = np.array([1.08, 1.12, 1.18], dtype=np.float32)

        tactics = self.solver.classify_tactics(offers, means)
        self.assertEqual(tactics[0], "LEAN_IN")       # 最低價
        self.assertEqual(tactics[1], "CROSS_ANCHOR")  # 中間價
        self.assertEqual(tactics[2], "SIDELINE")      # 超過最低價 20% (1.40 > 1.32)

    def test_tensor_solve_execution(self):
        """測試多供應商張量並行求解與無 NaN 數值穩定性。"""
        offers = np.array([1.18, 1.22, 1.28, 1.15], dtype=np.float32)
        means = np.array([1.08, 1.10, 1.12, 1.05], dtype=np.float32)
        stds = np.array([0.10, 0.10, 0.10, 0.08], dtype=np.float32)

        res = self.solver.solve_reverse_auction_tensor(offers, means, stds)

        self.assertEqual(res["k_suppliers"], 4)
        self.assertEqual(res["total_rollouts"], 4 * 5_000 * 20 * 6)
        self.assertEqual(len(res["best_offers"]), 4)
        self.assertEqual(len(res["best_sched_curves"]), 4)
        self.assertEqual(len(res["p_break_array"]), 4)

        # 數值穩定性驗證
        for p in res["p_break_array"]:
            self.assertFalse(np.isnan(p))
            self.assertTrue(0.0 <= p <= 1.0)

        for m in res["exp_margin_array"]:
            self.assertFalse(np.isnan(m))

    def test_circuit_breaker_tripped(self):
        """測試當對手報價極高引發破局率超標時，觸發熔斷旗標。"""
        # 供應商報出極端高價 (1.34，接近上限 1.35)
        offers = np.array([1.34, 1.34], dtype=np.float32)
        means = np.array([1.25, 1.25], dtype=np.float32)
        stds = np.array([0.15, 0.15], dtype=np.float32)

        res = self.solver.solve_reverse_auction_tensor(offers, means, stds)
        self.assertTrue(res["circuit_tripped"])


if __name__ == "__main__":
    unittest.main()
