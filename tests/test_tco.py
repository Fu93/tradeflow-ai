"""測試 TradeFlow v4 多目標非價格條款 TCO 影子成本折算引擎。"""
import unittest
from src.engine.tco import TCOEngine, TradeTermsSpec, SupplierOfferedTerms


class TestTCOEngine(unittest.TestCase):

    def setUp(self):
        self.terms = TradeTermsSpec(
            baseline_lead_days=20,
            baseline_moq=1000,
            baseline_payment_deposit_pct=0.30,
            annual_cost_of_capital=0.08,
            daily_lead_delay_penalty_pct=0.003,
            excess_moq_holding_cost_rate=0.05,
        )

    def test_ideal_terms_no_penalty(self):
        """完全符合基準條件，等效價格等於原始報價。"""
        offered = SupplierOfferedTerms(
            raw_unit_price=12.0,
            offered_lead_days=20,
            offered_moq=1000,
            deposit_pct=0.30,
        )
        res = TCOEngine.calculate_effective_price(self.terms, offered)
        self.assertAlmostEqual(res.effective_unit_price, 12.0, places=2)
        self.assertEqual(res.lead_time_penalty, 0.0)
        self.assertEqual(res.moq_holding_penalty, 0.0)

    def test_backstabbed_by_leadtime_and_moq(self):
        """
        工廠表面降價至 11.50，但交期拖長至 45 天 (延遲 25 天)，且要求 MOQ 3000 (超額 200%)。
        驗證 TCO 引擎識別出影子成本，折算後等效單價反而高於原始報價。
        """
        offered = SupplierOfferedTerms(
            raw_unit_price=11.50,
            offered_lead_days=45,
            offered_moq=3000,
            deposit_pct=1.0,  # 要求 100% 全款
        )
        res = TCOEngine.calculate_effective_price(self.terms, offered)
        # 應有顯著的交期、MOQ 與資金佔用懲罰
        self.assertGreater(res.lead_time_penalty, 0.5)
        self.assertGreater(res.moq_holding_penalty, 0.5)
        self.assertGreater(res.payment_cost_penalty, 0.05)
        self.assertGreater(res.effective_unit_price, 11.50)
        self.assertFalse(res.is_favorable_vs_raw)

    def test_unbundled_tax_and_packaging(self):
        """工廠報裸價 12.0，但要求加 10% 稅點且外箱每件加收 0.5 元。"""
        offered = SupplierOfferedTerms(
            raw_unit_price=12.0,
            unbundled_tax_rate=0.10,
            unbundled_packaging_fee=0.50,
        )
        res = TCOEngine.calculate_effective_price(self.terms, offered)
        # 稅票 1.2 + 包裝 0.5 = 1.7 額外成本
        self.assertAlmostEqual(res.tax_packaging_adjustment, 1.70, places=2)
        self.assertAlmostEqual(res.effective_unit_price, 13.70, places=2)


if __name__ == "__main__":
    unittest.main()
