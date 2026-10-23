"""task-2 / P1 誠實性回歸測試：模具費減免守門不得被 Triple Logrolling 繞過。

缺陷（DEEP_DEFECT_AUDIT §5.5 後續追加，P1）：

`LogrollingEngine.evaluate` 的策略 4「全維度超級置換 (Triple Logrolling)」
硬寫 `mold_exempt=True`，卻**沒有**像策略 2 那樣檢查
`mandate.mold_fee_exemption_demanded`。於是當對方從未報出模具費
（`main.py:1371-1374` 已據此把該旗標設為 False，意圖關閉該主張）時：

  1. 引擎仍產生宣稱「模具費全額減免」的候選；
  2. 它用 `mandate.estimated_mold_fee`（預設 3000、LIMITATIONS §十三自述
     為未校準選定值）乘上一筆**不存在的**減免，把虛構收益灌進
     `buyer_surplus_value` 並贏得排名（修前實測 4824.59 vs 第二名 1247.53）；
  3. `buyer_script_zh` 對外承諾「由貴廠全額減免」，並經 `logrolling_proposal`
     進入 API 回應。

本檔是這條路徑的守衛。所有案例皆在修正前 FAIL、修正後 PASS。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.legacy.logrolling import (
    CorporateMandate,
    LogrollingEngine,
    LogrollingProposal,
    SupplierCurrentOffer,
    TradeoffType,
)


def _mandate(**overrides) -> CorporateMandate:
    """與測試主檔一致的基準 mandate，可覆寫個別欄位。"""
    base = dict(
        target_price=12.0,
        walk_away_ceiling=16.0,
        baseline_lead_days=25,
        max_acceptable_lead_days=35,
        min_desired_lead_days=15,
        lead_day_shadow_value=0.15,
        baseline_deposit_pct=0.30,
        max_allowed_deposit_pct=0.50,
        buyer_capital_cost_rate=0.08,
        supplier_capital_relief_rate=0.22,
        baseline_moq=500,
        max_allowed_moq=1200,
        mold_fee_exemption_demanded=True,
        estimated_mold_fee=3000.0,
        allowed_tradeoffs=[
            TradeoffType.DEPOSIT_FOR_LEADTIME,
            TradeoffType.VOLUME_FOR_PRICE,
            TradeoffType.PRICE_FOR_TOOLING,
        ],
    )
    base.update(overrides)
    return CorporateMandate(**base)


def _offer(**overrides) -> SupplierCurrentOffer:
    base = dict(price=15.0, lead_days=25, deposit_pct=0.30, moq=500, mold_fee=3000.0)
    base.update(overrides)
    return SupplierCurrentOffer(**base)


class TestTripleLogrollingMoldGate(unittest.TestCase):
    """主缺陷復現：demanded=False + mold_fee=0 時不得產生模具全免提案。"""

    def test_no_waiver_claim_when_mandate_does_not_demand_it(self):
        """對方從未報模具費 → 贏家不得宣稱模具全免（修前：策略 4 硬掛 True）。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        res = LogrollingEngine.evaluate(mandate, offer)

        best = res.best_recommendation
        self.assertFalse(
            best.mold_fee_exempted,
            f"mandate 未要求免除模具費，贏家卻宣稱全免：{best.strategy_name}",
        )
        # 整個帕雷托前沿都不許出現模具全免承諾。
        for prop in res.all_pareto_proposals:
            with self.subTest(strategy=prop.strategy_name):
                self.assertFalse(
                    prop.mold_fee_exempted,
                    "mandate 未授權模具費減免，卻有候選宣稱全免",
                )

    def test_winner_script_makes_no_mold_fee_promise(self):
        """贏家（含中英文話術）不得出現模具費減免承諾。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        best = LogrollingEngine.evaluate(mandate, offer).best_recommendation

        for script_name, script in (
            ("buyer_script_zh", best.buyer_script_zh),
            ("buyer_script_en", best.buyer_script_en),
        ):
            with self.subTest(surface=script_name):
                self.assertNotIn("全額減免", best.buyer_script_zh)
                self.assertNotIn(
                    "減免", script,
                    f"{script_name} 仍對外承諾模具費減免（對方從未報過模具費）",
                )
                self.assertNotIn("fully waived", script)
                self.assertNotIn("tooling fee", script)

    def test_fabricated_mold_surplus_no_longer_wins_rank(self):
        """虛構模具收益不得再主導排名（修前贏家 4824.59，第二名僅 1247.53）。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        res = LogrollingEngine.evaluate(mandate, offer)
        ranked = sorted(
            res.all_pareto_proposals, key=lambda x: -x.buyer_surplus_value
        )

        # 修前的虛構贏家「全維度超級置換」不得出現在前沿中。
        for prop in res.all_pareto_proposals:
            with self.subTest(strategy=prop.strategy_name):
                self.assertNotIn(
                    "Triple Logrolling", prop.strategy_name,
                    "模具費守門失效：策略 4 候選仍然存活並進入排名",
                )

        # 解除守門後（demanded=True）同一條路徑的贏家曾達 4824.59；
        # 守門生效後，最高剩餘必須低於「不含模具收益」的真實水準。
        self.assertLess(
            ranked[0].buyer_surplus_value, mandate.estimated_mold_fee + 2000.0,
            "贏家的買方剩餘仍含未校準的虛構模具收益",
        )
        # 贏家第二名之間的差距必須回到同一量級（不再橫跨 3000 的虛構收益）。
        if len(ranked) >= 2:
            self.assertLess(
                ranked[0].buyer_surplus_value - ranked[1].buyer_surplus_value,
                mandate.estimated_mold_fee,
                "前兩名差距仍橫跨整筆未發生的模具費減免",
            )

    def test_waiver_still_available_when_mandate_demands_it(self):
        """控制組：mandate 確實要求且對方真的報了模具費，減免不得被誤關掉。"""
        mandate = _mandate(mold_fee_exemption_demanded=True)
        offer = _offer(mold_fee=3000.0)

        res = LogrollingEngine.evaluate(mandate, offer)

        waiver_candidates = [
            p for p in res.all_pareto_proposals if p.mold_fee_exempted
        ]
        self.assertTrue(
            waiver_candidates,
            "mandate 要求免除模具費且對方已報價，卻沒有任何候選提供該置換",
        )
        best = res.best_recommendation
        self.assertTrue(best.mold_fee_exempted)
        self.assertIn("全額減免", best.buyer_script_zh)
        # 控制組數值不得退化（舊行為：模具全免帶來正收益）。
        self.assertGreater(best.buyer_surplus_value, 4000.0)

    def test_waiver_blocked_when_counterpart_never_quoted_a_fee(self):
        """demanded=True 但對方 mold_fee=0 → 沒有可減免的對象，話術不得承諾。"""
        mandate = _mandate(mold_fee_exemption_demanded=True)
        offer = _offer(mold_fee=0.0)

        best = LogrollingEngine.evaluate(mandate, offer).best_recommendation

        self.assertNotIn(
            "全額減免", best.buyer_script_zh,
            "對方從未報出模具費，話術卻仍宣稱全額減免",
        )
        self.assertNotIn("fully waived", best.buyer_script_en)
        # 沒有可減免的錢，收益不得再計入預估模具費。
        self.assertLess(
            best.buyer_surplus_value,
            (mandate.walk_away_ceiling - best.proposed_price) * mandate.baseline_moq
            + mandate.estimated_mold_fee,
            "對方未報模具費時，surplus 仍把 estimated_mold_fee 計入",
        )


class TestIsFeasibleMoldGateBeltAndBraces(unittest.TestCase):
    """第二道防線：`_is_feasible` 獨立攔截未授權的模具全免提案。"""

    def test_is_feasible_rejects_ungrounded_mold_exemption(self):
        """即使候選被掛上 mold_exempt=True，_is_feasible 也必须拒絕。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        rogue = LogrollingEngine._create_proposal(
            name="rogue waiver", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=True,
            tradeoffs=[
                TradeoffType.DEPOSIT_FOR_LEADTIME,
                TradeoffType.VOLUME_FOR_PRICE,
                TradeoffType.PRICE_FOR_TOOLING,
            ],
            mandate=mandate,
            offer=offer,
        )
        # 候選物件本身確實宣稱全免（證明這是「未授權承諾」被攔，而非從未產生）。
        self.assertTrue(rogue.mold_fee_exempted)
        self.assertFalse(
            LogrollingEngine._is_feasible(rogue, mandate),
            "_is_feasible 必須拒絕「宣稱 mold_exempt 但 mandate 未要求減免」的提案",
        )

    def test_is_feasible_allows_grounded_mold_exemption(self):
        """控制組：有依據的模具全免提案不得被誤攔。"""
        mandate = _mandate(mold_fee_exemption_demanded=True)
        offer = _offer(mold_fee=3000.0)

        grounded = LogrollingEngine._create_proposal(
            name="grounded waiver", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=True,
            tradeoffs=[
                TradeoffType.DEPOSIT_FOR_LEADTIME,
                TradeoffType.VOLUME_FOR_PRICE,
                TradeoffType.PRICE_FOR_TOOLING,
            ],
            mandate=mandate,
            offer=offer,
        )
        self.assertTrue(LogrollingEngine._is_feasible(grounded, mandate))

    def test_is_feasible_rejects_tooling_tradeoff_without_exemption(self):
        """掛了 PRICE_FOR_TOOLING 就必須真的換到模具免費，否則是話術造假。"""
        mandate = _mandate(mold_fee_exemption_demanded=True)
        offer = _offer(mold_fee=3000.0)

        inconsistent = LogrollingProposal(
            strategy_name="claims tooling but keeps the fee",
            proposed_price=13.50,
            proposed_lead_days=15,
            proposed_deposit_pct=0.50,
            proposed_moq=750,
            mold_fee_exempted=False,
            tradeoff_types_used=[
                TradeoffType.DEPOSIT_FOR_LEADTIME,
                TradeoffType.PRICE_FOR_TOOLING,
            ],
            buyer_surplus_value=1000.0,
            buyer_effective_unit_saving=1.3,
            supplier_surplus_estimate=500.0,
            explanation="",
        )
        self.assertFalse(
            LogrollingEngine._is_feasible(inconsistent, mandate),
            "宣稱換了模具費（PRICE_FOR_TOOLING）卻未實際免除，必須被拒",
        )

    def test_ungrounded_rogue_candidate_never_reaches_pareto_front(self):
        """整合面：未來新增的候選若忘守門，也不得穿過 evaluate 進入前沿。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        res = LogrollingEngine.evaluate(mandate, offer)

        self.assertFalse(
            res.best_recommendation.mold_fee_exempted,
            "整合路徑仍選出宣稱模具全免的方案",
        )
        self.assertNotIn("全額減免", res.best_recommendation.buyer_script_zh)
        # 前沿中不得存在任何未授權的模具全免承諾。
        for prop in res.all_pareto_proposals:
            with self.subTest(strategy=prop.strategy_name):
                self.assertFalse(prop.mold_fee_exempted)


class TestMoldSurplusAccounting(unittest.TestCase):
    """`estimated_mold_fee` 不得在未發生減免時被計入收益（含供應商端）。"""

    def test_no_mold_fee_means_no_mold_surplus(self):
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        prop = LogrollingEngine._create_proposal(
            name="no-fee", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        self.assertNotIn("¥3000", prop.explanation)

    def test_estimated_mold_fee_is_not_counted_without_authorization(self):
        """demanded=False 時，即使 mold_exempt=True 也不得計入 3000。"""
        mandate = _mandate(mold_fee_exemption_demanded=False)
        offer = _offer(mold_fee=0.0)

        ungrounded = LogrollingEngine._create_proposal(
            name="ungrounded", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=True, tradeoffs=[], mandate=mandate, offer=offer,
        )
        grounded_shape = LogrollingEngine._create_proposal(
            name="grounded-shape", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        self.assertAlmostEqual(
            ungrounded.buyer_surplus_value, grounded_shape.buyer_surplus_value,
            places=2,
            msg="未授權的 mold_exempt 仍把 estimated_mold_fee 計入買方收益",
        )
        self.assertAlmostEqual(
            ungrounded.supplier_surplus_estimate,
            grounded_shape.supplier_surplus_estimate,
            places=2,
            msg="未授權的 mold_exempt 仍把 estimated_mold_fee*0.5 計入供應商成本",
        )

    def test_mold_surplus_counted_only_when_grounded(self):
        """控制組：有依據時 estimated_mold_fee 仍須如實計入（不得過度修正）。"""
        mandate = _mandate(mold_fee_exemption_demanded=True)
        offer = _offer(mold_fee=3000.0)

        with_waiver = LogrollingEngine._create_proposal(
            name="with", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=True, tradeoffs=[], mandate=mandate, offer=offer,
        )
        without_waiver = LogrollingEngine._create_proposal(
            name="without", p=13.50, l=15, d=0.50, q=750,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        self.assertAlmostEqual(
            with_waiver.buyer_surplus_value - without_waiver.buyer_surplus_value,
            mandate.estimated_mold_fee, places=2,
        )
        self.assertAlmostEqual(
            without_waiver.supplier_surplus_estimate
            - with_waiver.supplier_surplus_estimate,
            mandate.estimated_mold_fee * 0.5, places=2,
        )


if __name__ == "__main__":
    unittest.main()
