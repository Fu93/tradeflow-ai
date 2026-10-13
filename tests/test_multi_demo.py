"""單元測試：多供應商逆向拍賣模擬協同與動態 BATNA 收斂流程。"""
import unittest
from src.engine.multi_solver import simulate_reverse_auction


class TestMultiSupplierSimulation(unittest.TestCase):
    def test_reverse_auction_simulation_lifecycle(self):
        # 執行 4 供應商逆向拍賣模擬
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            moq=10000,
            n_samples=5000,
        )

        self.assertTrue(res["success"])
        self.assertIn("Supplier A", res["winning_supplier"])
        self.assertAlmostEqual(res["winning_price"], 12.80)
        self.assertGreater(res["total_savings_pct"], 0.20)  # > 20% savings
        self.assertEqual(res["total_savings_cny"], (16.20 - 12.80) * 10000)
        self.assertGreater(res["total_rollouts_accum"], 5_000_000)

        # 檢驗 3 輪軌跡中各廠商動態 BATNA 上限是否逐步收縮
        trace = res["trace"]
        self.assertEqual(len(trace), 3)

        # 驗證 Round 1 的戰術標籤
        r1 = trace[0]
        self.assertEqual(r1["tactics"][0], "LEAN_IN")        # 最低價 A
        self.assertEqual(r1["tactics"][1], "CROSS_ANCHOR")   # 中段 B
        self.assertEqual(r1["tactics"][2], "CROSS_ANCHOR")   # 中段 C
        self.assertEqual(r1["tactics"][3], "SIDELINE")       # 超過上限 D

        # 驗證對 B 與 C 的有效 BATNA 上限是否低於初始 16.20
        self.assertLess(r1["effective_ceilings_cny"][1], 16.20)
        self.assertLess(r1["effective_ceilings_cny"][2], 16.20)


if __name__ == "__main__":
    unittest.main()
