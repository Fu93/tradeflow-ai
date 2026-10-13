"""TradeFlow v4 — Logrolling 多維條款置換引擎單元測試與 AST 護欄驗證。"""
import unittest
from src.engine.logrolling import (
    CorporateMandate,
    TradeoffType,
    SupplierCurrentOffer,
    LogrollingEngine,
    ASTMandateGuardrail,
    ASTMandateViolationError,
)


class TestLogrollingEngine(unittest.TestCase):

    def setUp(self):
        self.mandate = CorporateMandate(
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

    def test_mandate_convex_validation(self):
        """驗證 Corporate Mandate 的邊界約束校驗。"""
        with self.assertRaises(ValueError):
            CorporateMandate(target_price=15.0, walk_away_ceiling=14.0)

        with self.assertRaises(ValueError):
            CorporateMandate(
                target_price=12.0,
                walk_away_ceiling=16.0,
                baseline_lead_days=25,
                max_acceptable_lead_days=20,
            )

    def test_pareto_optimization_when_price_stagnant(self):
        """當工廠在單價 ¥15.00 寸步不讓時，驗證多維置換創造出帕雷托改善。"""
        offer = SupplierCurrentOffer(
            price=15.0,
            lead_days=25,
            deposit_pct=0.30,
            moq=500,
            mold_fee=3000.0,
            is_price_stagnant=True,
        )
        res = LogrollingEngine.evaluate(self.mandate, offer)
        self.assertTrue(res.ast_guardrail_passed)
        self.assertGreater(len(res.all_pareto_proposals), 0)

        best = res.best_recommendation
        self.assertGreater(best.buyer_surplus_value, 500.0)
        self.assertTrue(best.proposed_lead_days <= 25)
        self.assertTrue(best.buyer_script_zh != "")
        self.assertIn("定金", best.buyer_script_zh)

    def test_ast_guardrail_blocks_over_deposit(self):
        """AST 護欄驗證：定金超出授權上限必須引發異常。"""
        illegal_proposal = {
            "proposed_price": 14.0,
            "proposed_lead_days": 20,
            "proposed_deposit_pct": 0.60,
            "proposed_moq": 500,
            "tradeoff_types_used": ["DEPOSIT_FOR_LEADTIME"],
        }
        with self.assertRaises(ASTMandateViolationError):
            ASTMandateGuardrail.verify_proposal(
                illegal_proposal,
                self.mandate,
                buyer_script="我方預付 60% 定金",
            )

    def test_ast_guardrail_blocks_script_numeric_tampering(self):
        """AST 護欄驗證：話術數字越界時必須引發異常。"""
        valid_proposal = {
            "proposed_price": 14.0,
            "proposed_lead_days": 20,
            "proposed_deposit_pct": 0.50,
            "proposed_moq": 500,
            "tradeoff_types_used": ["DEPOSIT_FOR_LEADTIME"],
        }
        tampered_script = "我方今天財務特批預付 70% 定金現款秒結排單"
        with self.assertRaises(ASTMandateViolationError):
            ASTMandateGuardrail.verify_proposal(
                valid_proposal,
                self.mandate,
                buyer_script=tampered_script,
            )


if __name__ == "__main__":
    unittest.main()
