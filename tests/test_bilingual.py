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

    # -----------------------------------------------------------------------
    # 反向鏈路：英文高管指令解析器與雙向一致性測試 (Reverse Pipeline Tests)
    # -----------------------------------------------------------------------
    def test_executive_command_parser_codes(self):
        """驗證高管指令代碼 1、2、3 正確解析。"""
        from src.engine.bilingual import EnglishExecutiveCommandParser, ExecutiveActionEnum
        c1 = EnglishExecutiveCommandParser.parse("1")
        self.assertEqual(c1.action, ExecutiveActionEnum.CONCEDE)

        c2 = EnglishExecutiveCommandParser.parse("2")
        self.assertEqual(c2.action, ExecutiveActionEnum.HOLD_FIRM)

        c3 = EnglishExecutiveCommandParser.parse("3")
        self.assertEqual(c3.action, ExecutiveActionEnum.WALK_AWAY)

    def test_executive_command_parser_numeric_price(self):
        """驗證直接輸入價格 (如 13.80 或 $14.20) 正確解析。"""
        from src.engine.bilingual import EnglishExecutiveCommandParser, ExecutiveActionEnum
        cp1 = EnglishExecutiveCommandParser.parse("13.80")
        self.assertEqual(cp1.action, ExecutiveActionEnum.OVERRIDE_PRICE)
        self.assertEqual(cp1.override_price, 13.80)

        cp2 = EnglishExecutiveCommandParser.parse("$14.20")
        self.assertEqual(cp2.action, ExecutiveActionEnum.OVERRIDE_PRICE)
        self.assertEqual(cp2.override_price, 14.20)

    def test_executive_command_parser_natural_language(self):
        """驗證英文自然語言商務指令 (條款打包) 正確解析。"""
        from src.engine.bilingual import EnglishExecutiveCommandParser, ExecutiveActionEnum
        cmd = EnglishExecutiveCommandParser.parse("offer 13.50 with 50% deposit within 15 days and waive tooling fee")
        self.assertEqual(cmd.action, ExecutiveActionEnum.OVERRIDE_PRICE)
        self.assertEqual(cmd.override_price, 13.50)
        self.assertEqual(cmd.demanded_deposit_pct, 0.50)
        self.assertEqual(cmd.demanded_lead_days, 15)
        self.assertTrue(cmd.waive_mold_fee)

    def test_reverse_pipeline_hold_firm_zh_generation(self):
        """驗證高管回覆 2 (Hold Firm) 時，中文渲染器生成強硬底線話術且零洩漏。"""
        from src.engine.bilingual import EnglishExecutiveCommandParser, CrossLingualSanityChecker
        parsed = EnglishExecutiveCommandParser.parse("2")
        action_obj = TacticalActionObject(
            source_round_id="SESS_402_R2_EXEC",
            authorized_price=14.50,
            walk_away_ceiling=16.00,
            tactical_mode=TacticalModeEnum.FIRM_HOLD,
            product_name="客製CNC金屬件",
            supplier_contact_name="張廠長",
            deposit_pct=0.30,
        )
        zh_script = ChineseSurfaceRenderer.render(action_obj)
        en_card = EnglishExecutiveRenderer.render_executive_confirmation_card(action_obj, parsed.action.value)

        self.assertIn("張廠長", zh_script)
        self.assertIn("¥14.50", zh_script)
        self.assertIn("預算卡死", zh_script)
        # 驗證一致性
        self.assertTrue(CrossLingualSanityChecker.verify_bidirectional_consistency(action_obj, zh_script, en_card))

    def test_reverse_pipeline_concede_zh_generation(self):
        """驗證高管回覆 1 (Concede) 時，中文渲染器生成特批讓步話術且零洩漏。"""
        from src.engine.bilingual import EnglishExecutiveCommandParser, CrossLingualSanityChecker
        parsed = EnglishExecutiveCommandParser.parse("1")
        action_obj = TacticalActionObject(
            source_round_id="SESS_402_R3_EXEC",
            authorized_price=13.20,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.LEAN_IN,
            product_name="自動折疊傘",
            supplier_contact_name="李總",
            deposit_pct=0.30,
        )
        zh_script = ChineseSurfaceRenderer.render(action_obj)
        en_card = EnglishExecutiveRenderer.render_executive_confirmation_card(action_obj, parsed.action.value)

        self.assertIn("李總", zh_script)
        self.assertIn("¥13.20", zh_script)
        self.assertIn("出廠價", zh_script)
        self.assertTrue(CrossLingualSanityChecker.verify_bidirectional_consistency(action_obj, zh_script, en_card))

    def test_schema_version_rejection(self):
        """驗證 TacticalActionObject 版本不匹配時渲染器主動拒絕。"""
        action_obj = TacticalActionObject(
            schema_version="3.0.0",
            authorized_price=12.00,
            walk_away_ceiling=15.00,
        )
        with self.assertRaises(ValueError):
            ChineseSurfaceRenderer.render(action_obj)

        with self.assertRaises(ValueError):
            EnglishExecutiveRenderer.render_executive_card(action_obj)

    def test_adversarial_identity_probe_detection_and_defense(self):
        """驗證工廠試探外貿/美元/口岸時被識別為 IDENTITY_PROBE_TEST，並以國內老買家行話反制。"""
        # 1. 識別測試
        msg1 = "你們是外貿公司嗎？能做美金嗎？"
        res1 = InboundIntentClassifier.classify(msg1)
        self.assertEqual(res1.primary_intent, VendorTacticEnum.IDENTITY_PROBE_TEST)

        msg2 = "貨走哪個港？你們在慈溪有倉庫嗎？"
        res2 = InboundIntentClassifier.classify(msg2)
        self.assertEqual(res2.primary_intent, VendorTacticEnum.IDENTITY_PROBE_TEST)

        # 2. 渲染防禦話術 (DEFENSIVE PIVOT 拉回交易要素，不自證)
        action_obj = TacticalActionObject(
            authorized_price=13.80,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.DEFENSIVE_PIVOT,
            product_name="快充適配器",
            supplier_contact_name="陳經理",
            counterpart_intent=VendorTacticEnum.IDENTITY_PROBE_TEST,
            deposit_pct=0.30,
        )
        rendered = ChineseSurfaceRenderer.render(action_obj)
        self.assertIn("開票走 13 個點專票", rendered)
        self.assertIn("財務後台走合同", rendered)
        self.assertIn("¥13.80", rendered)
        self.assertIn("30%", rendered)
        self.assertNotIn("外貿", rendered)
        self.assertNotIn("外商", rendered)

        # 3. 遙測英文摘要驗證
        telemetry = BilingualTacticalBridge.telemetry_english_summary(
            intent=VendorTacticEnum.IDENTITY_PROBE_TEST,
            quoted_price=15.00,
            target_price=13.80,
            p_break=0.08,
        )
        self.assertIn("IDENTITY PROBE", telemetry)

    def test_semantic_nativeness_audit_rejects_stiff_phrasing(self):
        """驗證語意審計器攔截生硬機翻書面腔與外商洩漏詞彙。"""
        from src.engine.bilingual import CrossLingualSanityChecker
        stiff_text = "尊敬的供應商，我們公司深感榮幸向您詢價，董事會希望了解美元結算條款"
        audit = CrossLingualSanityChecker.check_semantic_nativeness(stiff_text, enable_llm=False)
        self.assertFalse(audit["is_native"])
        self.assertIn("尊敬的", audit["stiff_phrases"])
        self.assertIn("深感榮幸", audit["stiff_phrases"])
        self.assertIn("董事會", audit["leak_phrases"])

    def test_has_actionable_commercial_content_structural_detection(self):
        """驗證反向結構化商務要素判定：不依賴黑名單，判斷是否含價/期/量/定金/規格。"""
        # 1. 包含可執行要素 -> True
        self.assertTrue(InboundIntentClassifier.has_actionable_commercial_content("這款單價 ¥14.50"))
        self.assertTrue(InboundIntentClassifier.has_actionable_commercial_content("起訂量 1000 套起"))
        self.assertTrue(InboundIntentClassifier.has_actionable_commercial_content("交期最快 20 天"))
        self.assertTrue(InboundIntentClassifier.has_actionable_commercial_content("定金要求 30% 預付"))
        self.assertTrue(InboundIntentClassifier.has_actionable_commercial_content("我們只做 304 不銹鋼原包料"))

        # 2. 不包含可執行商務要素（純雜訊、閒聊、試探） -> False
        self.assertFalse(InboundIntentClassifier.has_actionable_commercial_content("你們公司社保交在哪個城市？"))
        self.assertFalse(InboundIntentClassifier.has_actionable_commercial_content("上次那批貨的商檢單能發我看看嗎？"))
        self.assertFalse(InboundIntentClassifier.has_actionable_commercial_content("你們財務在那個區？"))
        self.assertFalse(InboundIntentClassifier.has_actionable_commercial_content("你們老闆哪裡人啊？"))
        self.assertFalse(InboundIntentClassifier.has_actionable_commercial_content("今天天氣挺熱的"))

    def test_defensive_pivot_structural_defense(self):
        """驗證當對手偏離交付要素時，策略原生 DEFENSIVE_PIVOT 不自證、鎖死專票與現款。"""
        action_obj = TacticalActionObject(
            authorized_price=13.80,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.DEFENSIVE_PIVOT,
            product_name="快充適配器",
            supplier_contact_name="陳經理",
            deposit_pct=0.30,
        )
        rendered = ChineseSurfaceRenderer.render(action_obj)
        self.assertIn("開票走 13 個點專票", rendered)
        self.assertIn("財務後台走合同", rendered)
        self.assertIn("¥13.80", rendered)
        self.assertIn("30%", rendered)
        self.assertNotIn("外商", rendered)
        self.assertNotIn("外貿", rendered)

    def test_silence_strategic_cooling_render(self):
        """驗證策略原生 SILENCE 渲染為老買手冷卻威懾話術。"""
        action_obj = TacticalActionObject(
            authorized_price=13.80,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.SILENCE,
            product_name="快充適配器",
            supplier_contact_name="陳經理",
        )
        rendered = ChineseSurfaceRenderer.render(action_obj)
        self.assertIn("陳經理你先忙", rendered)
        self.assertIn("我先問問餘姚那邊", rendered)

    def test_exemplars_group_b_out_of_distribution_validation(self):
        """
        Group B 語料驗證 (Out-of-Distribution Test Set):
        使用未出現在 Prompt Few-Shot 中的獨立長三角實戰採購對話，驗證結構化要素判定與防禦。
        """
        group_b_test_messages = [
            ("你們是做跨境電商還是線下實體？", False),
            ("這批貨走哪個口岸清關？", False),
            ("你們公司進出口權代碼發一下", False),
            ("這款出廠價最低 14.80 元給您", True),
            ("排單至少要 2000 個起訂", True),
            ("大批量可以 15 天交貨", True),
        ]
        for msg, expected_actionable in group_b_test_messages:
            res = InboundIntentClassifier.has_actionable_commercial_content(msg)
            self.assertEqual(res, expected_actionable, f"Failed on Group B message: {msg}")

    def test_mixed_signal_detection_and_suppression(self):
        """驗證混合信號 (同時含價格與身份試探) 被識別，並在英文卡標註、中文話術邊界鎖定。"""
        raw = "你們是做外貿還是內貿？這個價 14.50 元能做的話，我這週給你排單。"
        classification = InboundIntentClassifier.classify(raw)
        self.assertTrue(classification.is_mixed_signal)
        self.assertEqual(classification.mentioned_price, 14.50)

        action_obj = TacticalActionObject(
            authorized_price=14.50,
            walk_away_ceiling=16.00,
            product_name="金屬件",
            supplier_contact_name="李總",
            is_mixed_signal=True,
            mixed_signal_note=classification.mixed_signal_note,
        )
        en_card = EnglishExecutiveRenderer.render_executive_card(action_obj)
        zh_script = ChineseSurfaceRenderer.render(action_obj)

        self.assertIn("[MIXED SIGNAL DETECTED]", en_card)
        self.assertIn("問那麼多幹嘛", zh_script)
        self.assertIn("¥14.50", zh_script)

    def test_defensive_pivot_variants(self):
        """驗證 DEFENSIVE_PIVOT 面對抱怨行情、小單推託與刺探的三大話術變體。"""
        # 變體 A: 抱怨行情
        obj_cry = TacticalActionObject(
            authorized_price=13.50,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.DEFENSIVE_PIVOT,
            counterpart_intent=VendorTacticEnum.CRY_POVERTY_COST_HIKE,
        )
        res_cry = ChineseSurfaceRenderer.render(obj_cry)
        self.assertIn("行情波動我清楚", res_cry)

        # 變體 B: 以量壓價
        obj_moq = TacticalActionObject(
            authorized_price=13.50,
            walk_away_ceiling=15.00,
            tactical_mode=TacticalModeEnum.DEFENSIVE_PIVOT,
            counterpart_intent=VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
        )
        res_moq = ChineseSurfaceRenderer.render(obj_moq)
        self.assertIn("別因小失大", res_moq)

    def test_session_silence_break_trigger(self):
        """驗證多供應商獨立會話在對手主動降價 >= 3% 或實質讓步時瞬間破冰喚醒。"""
        from src.session.schema import NegotiationSession, SessionSpec
        session = NegotiationSession(
            spec=SessionSpec(session_id="S_TEST", product_name="殼體", target_price=12.0, ceiling_price=15.0),
            latest_opponent_offer=15.00,
            silence_until_ts=9999999999.0,  # 沉默冷卻中
        )
        # 對手只降價 1% (14.90) -> 不足以解除沉默
        unbroken = session.check_break_silence(new_offer=14.90)
        self.assertFalse(unbroken)
        self.assertIsNotNone(session.silence_until_ts)

        # 對手降價超過 3% (14.50 <= 15.00 * 0.97 = 14.55) -> 瞬間破冰解除
        broken = session.check_break_silence(new_offer=14.50)
        self.assertTrue(broken)
        self.assertIsNone(session.silence_until_ts)


if __name__ == "__main__":
    unittest.main()
