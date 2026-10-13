"""測試 TradeFlow v4 雙語跨語言認知大腦、策略語言中立核心與雙向表面渲染器。"""
import unittest
from src.engine.bilingual import (
    VendorTacticEnum,
    TacticalModeEnum,
    TacticalActionObject,
    ActionObjectASTGuardrail,
    ChineseSurfaceRenderer,
    EnglishExecutiveRenderer,
    InboundIntentClassifier,
    NumericGuardrailValidator,
    GuardrailViolationError,
    BilingualTacticalBridge,
)


class TestBilingualCognitiveBridge(unittest.TestCase):

    def test_intent_classification_off_platform(self):
        text = "平台聊天老被限制，加個微信細聊 13812345678"
        res = InboundIntentClassifier.classify(text)
        self.assertEqual(res.primary_intent, VendorTacticEnum.OFFLINE_BYPASS_DIVERT)
        self.assertTrue(res.requires_escalation)

    def test_intent_classification_cry_poverty(self):
        text = "現在原材料銅和布料天天漲，工人工資也貴，這個價做下來純虧本，沒利潤！"
        res = InboundIntentClassifier.classify(text)
        self.assertEqual(res.primary_intent, VendorTacticEnum.CRY_POVERTY_COST_HIKE)
        self.assertFalse(res.requires_escalation)

    def test_intent_classification_price_extraction(self):
        text = "最低批發價 14.80 元/把，500個起訂"
        res = InboundIntentClassifier.classify(text)
        self.assertEqual(res.mentioned_price, 14.80)
        self.assertEqual(res.demanded_moq, 500)

    def test_numeric_guardrail_pass(self):
        valid_text = "首批試單給到 ¥12.30 出廠價，現款現結 30% 定金見單走帳。"
        passed = NumericGuardrailValidator.validate_reply(
            draft_text=valid_text,
            authorized_price=12.30,
            walk_away_ceiling=14.00,
        )
        self.assertTrue(passed)

    def test_numeric_guardrail_forbidden_words(self):
        bad_text = "那我們再退一步，給您 ¥12.30 行不行？"
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                draft_text=bad_text,
                authorized_price=12.30,
                walk_away_ceiling=14.00,
            )

    def test_numeric_guardrail_ceiling_breach(self):
        breached_text = "意向價 ¥15.50 可以排單。"
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                draft_text=breached_text,
                authorized_price=12.30,
                walk_away_ceiling=14.00,
            )

    def test_bilingual_tactical_generation(self):
        reply = BilingualTacticalBridge.generate_chinese_counter_offer(
            tactic="LEAN_IN",
            authorized_price=12.50,
            product_name="自動傘",
            moq=1000,
        )
        self.assertIn("12.50", reply)
        self.assertIn("出廠價", reply)

    def test_telemetry_english_summary(self):
        summary = BilingualTacticalBridge.telemetry_english_summary(
            intent=VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
            quoted_price=14.50,
            target_price=12.00,
            p_break=0.18,
        )
        self.assertIn("AUDIT ESCALATION", summary)
        self.assertIn("14.50", summary)

    def test_llm_semantic_fallback_mock(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"primary_intent": "CRY_POVERTY_COST_HIKE", "confidence": 0.95, "mentioned_price": 14.5, "demanded_moq": null, "tactical_rationale": "Cost pressure inferred", "requires_escalation": false}'
                }
            }]
        }
        text_without_regex = "工廠開工水電開銷都平不掉，暫時給到14.50"
        with patch("requests.post", return_value=mock_resp), patch.dict("os.environ", {"EVOLUS_API_KEY": "dummy_key"}):
            res = InboundIntentClassifier.classify(text_without_regex, enable_llm_fallback=True)
            self.assertEqual(res.primary_intent, VendorTacticEnum.CRY_POVERTY_COST_HIKE)
            self.assertEqual(res.mentioned_price, 14.50)

    # -----------------------------------------------------------------------
    # 策略語言中立核心 (Language-Agnostic Decision Core) 單元測試
    # -----------------------------------------------------------------------
    def test_tactical_action_object_creation_and_serialization(self):
        """驗證 TacticalActionObject 結構化對象創建與 JSON 序列化。"""
        action_obj = TacticalActionObject(
            authorized_price=12.50,
            walk_away_ceiling=15.00,
            breakdown_risk=0.08,
            expected_margin=2.50,
            posterior_reserve_mean=1.12,
            posterior_reserve_std=0.05,
            bayesian_confidence=0.92,
            estimated_floor_price=13.44,
            tactical_mode=TacticalModeEnum.LEAN_IN,
            product_name="CNC航太金屬外殼",
            supplier_contact_name="陳總",
            lead_days=20,
            deposit_pct=0.30,
            moq=2000,
        )
        data = action_obj.model_dump()
        self.assertEqual(data["authorized_price"], 12.50)
        self.assertEqual(data["product_name"], "CNC航太金屬外殼")
        self.assertEqual(data["supplier_contact_name"], "陳總")
        self.assertEqual(data["bayesian_confidence"], 0.92)

    def test_action_object_ast_guardrail_pass(self):
        """驗證 AST 護欄對合規策略對象正常放行。"""
        action_obj = TacticalActionObject(
            authorized_price=12.50,
            walk_away_ceiling=15.00,
            deposit_pct=0.40,
            lead_days=15,
        )
        passed = ActionObjectASTGuardrail.verify(action_obj)
        self.assertTrue(passed)

    def test_action_object_ast_guardrail_blocks_ceiling_breach(self):
        """驗證 AST 護欄嚴格阻斷高於上限之出價。"""
        action_obj = TacticalActionObject(
            authorized_price=16.50,
            walk_away_ceiling=15.00,
            deposit_pct=0.30,
            lead_days=20,
        )
        with self.assertRaises(GuardrailViolationError):
            ActionObjectASTGuardrail.verify(action_obj)

    def test_action_object_ast_guardrail_blocks_over_deposit(self):
        """驗證 AST 護欄嚴格阻斷定金突破 60% 系統安全上限。"""
        action_obj = TacticalActionObject(
            authorized_price=12.00,
            walk_away_ceiling=15.00,
            deposit_pct=0.75,
            lead_days=20,
        )
        with self.assertRaises(GuardrailViolationError):
            ActionObjectASTGuardrail.verify(action_obj)

    def test_chinese_surface_renderer_dynamic_interpolation(self):
        """驗證中文渲染器動態插值品類與稱呼，且徹底杜絕外商溢價詞彙。"""
        action_obj = TacticalActionObject(
            authorized_price=85.00,
            walk_away_ceiling=110.00,
            tactical_mode=TacticalModeEnum.LEAN_IN,
            product_name="工業級LiFePO4電池組",
            supplier_contact_name="李廠長",
            deposit_pct=0.30,
            moq=500,
        )
        rendered = ChineseSurfaceRenderer.render(action_obj)
        self.assertIn("李廠長", rendered)
        self.assertIn("工業級LiFePO4電池組", rendered)
        self.assertIn("¥85.00", rendered)
        self.assertIn("30%", rendered)
        # 嚴格禁止外商溢價詞彙
        self.assertNotIn("歐美專案", rendered)
        self.assertNotIn("外商", rendered)
        self.assertNotIn("美元", rendered)

    def test_chinese_surface_renderer_logroll(self):
        """驗證中文渲染器正確渲染多維 Logrolling 置換條款。"""
        action_obj = TacticalActionObject(
            authorized_price=13.80,
            walk_away_ceiling=16.00,
            tactical_mode=TacticalModeEnum.LOGROLL,
            product_name="客製碳纖維自動傘",
            supplier_contact_name="王總",
            lead_days=18,
            deposit_pct=0.50,
            mold_fee_exempted=True,
            waive_mold_fee_amount=3000.0,
        )
        rendered = ChineseSurfaceRenderer.render(action_obj)
        self.assertIn("王總", rendered)
        self.assertIn("¥13.80", rendered)
        self.assertIn("50%", rendered)
        self.assertIn("18 天", rendered)
        self.assertIn("免除模具費 ¥3000", rendered)

    def test_english_executive_renderer_card(self):
        """驗證英文高管決策卡包含貝氏底價、置信度、破裂風險與清晰指令。"""
        action_obj = TacticalActionObject(
            authorized_price=12.20,
            walk_away_ceiling=15.50,
            breakdown_risk=0.072,
            expected_margin=3.30,
            posterior_reserve_mean=1.08,
            posterior_reserve_std=0.04,
            bayesian_confidence=0.96,
            estimated_floor_price=12.96,
            tactical_mode=TacticalModeEnum.LEAN_IN,
            product_name="Aerospace Aluminum Enclosure",
            lead_days=21,
            deposit_pct=0.30,
            moq=1500,
            counterpart_offered_price=14.50,
        )
        card = EnglishExecutiveRenderer.render_executive_card(action_obj)
        self.assertIn("TradeFlow CPO Executive Telemetry", card)
        self.assertIn("Aerospace Aluminum Enclosure", card)
        self.assertIn("¥14.50", card)
        self.assertIn("¥12.20", card)
        self.assertIn("¥12.96", card)
        self.assertIn("96.0%", card)
        self.assertIn("7.2%", card)
        self.assertIn("[ 1 ] CONCEDE", card)
        self.assertIn("[ 2 ] HOLD FIRM", card)
        self.assertIn("[ 3 ] WALK AWAY", card)


if __name__ == "__main__":
    unittest.main()
