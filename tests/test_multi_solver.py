"""Multi-supplier coupled expectimax.

`total_rollouts` counts scenario-offer cells scored, including sweeps.
It is not K x n_opp_samples x n_open x n_sched.
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

    def test_first_round_priors_are_anchored_to_quotes(self):
        """[§3.2 回歸] 首輪 step_round 必須讓各家先驗隨報價分化，且單調。"""
        from src.engine.multi_solver import MultiAuctionSession

        session = MultiAuctionSession(n_samples=2000)
        r1 = session.step_round([13.26, 15.60, 15.92, 16.80])

        counters = r1["counter_offers_cny"]
        cheapest = min(r1["quotes"])
        for offer in counters:
            self.assertLessEqual(offer, cheapest + 1e-6)
        # Different quotes must move the reserve beliefs even when the
        # outside option caps every counter at the cheapest quote.
        priors = [round(float(x), 4) for x in session.prior_means]
        self.assertGreater(len(set(priors)), 1, msg=f"priors did not separate: {priors}")

        # 先驗必須落在合理支撐區間（不高於本家報價、低於 walk-away 上限）
        priors = [float(x) for x in session.prior_means]
        self.assertTrue(all(p > 0.5 for p in priors), f"implausible priors: {priors}")
        self.assertTrue(all(p < 1.35 for p in priors), f"priors exceed ceiling: {priors}")

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
        self.assertGreater(res["total_rollouts"], 0)
        self.assertIn("p_deal", res["mc"])
        self.assertIn("p_deal_se", res["mc"])
        self.assertIn("cvar_cost", res["mc"])
        self.assertIn("regret", res["mc"])
        self.assertEqual(len(res["best_offers"]), 4)
        self.assertEqual(len(res["best_sched_curves"]), 4)
        self.assertEqual(len(res["p_break_array"]), 4)

        # 數值穩定性驗證
        for p in res["p_break_array"]:
            self.assertFalse(np.isnan(p))
            self.assertTrue(0.0 <= p <= 1.0)

        for m in res["exp_margin_array"]:
            self.assertFalse(np.isnan(m))

    def test_summary_reports_every_savings_baseline_consistently(self):
        """[誠實揭露回歸] 三個節省率必須各自與其基準一致，且可從結果重算。

        原本只驗到 `total_savings_pct`（相對 walk-away 上限）。但那個 22.2% 最
        容易被讀成「比供應商報價省 22%」，因此 get_summary 另外回報相對首輪
        報價與相對目標價的兩個基準；這三組數字任一被寫死都不會被既有測試抓到。
        """
        from src.engine.multi_solver import MultiAuctionSession

        session = MultiAuctionSession(
            target_price=12.0, walk_away_ceiling=16.2, moq=10_000, n_samples=5000,
        )
        session.step_round([13.26, 15.60, 15.92, 16.80])
        session.step_round([12.98, 15.10, 15.60, 16.40])
        s = session.get_summary()

        ceiling = s["walk_away_ceiling"]
        target = s["product_target"]
        moq = s["moq"]
        final = s["winning_price"]
        first = s["first_round_best_quote"]

        self.assertEqual(s["total_savings_baseline"], "first_round_best_quote")
        self.assertAlmostEqual(s["total_savings_baseline_value"], first)
        self.assertAlmostEqual(s["total_savings_pct"], (first - final) / first)
        self.assertAlmostEqual(s["total_savings_cny"], (first - final) * moq)
        self.assertAlmostEqual(s["savings_vs_first_quote_pct"], s["total_savings_pct"])
        self.assertAlmostEqual(s["savings_vs_first_quote_cny"], s["total_savings_cny"])
        self.assertAlmostEqual(s["savings_vs_target_pct"], (target - final) / target)
        self.assertAlmostEqual(s["savings_vs_target_cny"], (target - final) * moq)
        self.assertAlmostEqual(s["savings_vs_ceiling_pct"], (ceiling - final) / ceiling)
        self.assertAlmostEqual(s["savings_vs_ceiling_cny"], (ceiling - final) * moq)
        # The headline is not the ceiling figure, and a miss versus target stays negative.
        self.assertNotAlmostEqual(s["total_savings_pct"], s["savings_vs_ceiling_pct"])
        self.assertLess(s["savings_vs_target_pct"], 0.0)
        self.assertLess(s["savings_vs_target_cny"], 0.0)

    def test_negative_headline_is_not_clamped(self):
        """A close above the first quote is a negative headline, not zero."""
        from src.engine.multi_solver import MultiAuctionSession

        session = MultiAuctionSession(
            target_price=12.0, walk_away_ceiling=16.2, moq=10_000, n_samples=200,
        )
        session.step_round([10.00, 11.00, 12.00, 13.00])
        session.step_round([12.50, 13.00, 13.40, 14.00])
        s = session.get_summary()
        self.assertLess(s["total_savings_pct"], 0.0)
        self.assertLess(s["savings_vs_first_quote_cny"], 0.0)
        self.assertAlmostEqual(s["savings_vs_target_cny"], (12.0 - 12.50) * 10_000)
        self.assertGreater(s["savings_vs_ceiling_pct"], 0.0)

    def test_circuit_breaker_follows_the_risk_cap(self):
        """A tight user cap blocks the supplier we would push. The default cap is looser."""
        offers = np.array([1.34, 1.34], dtype=np.float32)
        means = np.array([1.25, 1.25], dtype=np.float32)
        stds = np.array([0.15, 0.15], dtype=np.float32)

        loose = self.solver.solve_reverse_auction_tensor(offers, means, stds)
        tight = MultiSupplierSolver(
            n_opp_samples=5_000,
            n_open=20,
            n_sched=6,
            use_gpu=False,
            walk_away_ceiling=1.35,
            risk_tolerance=0.02,
        )
        blocked = tight.solve_reverse_auction_tensor(offers, means, stds)
        self.assertTrue(blocked["circuit_tripped"])
        self.assertLessEqual(int(loose["circuit_tripped"]), int(blocked["circuit_tripped"]))


if __name__ == "__main__":
    unittest.main()
