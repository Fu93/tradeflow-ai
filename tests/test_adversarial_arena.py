"""單元測試：Phase 4 對抗式 Self-Play 競技場與 5 大策略對手。"""
import unittest
import numpy as np
from src.benchmark.adversaries import (
    HardBargainerAdversary,
    ConcederAdversary,
    TitForTatAdversary,
    BluffingAdversary,
    NoisyAdversary,
)
from src.benchmark.arena import AdversarialArena


class TestAdversarialArena(unittest.TestCase):
    def test_adversary_behavior_curves(self):
        """測試 5 類對抗代理的出價行為與邊界約束。"""
        rng = np.random.default_rng(42)

        # 1. Hard-Bargainer 前期死守價格
        hb = HardBargainerAdversary(kappa=3.5, init_price=1.30, reserve_price=1.15)
        p1 = hb.respond(1, 1.05, [1.05], [])
        p2 = hb.respond(2, 1.10, [1.05, 1.10], [p1])
        self.assertAlmostEqual(p1, 1.30, places=2)
        self.assertLess(p1 - p2, 0.03)  # 前期讓步極小

        # 2. Conceder 前期大幅讓步
        cc = ConcederAdversary(kappa=0.25, init_price=1.30, reserve_price=1.05)
        cp1 = cc.respond(1, 1.05, [1.05], [])
        cp2 = cc.respond(2, 1.10, [1.05, 1.10], [cp1])
        self.assertGreater(cc.init_price - cp1, 0.15)  # 首輪即急劇讓步
        self.assertGreater(cp1 - cp2, 0.02)  # 第 2 輪持續快速讓步

        # 3. Tit-for-Tat 鏡像買方讓步
        tft = TitForTatAdversary(beta=1.0, init_price=1.30, reserve_price=1.08)
        tp1 = tft.respond(1, 1.05, [1.05], [])
        tp2 = tft.respond(2, 1.12, [1.05, 1.12], [tp1])  # 買方讓步 0.07
        self.assertAlmostEqual(tp1 - tp2, 0.07, places=2)

        # 4. Bluffing 突變
        bluff = BluffingAdversary(bluff_drop_prob=1.0, init_price=1.32, reserve_price=1.10)
        bp_mid = bluff.respond(3, 1.10, [1.05, 1.08, 1.10], [1.32, 1.31])
        bp_final = bluff.respond(5, 1.15, [1.05, 1.08, 1.10, 1.12, 1.15], [1.32, 1.31, 1.30, 1.29], rng=rng)
        self.assertGreater(bp_mid, 1.25)
        self.assertAlmostEqual(bp_final, 1.10, places=2)

        # 5. Noisy 噪聲約束
        noisy = NoisyAdversary(noise_std=0.04, init_price=1.28, reserve_price=1.08)
        for r in range(1, 6):
            np_val = noisy.respond(r, 1.10, [1.10], [1.25], rng=rng)
            self.assertTrue(1.08 <= np_val <= 1.35)

    def test_single_match_execution(self):
        """測試單場對決結算指標。"""
        arena = AdversarialArena(max_rounds=5)
        conceder = ConcederAdversary(max_rounds=5)

        # Engine vs Conceder 應該高機率快速成交
        match_res = arena.run_match(conceder, agent_type="engine")
        self.assertTrue(match_res["deal_closed"])
        self.assertIsNotNone(match_res["final_price"])
        self.assertGreater(match_res["savings_pct"], 0.0)
        self.assertLessEqual(match_res["rounds"], 5)

    def test_tournament_and_markdown_export(self):
        """測試自博弈錦標賽與 Markdown 報表匯出。"""
        arena = AdversarialArena(max_rounds=5)
        # 快速自檢：每種對手 6 場
        results = arena.run_tournament(n_matches_per_adversary=6, seed=123)

        self.assertEqual(len(results), 5)
        for adv_name, agent_dict in results.items():
            self.assertIn("TradeFlow Engine", agent_dict)
            self.assertIn("Fixed 5%/rd Baseline", agent_dict)
            for agent_name, m in agent_dict.items():
                self.assertAlmostEqual(m["deal_rate"] + m["breakdown_rate"], 1.0, places=3)
                self.assertTrue(0.0 <= m["deal_rate"] <= 1.0)
                self.assertTrue(0.0 <= m["savings_pct"] <= 1.0)

        # 匯出 Markdown 報表
        md_table = arena.export_markdown_table(results)
        self.assertIn("Hard-Bargainer", md_table)
        self.assertIn("TradeFlow Engine", md_table)
        self.assertIn("Fixed 5%/rd Baseline", md_table)


if __name__ == "__main__":
    unittest.main()
