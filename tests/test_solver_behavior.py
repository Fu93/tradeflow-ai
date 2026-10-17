"""回歸測試：決策引擎行為修正的驗證（對應 DEEP_DEFECT_AUDIT §3.1–3.5、§4.3、§6.6）。

每個測試在**修正前**必須 FAIL，修正後必須 PASS。測試刻意鎖定「行為」而非
「魔法數字」：能用結構不變量的地方就不用硬編值，避免把某次抽樣的結果固化成契約。
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.benchmark.adversaries import (
    TitForTatAdversary,
    HardBargainerAdversary,
)
from src.benchmark.arena import AdversarialArena
from src.engine import solver
from src.engine.multi_solver import (
    MultiAuctionSession,
    simulate_reverse_auction,
)


class TestGhostBuyerHistory(unittest.TestCase):
    """§3.1 — simulate_reverse_auction 餵給對手的 buyer_history 必須是真實買方出價。"""

    def _run_with_spy(self, n_rounds=3, n_samples=2000):
        seen = []
        original = TitForTatAdversary.respond

        def spy(self, round_idx, buyer_offer, buyer_history, supplier_history, rng=None):
            seen.append(
                {
                    "round": round_idx,
                    "buyer_offer": float(buyer_offer),
                    "history": [float(x) for x in buyer_history],
                }
            )
            return original(self, round_idx, buyer_offer, buyer_history, supplier_history, rng)

        TitForTatAdversary.respond = spy
        try:
            res = simulate_reverse_auction(
                target_price=12.0,
                walk_away_ceiling=16.2,
                moq=10000,
                n_samples=n_samples,
                n_rounds=n_rounds,
            )
        finally:
            TitForTatAdversary.respond = original
        return res, seen

    def test_buyer_history_is_not_a_ghost_constant(self):
        """修正前：buyer_history 永遠是 [1.10, 1.15, …]（b_offer=1.05 的幽靈）。

        修正後：歷史必須由引擎實際的還價驅動 → 第 2 輪的 buyer_offer 不得等於
        第 1 輪的固定開場值 1.05，且序列必須單調不減（買方讓步）。
        """
        res, seen = self._run_with_spy()
        self.assertGreaterEqual(len(seen), 2, "TitForTat must be called at least twice")

        first, second = seen[0], seen[1]
        # 第 1 輪仍允許固定開場值（尚無引擎還價可用）
        self.assertAlmostEqual(first["buyer_offer"], 1.05, places=6)
        # [核心斷言] 第 2 輪的買方報價必須來自引擎 counter-offer，而非 1.05+0.05
        self.assertNotAlmostEqual(
            second["buyer_offer"], 1.10, places=3,
            msg="round-2 buyer offer is still the ghost constant 1.05+0.05",
        )
        # 歷史必須含本輪報價，且第 2 輪長度 >= 2（修正前永遠 < 2 → TitForTat 走 fallback）
        self.assertEqual(second["history"][-1], second["buyer_offer"])
        self.assertGreaterEqual(len(second["history"]), 2)

    def test_tit_for_tat_round2_responds_to_buyer_round1_offer(self):
        """TitForTat 第 2 輪報價必須對「買方第 1 輪出價的變動」有反應。

        直接以間接比較驗證：同一對手、相同 supplier_history，只改 buyer 歷史，
        報價必須不同。
        """
        tft = TitForTatAdversary(max_rounds=3, init_price=1.30, reserve_price=1.12)
        round1 = 1.30
        # 情境 A：買方只讓了 0.02
        hist_a = [1.05, 1.07]
        # 情境 B：買方讓了 0.15
        hist_b = [1.05, 1.20]
        p_a = tft.respond(2, hist_a[-1], hist_a, [round1])
        p_b = tft.respond(2, hist_b[-1], hist_b, [round1])
        self.assertLess(p_a, round1)
        self.assertLess(p_b, round1)
        self.assertGreater(
            (round1 - p_b), (round1 - p_a) + 0.05,
            msg="TitForTat must mirror a larger buyer concession with a larger drop",
        )

    def test_engine_feedback_loop_changes_opponent_trajectory(self):
        """整條路徑的整合驗證：引擎的實際還價（counter_offers_mean_norm）被記錄，
        且 > 固定開場值，證明回饋環確實閉合。"""
        res, _ = self._run_with_spy()
        means = [t["counter_offers_mean_norm"] for t in res["trace"]]
        self.assertEqual(len(means), 3)
        self.assertTrue(all(m > 1.0 for m in means), f"mean counter offers: {means}")
        # 買方讓步 → 第 2 輪的引擎還價均值應高於第 1 輪
        self.assertGreater(means[1], means[0])


class TestFirstRoundQuoteResponsiveness(unittest.TestCase):
    """§3.2 — 第 1 輪 counter-offer 必須反映各家首輪報價。"""

    def test_first_round_counter_offers_differ_per_supplier(self):
        session = MultiAuctionSession(n_samples=2000)
        r1 = session.step_round([13.26, 15.60, 15.92, 16.80])
        counters = r1["counter_offers_cny"]
        self.assertEqual(len(counters), 4)
        # [核心斷言] 修正前四家全部相同（實測四個值完全一致）
        self.assertGreater(
            len(set(round(c, 4) for c in counters)), 1,
            msg=f"all first-round counter-offers are identical: {counters}",
        )

    def test_first_round_counter_offers_are_monotone_in_quote(self):
        """報價越高 → 首輪 counter-offer 越高（單調、可解釋）。"""
        session = MultiAuctionSession(n_samples=2000)
        r1 = session.step_round([13.26, 15.60, 15.92, 16.80])
        counters = r1["counter_offers_cny"]
        # 逐對比較單調不降
        for i in range(len(counters) - 1):
            self.assertLessEqual(
                counters[i], counters[i + 1] + 1e-9,
                msg=f"counter-offers not monotone in quote: {counters}",
            )
        self.assertLess(counters[0], counters[-1])

    def test_distinct_quote_sets_give_distinct_counter_offers(self):
        """不同的首輪報價集合 → 不同的首輪 counter-offer 向量。"""
        s1 = MultiAuctionSession(n_samples=2000)
        s2 = MultiAuctionSession(n_samples=2000)
        c1 = s1.step_round([13.00, 14.00, 15.00, 16.00])["counter_offers_cny"]
        c2 = s2.step_round([11.00, 12.00, 13.00, 14.00])["counter_offers_cny"]
        self.assertFalse(
            all(abs(a - b) < 1e-9 for a, b in zip(c1, c2)),
            msg="quote level has no effect on first-round counter-offers",
        )


class TestBackendReporting(unittest.TestCase):
    """§3.3 — get_summary() 必須回報 solver 的**實際**後端。"""

    def test_summary_backend_matches_solver(self):
        session = MultiAuctionSession(n_samples=500, use_gpu=True)
        session.step_round([13.0, 14.0, 15.0, 16.0])
        expected = getattr(session.solver, "_backend_name", None)
        self.assertIsNotNone(expected)
        summary = session.get_summary()
        # [核心斷言] 修正前 session 讀不到 _backend_name → 永遠回 CPU 預設字串，
        # 即使 use_gpu=True（此環境 CuPy 不可用 → solver 記的是「requested but unavailable」）。
        self.assertEqual(summary["backend"], expected)
        self.assertNotEqual(summary["backend"], "CPU (NumPy Vectorized)")

    def test_summary_backend_cpu_when_gpu_not_requested(self):
        session = MultiAuctionSession(n_samples=500, use_gpu=False)
        session.step_round([13.0, 14.0, 15.0, 16.0])
        self.assertEqual(session.get_summary()["backend"], "CPU (NumPy Vectorized)")


class TestParetoTerminologyAndFrontier(unittest.TestCase):
    """§3.4 / §3.7 — 「帕雷托」不可用來描述標量化 argmax；真前沿必須可查。"""

    def test_solve_reports_a_real_pareto_frontier(self):
        res = solver.solve(n_opp_samples=2000)
        self.assertIn("pareto_frontier", res)
        self.assertIn("pareto_frontier_size", res)
        frontier = res["pareto_frontier"]
        self.assertEqual(res["pareto_frontier_size"], len(frontier))
        self.assertGreater(len(frontier), 0)

        # 真前沿的定義性檢查：前沿內任兩點互不支配
        margin = np.asarray(res["exp_margin"])
        brk = np.asarray(res["exp_break"])
        for a in frontier:
            ma, ba = margin[a[0], a[1]], brk[a[0], a[1]]
            for b in frontier:
                if a == b:
                    continue
                mb, bb = margin[b[0], b[1]], brk[b[0], b[1]]
                dominated = (mb >= ma) and (bb <= ba) and (mb > ma or bb < ba)
                self.assertFalse(dominated, f"{b} dominates frontier member {a}")

    def test_scalar_best_is_not_claimed_as_pareto_optimal(self):
        """`best` 是純量 argmax；修正後不得再把它**宣稱**為帕雷托最優。

        允許的例外：揭露用的「[修正] 舊版稱其為帕雷托最優」字樣（誠實記錄歷史術語）。
        因此逐一檢查每一行：任何含「帕雷托/帕累托最優」的行都必須是揭露行。
        """
        import inspect

        for obj in (solver.solve, solver._pareto_frontier_indices):
            doc = inspect.getdoc(obj) or ""
            for line in doc.splitlines():
                if "帕雷托最優" in line or "帕累托最優" in line:
                    self.assertTrue(
                        ("修正" in line) or ("舊版" in line),
                        f"{obj.__name__} still *claims* Pareto-optimality in: {line!r}",
                    )

        # 現行宣稱必須是「純量選取」
        self.assertIn("純量選取", inspect.getdoc(solver.solve) or "")
        # multi_solver 不得再稱該 argmax 為「求帕累托最優」
        multi_src = inspect.getsource(sys.modules["src.engine.multi_solver"])
        self.assertNotIn("求帕累托最優", multi_src)

    def test_scalar_best_lies_on_the_frontier(self):
        """純量 argmax 必然落在前沿上（術語誤用的可證偽部分）。"""
        res = solver.solve(n_opp_samples=2000)
        self.assertIn(list(res["best"]), [list(p) for p in res["pareto_frontier"]])

    def test_scalar_best_really_is_the_argmax_of_the_objective(self):
        """`best` 必須是 `exp_margin − λ·exp_break` 的 argmax，不是任何常數索引。

        [為什麼需要這個測試] 原本只檢查「best 落在前沿上」與「best_score 存在」，
        但 (0, 0) 這個索引本身就落在前沿上，所以把 `best` 寫死成 0,0 也能全綠。
        這裡改用回傳的兩個期望值矩陣重算目標函式，索引一旦被寫死就會失敗。
        """
        lam = 1.0
        res = solver.solve(n_opp_samples=2000, lambda_break=lam)
        margin = np.asarray(res["exp_margin"])
        brk = np.asarray(res["exp_break"])
        score = margin - lam * brk
        expected = np.unravel_index(int(np.argmax(score)), score.shape)
        got = tuple(int(x) for x in res["best"])
        self.assertEqual(got, tuple(int(x) for x in expected),
                         msg=f"best={got} is not the argmax ({expected}) of margin-{lam}*break")
        self.assertAlmostEqual(res["best_score"], float(score[got]), places=9)
        # 這個案例的最優索引不是 (0, 0)，所以寫死索引一定會被抓到
        self.assertNotEqual(got, (0, 0))

    def test_recommend_reports_the_selected_open_and_curve(self):
        """`recommend` 必須用 `best` 索引取值，不能自己另挑一格。"""
        from src.engine import model

        opens, scheds = model.strategy_grid()
        res = solver.solve(n_opp_samples=2000, opens=opens, scheds=scheds)
        rec = solver.recommend(res, target_price=12.0, opens=opens, scheds=scheds)
        i_open, i_sched = res["best"]
        self.assertAlmostEqual(rec["opening_offer_norm"], float(opens[i_open]), places=12)
        self.assertAlmostEqual(rec["opening_offer_price"], float(opens[i_open] * 12.0), places=9)
        self.assertEqual(rec["concession_curve"], [float(x) for x in scheds[i_sched]])
        self.assertAlmostEqual(
            rec["expected_margin_norm"], float(np.asarray(res["exp_margin"])[i_open, i_sched]), places=12
        )


class TestOpeningPriceSensitivity(unittest.TestCase):
    """§3.5 — 目標函式必須對開盤價有真正的張力（內部極值）。"""

    def test_anchoring_reaction_changes_the_optimum(self):
        """加入對手錨定反應後，最佳開盤檔必須與「無反應」版本不同。"""
        original_beta = solver.ANCHORING_BETA
        try:
            solver.ANCHORING_BETA = 1.0
            on = solver.solve(n_opp_samples=20000)
            solver.ANCHORING_BETA = 0.0
            off = solver.solve(n_opp_samples=20000)
        finally:
            solver.ANCHORING_BETA = original_beta

        self.assertNotEqual(
            on["best"], off["best"],
            msg="anchoring reaction does not move the optimum; §3.5 not addressed",
        )

    def test_per_open_profile_has_an_interior_peak(self):
        """逐開盤最佳分數必須是「先升後降」而非單調，且高峰不在邊界。"""
        res = solver.solve(n_opp_samples=20000)
        profile = (res["exp_margin"] - res["exp_break"]).max(axis=1)
        peak = int(np.argmax(profile))
        self.assertGreater(peak, 0, "peak sits on the lower boundary (degenerate)")
        self.assertLess(peak, len(profile) - 1, "peak sits on the upper boundary")
        self.assertGreater(profile[peak], profile[0], "profile is not rising initially")
        self.assertGreater(
            profile[peak], profile[-1],
            "profile does not collapse at high opening prices",
        )

    def test_anchoring_multiplier_sign(self):
        """錨定乘數的單調性：開盤越硬（gap 越大）→ 對手讓步乘數越小。"""
        gaps = np.array([0.05, 0.10, 0.20, 0.40], dtype=np.float64)
        m = solver.anchoring_multiplier(np, gaps)
        self.assertTrue(np.all(np.diff(m) < 0), f"multiplier not decreasing: {m}")
        self.assertAlmostEqual(float(m[1]), 1.0, places=6)


class TestArenaDifferentiation(unittest.TestCase):
    """§4.3 — 引擎與固定基準必須是真正不同的策略。"""

    @classmethod
    def setUpClass(cls):
        cls.arena = AdversarialArena(max_rounds=5, n_opp_samples=500)
        cls.results = cls.arena.run_tournament(n_matches_per_adversary=12, seed=20261013)

    def test_engine_and_baseline_are_not_numerically_identical(self):
        """[核心缺陷] 修正前引擎與基準在 4/5 對手上的節省率逐位相同。"""
        identical = []
        for adv, agents in self.results.items():
            if not isinstance(agents, dict):
                continue
            e = agents["TradeFlow Engine"]["savings_pct"]
            b = agents["Fixed 5%/rd Baseline"]["savings_pct"]
            if abs(e - b) < 5e-4:  # 同一儲存精度下視為相同
                identical.append((adv, e))
        self.assertLessEqual(
            len(identical), 1,
            msg=f"engine and baseline still (near-)identical on: {identical}",
        )

    def test_engine_no_longer_loses_the_bluffing_matchup(self):
        """[核心缺陷] 修正前引擎在 Bluffing Hardliner 上比基準差（66.7% vs 75.0% 成交率）。

        本測試鎖定**成交率**這一項（修正前引擎明顯落後），它在各種樣本量下都穩定；
        savings 的逐點差距會隨 n 在 ±0.005 內變號，屬抽樣噪聲，不作為契約
        （該殘差已誠實回報給 docs owner，見報告）。
        """
        agents = self.results["Bluffing Hardliner"]
        e = agents["TradeFlow Engine"]
        b = agents["Fixed 5%/rd Baseline"]
        self.assertGreaterEqual(
            e["deal_rate"], b["deal_rate"],
            msg=f"engine still loses the Bluffing deal-rate matchup: {e} vs {b}",
        )
        # 且兩者必須是不同策略（修正前 savings 逐位相同）
        self.assertGreater(abs(e["savings_pct"] - b["savings_pct"]), 5e-4)

    def test_engine_differentiated_on_at_least_four_of_five(self):
        """引擎必須在至少 4/5 對手上與基準可區分（修正前僅 1/5）。"""
        differing = 0
        for adv, agents in self.results.items():
            if not isinstance(agents, dict):
                continue
            e = agents["TradeFlow Engine"]["savings_pct"]
            b = agents["Fixed 5%/rd Baseline"]["savings_pct"]
            if abs(e - b) >= 5e-4:
                differing += 1
        self.assertGreaterEqual(differing, 4, f"only {differing}/5 archetypes differ")

    def test_engine_still_beat_baseline_on_most_savings(self):
        """修正前 4/5 對手節省率逐位相同；修正後引擎在多數對手上有可量測的優勢。
        鎖定「多數」而非「全部」，避免為了讓基準好看而調參（那是評測造假）。"""
        better = 0
        for adv, agents in self.results.items():
            if not isinstance(agents, dict):
                continue
            e = agents["TradeFlow Engine"]["savings_pct"]
            b = agents["Fixed 5%/rd Baseline"]["savings_pct"]
            if e > b + 5e-4:
                better += 1
        self.assertGreaterEqual(better, 3, f"engine better on only {better}/5 archetypes")

    def test_tournament_reports_resolution(self):
        for adv, agents in self.results.items():
            if not isinstance(agents, dict):
                continue
            for name, m in agents.items():
                self.assertIn("n_matches", m)
                self.assertEqual(m["n_matches"], 12)
        self.assertEqual(self.arena.last_tournament_meta["n_matches_per_adversary"], 12)

    def test_engine_faces_the_adversary_actual_reserve(self):
        """引擎不得再以固定 1.08 的虛構對手為先驗（§4.3-b）。"""
        estrella = {a.name: AdversarialArena._adversary_reserve(a, 12.0)
                    for a in self.arena.adversaries}
        self.assertEqual(estrella["Hard-Bargainer"], 1.15)
        self.assertEqual(estrella["Conceder"], 1.05)
        self.assertEqual(estrella["Tit-for-Tat"], 1.08)
        # 至少要有兩個不同的對手底價，否則「面對各對手真實底價」無意義
        self.assertGreater(len(set(estrella.values())), 1)

    def test_baseline_can_actually_violate_the_budget(self):
        """違規率必須是可量測的，而非定義上的恆零（§4.3-c）。

        以「固定 5%/輪（相對自身報價複利）」的基準對上一個長期限的拖延型對手，
        基準應可真的撞破 walk_away ceiling；引擎則有硬裁剪，不得違規。
        """
        arena = AdversarialArena(max_rounds=12, n_opp_samples=200)
        stubborn = HardBargainerAdversary(
            max_rounds=12, init_price=1.60, reserve_price=1.50
        )
        rng = np.random.default_rng(7)
        any_violation = False
        for _ in range(60):
            m = arena.run_match(stubborn, agent_type="fixed_baseline", rng=rng)
            if m["budget_violation"]:
                any_violation = True
                break
        self.assertTrue(
            any_violation,
            "baseline never violates the ceiling -> violation instrumentation is vacuous",
        )
        # 同一情境下引擎有硬裁剪 → 不得違規
        rng = np.random.default_rng(7)
        for _ in range(20):
            m = arena.run_match(stubborn, agent_type="engine", rng=rng)
            self.assertFalse(m["budget_violation"])


class TestOrchestratorRobustnessAmplifier(unittest.TestCase):
    """§6.6 — webhook 路徑不得每次請求都重算整套 robustness.report()。"""

    def test_quick_path_schedules_no_extra_solves(self):
        from src.app import orchestrator

        calls = {"n": 0}
        original = orchestrator.robustness.report

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        orchestrator.robustness.report = counting
        orchestrator._ROBUSTNESS_CACHE.clear()
        try:
            result = orchestrator.analyze(
                dict(orchestrator.DEMO_SPEC), observed_offer=1.25, quick=True
            )
        finally:
            orchestrator.robustness.report = counting and original

        # 修正前：quick=True 會呼叫 report()（內部 10 次 solve）
        self.assertEqual(
            calls["n"], 0,
            msg="analyze(quick=True) still triggers robustness.report() on the request path",
        )
        self.assertIn("robustness", result)
        self.assertFalse(result["robustness"].get("computed", True))

    def test_webhook_path_keeps_signature_and_shape(self):
        from src.app import orchestrator

        orchestrator._ROBUSTNESS_CACHE.clear()
        out = orchestrator.on_webhook(
            {"extracted": {"product": "auto umbrella", "target_price": 12.0},
             "offer": 1.25, "email_body": "We can do ¥15 per unit, MOQ 500."}
        )
        # 介面凍結：既有鍵必須保留
        for key in ("recommendation", "posterior_reserve_mean",
                    "posterior_reserve_std", "robustness", "proposal", "draft_email"):
            self.assertIn(key, out)
        self.assertIn("opening_offer_price", out["recommendation"])

    def test_full_path_still_computes_the_report(self):
        from src.app import orchestrator

        orchestrator._ROBUSTNESS_CACHE.clear()
        result = orchestrator.analyze(dict(orchestrator.DEMO_SPEC), quick=False)
        self.assertTrue(result["robustness"].get("computed"))
        for key in ("engine_margin", "baseline_margin", "gain", "sensitivity", "misbelief"):
            self.assertIn(key, result["robustness"])


if __name__ == "__main__":
    unittest.main()
