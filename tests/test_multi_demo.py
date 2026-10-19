"""單元測試：多供應商逆向拍賣模擬協同與動態 BATNA 收斂流程。"""
import unittest
from src.app.demo_data import self_play_bundle
from src.engine.multi_solver import simulate_reverse_auction


class TestMultiSupplierSimulation(unittest.TestCase):
    def test_reverse_auction_simulation_lifecycle(self):
        # 執行 4 供應商逆向拍賣動態多 Agent 自博弈模擬 (無寫死報價)
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            moq=10000,
            n_samples=5000,
            demo=True,
            self_play=self_play_bundle(),
        )

        self.assertTrue(res["success"])
        self.assertIn("Supplier A", res["winning_supplier"])

        # [修正 §7.4] 舊版把最終結果寫死（winning_price==12.60），任何合法行為
        # 變動都會誤判失敗，卻測不出結構缺陷。改為鎖定**關係不變量**：
        # 贏家成交價 = 最後一輪最低報價。Headline 節省相對首輪最低報價；
        # 相對 walk-away 上限的數字留在 savings_vs_ceiling_*，且仍為正。
        trace = res["trace"]
        final_best = trace[-1]["best_quote"]
        first_best = trace[0]["best_quote"]
        self.assertAlmostEqual(res["winning_price"], final_best)
        self.assertEqual(trace[-1]["lead_supplier"], res["winning_supplier"])
        self.assertEqual(res["total_savings_baseline"], "first_round_best_quote")
        self.assertAlmostEqual(
            res["total_savings_cny"], (first_best - final_best) * 10000
        )
        self.assertAlmostEqual(
            res["total_savings_pct"], (first_best - final_best) / first_best
        )
        self.assertAlmostEqual(
            res["savings_vs_ceiling_pct"], (16.20 - final_best) / 16.20
        )
        self.assertAlmostEqual(
            res["savings_vs_target_cny"], (12.0 - final_best) * 10000
        )
        # 成交價低於 walk-away 上限；headline 本身可以小於 10%。
        self.assertGreater(res["savings_vs_ceiling_pct"], 0.10)
        self.assertLess(res["winning_price"], 16.20)
        self.assertGreater(res["total_rollouts_accum"], 0)
        self.assertIn("p_deal_se", res["mc"])
        self.assertIn("cvar_cost", res["mc"])
        self.assertTrue(res["simulated"])
        self.assertEqual(res["data_class"], "simulated")

        # 檢驗 3 輪軌跡中各廠商動態 BATNA 上限是否逐步收縮
        self.assertEqual(len(trace), 3)

        # 驗證 Round 1 的戰術標籤（真實動態輸出）
        r1 = trace[0]
        # A 報價最低 → 首選深化
        self.assertEqual(r1["tactics"][0], "LEAN_IN")
        # D 報價 16.80 > walk-away 上限 16.20 → 邊緣化牽制
        self.assertEqual(r1["tactics"][3], "SIDELINE")
        # B、C 報價 (15.60, 15.92) 顯著高於最低價 13.26 (>1.12x) → 邊緣化牽制
        self.assertEqual(r1["tactics"][1], "SIDELINE")
        self.assertEqual(r1["tactics"][2], "SIDELINE")

        # [新增 §3.2 回歸] 首輪 counter-offer 必須反映各家首輪報價 —— 舊版
        # 四家完全相同（`prior_means=np.full(k,1.08)`），此斷言在修正前會失敗。
        cheapest = min(r1["quotes"])
        self.assertIsNotNone(r1["counter_offers_cny"][0])
        for offer, quote, blocked in zip(r1["counter_offers_cny"], r1["quotes"], r1["blocked"]):
            if blocked:
                self.assertIsNone(offer)
                continue
            self.assertLessEqual(offer, cheapest + 1e-6)
            self.assertLessEqual(offer, quote + 1e-6)
            self.assertLessEqual(offer, 16.20 + 1e-6)

        # 驗證非領先者的有效 BATNA 上限被交叉錨定壓制到低於初始 16.20
        self.assertLess(r1["effective_ceilings_cny"][1], 16.20)
        self.assertLess(r1["effective_ceilings_cny"][2], 16.20)
        self.assertLess(r1["effective_ceilings_cny"][3], 16.20)

        # 驗證每輪最低價持續下降（動態收斂）
        bests = [r["best_quote"] for r in trace]
        self.assertLess(bests[1], bests[0])
        self.assertLess(bests[2], bests[1])

    def test_interactive_multi_auction_session(self):
        """驗證真實即時逐輪交互會話 (MultiAuctionSession)：每一輪報價獨立傳入，不預設未來報價。"""
        from src.engine.multi_solver import MultiAuctionSession

        suppliers = [
            {"name": "Factory Alpha (寧波)", "profile": "Aggressive"},
            {"name": "Factory Beta (晉江)", "profile": "Conservative"},
        ]
        session = MultiAuctionSession(
            suppliers=suppliers,
            target_price=12.0,
            walk_away_ceiling=16.0,
            moq=5000,
            n_samples=2000,
        )

        # 第 1 輪獨立即時傳入
        r1 = session.step_round([15.00, 16.00])
        self.assertEqual(r1["round"], 1)
        self.assertEqual(r1["lead_supplier"], "Factory Alpha (寧波)")
        self.assertEqual(r1["tactics"][0], "LEAN_IN")

        # 第 2 輪獨立即時傳入
        r2 = session.step_round([14.20, 14.80])
        self.assertEqual(r2["round"], 2)

        # 第 3 輪獨立即時傳入（Beta 降價勝出）
        r3 = session.step_round([13.50, 13.10])
        self.assertEqual(r3["round"], 3)
        self.assertEqual(r3["lead_supplier"], "Factory Beta (晉江)")

        # 結算
        summary = session.get_summary()
        self.assertTrue(summary["success"])
        self.assertEqual(summary["winning_supplier"], "Factory Beta (晉江)")
        self.assertAlmostEqual(summary["winning_price"], 13.10)
        self.assertEqual(len(summary["trace"]), 3)

    def test_reverse_auction_adversary_dynamic_simulation(self):
        """驗證動態多 Agent 對抗自博弈模擬（無人工硬編碼報價）。"""
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            moq=10000,
            n_samples=2000,
            n_rounds=3,
            demo=True,
            self_play=self_play_bundle(),
        )
        self.assertTrue(res["success"])
        self.assertIn("winning_supplier", res)
        self.assertGreater(res["winning_price"], 0)
        self.assertEqual(len(res["trace"]), 3)


if __name__ == "__main__":
    unittest.main()
