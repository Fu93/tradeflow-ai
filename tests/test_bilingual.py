"""測試 TradeFlow v4 雙語跨語言認知大腦、6 大意圖分類器與數值護欄。"""
import unittest
from src.engine.bilingual import (
    VendorTacticEnum,
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
        # 使用沒有觸發 Stage 1 正則的口語句子
        text_without_regex = "工廠開工水電開銷都平不掉，暫時給到14.50"
        with patch("requests.post", return_value=mock_resp), patch.dict("os.environ", {"EVOLUS_API_KEY": "dummy_key"}):
            res = InboundIntentClassifier.classify(text_without_regex, enable_llm_fallback=True)
            self.assertEqual(res.primary_intent, VendorTacticEnum.CRY_POVERTY_COST_HIKE)
            self.assertEqual(res.mentioned_price, 14.50)



if __name__ == "__main__":
    unittest.main()
