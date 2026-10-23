"""task-2 / 獨立審計回歸測試：數值護欄、英文面、Logrolling 驗證順序。

本檔集中放置「修正前 FAIL、修正後 PASS」的對抗性案例。所有繞過字串都是
**測試資料**，不得為了讓它們通過而改動生產行為。

對應缺陷：
  * DEEP_DEFECT_AUDIT.md §2.1 / §2.2 / §2.3 / §2.4 / §2.5 / §5.2 / §5.5
  * 獨立審計追加項 C1（價格格式覆蓋）、C2（品名注入）、C3（`python -O`）、
    C5（Stage-2 欄位未夾制）、H3（截斷打敗升級閘門）
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.bilingual import (
    ActionObjectASTGuardrail,
    ChineseSurfaceRenderer,
    EnglishExecutiveRenderer,
    GuardrailViolationError,
    InboundIntentClassifier,
    NumericGuardrailValidator,
    TacticalActionObject,
    TacticalModeEnum,
)
from src.legacy.logrolling import (
    ASTMandateGuardrail,
    ASTMandateViolationError,
    CorporateMandate,
    LogrollingEngine,
    SupplierCurrentOffer,
    TradeoffType,
)


def _mandate(**overrides) -> CorporateMandate:
    base = dict(
        target_price=12.0,
        walk_away_ceiling=16.0,
        baseline_lead_days=25,
        max_acceptable_lead_days=35,
        min_desired_lead_days=15,
        lead_day_shadow_value=0.15,
        baseline_deposit_pct=0.30,
        max_allowed_deposit_pct=0.50,
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


# ===========================================================================
# §2.1 — 數值護欄只有上限，沒有下限 / 沒有「第二個承諾價」
# ===========================================================================
class TestNumericGuardrailFloor(unittest.TestCase):
    """修正前：護欄只拒絕「高於上限」，授權價旁再附更低承諾價一律放行。"""

    AUTH = 13.00
    CEIL = 16.20

    def test_rejects_second_unauthorized_lower_price(self):
        """審計 §2.1 重現字串：授權 13.00 卻同時承諾 9.50，修正前 PASSED。"""
        draft = "老闆，¥13.00 出廠價可以，另外我們願意 ¥9.50 出廠價成交。"
        with self.assertRaises(GuardrailViolationError) as ctx:
            NumericGuardrailValidator.validate_reply(
                draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
            )
        self.assertIn("9.50", str(ctx.exception))

    def test_rejects_second_unauthorized_higher_price(self):
        """授權價旁另承諾一個較高但仍在授權區間外的價格，同樣必須被拒。"""
        draft = "老闆，¥13.00 是我們的目標，但也可以給到 ¥14.00。"
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
            )

    def test_explicit_authorized_floor_is_honoured(self):
        """提供明確下限時，下限之上、授權價之下的數字仍算未授權。"""
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                "我方授權價 ¥13.00，最低可到 ¥12.00。",
                authorized_price=self.AUTH,
                walk_away_ceiling=self.CEIL,
                authorized_floor=12.50,
            )

    def test_floor_defaults_to_authorized_price(self):
        """`authorized_floor` 未提供時預設等於 `authorized_price`。"""
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                "我方授權價 ¥13.00，另外 ¥12.90 也可以。",
                authorized_price=self.AUTH,
                walk_away_ceiling=self.CEIL,
            )

    def test_control_legit_draft_still_passes(self):
        """控制組：只提及授權價的合法草稿必須仍然通過。"""
        self.assertTrue(
            NumericGuardrailValidator.validate_reply(
                "首批試單給到 ¥13.00 出廠價，現款現結 30% 定金見單走帳。",
                authorized_price=self.AUTH,
                walk_away_ceiling=self.CEIL,
            )
        )

    def test_control_above_ceiling_still_rejected(self):
        """控制組：超過上限的價格必須維持被拒（回歸保護）。"""
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                "我方意向價 ¥13.00，最高 15.50 元。",
                authorized_price=self.AUTH,
                walk_away_ceiling=14.00,
            )


# ===========================================================================
# C1 — 價格格式覆蓋（USD / RMB 後綴 / pcs / 套）
# ===========================================================================
class TestNumericGuardrailPriceFormats(unittest.TestCase):
    """修正前：只有貨幣前綴與 `元|塊|/件|/把|/個` 後綴會被掃到。"""

    AUTH = 12.30
    CEIL = 14.00

    # 審計 C1 實測：這四句在修正前全部 PASS（即護欄未攔截越界價）。
    BYPASS_DRAFTS = [
        "我方意向價 ¥12.30，另外也可以做到 USD 15.50 一件。",
        "我方意向價 ¥12.30，最高可以給您 15.50 RMB。",
        "我方意向價 ¥12.30，可以做到 15.50/pcs。",
        "我方意向價 ¥12.30，可以做到 15.50/套。",
        "我方意向價 ¥12.30，若貴廠能接受 6.00 元/件也行。",
        "我方意向價 ¥12.30，最低 12.90 每個。",
    ]

    def test_format_variants_are_all_rejected(self):
        for draft in self.BYPASS_DRAFTS:
            with self.subTest(draft=draft):
                with self.assertRaises(GuardrailViolationError):
                    NumericGuardrailValidator.validate_reply(
                        draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
                    )

    def test_control_chinese_yuan_suffix_still_works(self):
        """控制組：`元` 後綴越界仍須被拒（舊行為不得退化）。"""
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_reply(
                "我方意向價 ¥12.30，最高 15.50 元。",
                authorized_price=self.AUTH,
                walk_away_ceiling=self.CEIL,
            )


# ===========================================================================
# §2.2 — 禁用詞只比對 7 個字面字串
# ===========================================================================
class TestForbiddenCompromisePhrases(unittest.TestCase):
    """修正前：改一個字即繞過（審計實測四個改寫全部 PASS）。"""

    AUTH = 13.00
    CEIL = 16.20

    PARAPHRASES = [
        "私底下打款", "走私人帳戶", "不開票", "用二料",
        "私底下轉帳", "用私人帳號", "不開發票", "二料", "次料", "回料",
        "走私單", "私下打款", "線下付款",
    ]

    def test_paraphrases_are_blocked(self):
        for phrase in self.PARAPHRASES:
            draft = f"老闆，我們{phrase}，¥13.00 出廠價可以。"
            with self.subTest(phrase=phrase):
                with self.assertRaises(GuardrailViolationError):
                    NumericGuardrailValidator.validate_reply(
                        draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
                    )

    def test_original_literal_list_still_blocked(self):
        """向後相容：原 7 個字面字串仍必須被攔。"""
        for word in NumericGuardrailValidator.FORBIDDEN_COMPROMISE_WORDS:
            draft = f"老闆，{word}，¥13.00 出廠價可以。"
            with self.subTest(word=word):
                with self.assertRaises(GuardrailViolationError):
                    NumericGuardrailValidator.validate_reply(
                        draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
                    )

    def test_legitimate_compliance_refusal_is_not_blocked(self):
        """控制組：渲染器實際在**拒絕**線下交易時不得誤攔自家合規話術。

        這是縮小誤報的必要條件：片語黑名單無法理解語意，需以否定語境豁免。
        """
        legit = [
            "公司財務合規審計明確要求走系統留痕，私下微信恕不方便。首批 ¥13.00 預算卡死。",
            "配件必須按標準 TDS 技術規範走 100% 原包全新料，AQL 1.0 驗收嚴檢拒收次料。 ¥13.00 出廠價。",
        ]
        for draft in legit:
            with self.subTest(draft=draft):
                self.assertTrue(
                    NumericGuardrailValidator.validate_reply(
                        draft, authorized_price=self.AUTH, walk_away_ceiling=self.CEIL,
                    )
                )


# ===========================================================================
# §2.3 — 英文高管卡完全未經數值護欄
# ===========================================================================
class TestBilingualGuardrail(unittest.TestCase):
    """修正前：只驗中文 `auto_reply_text`，推送給主管手機的英文卡未受檢。"""

    def test_validate_bilingual_entrypoint_exists_and_passes_legit_pair(self):
        obj = TacticalActionObject(
            authorized_price=12.20, walk_away_ceiling=15.50,
            product_name="Aerospace Aluminum Enclosure",
        )
        zh = ChineseSurfaceRenderer.render(obj)
        en = EnglishExecutiveRenderer.render_executive_card(obj)
        self.assertTrue(
            NumericGuardrailValidator.validate_bilingual(
                zh, en, authorized_price=12.20, walk_away_ceiling=15.50,
            )
        )

    def test_english_card_unauthorized_concession_is_caught(self):
        """英文表面若被注入未授權讓步價，必須與中文面同樣被拒（§2.3 的核心）。"""
        tampered_en = (
            "Dear Partner, we can also go to USD 9.50/unit today "
            "if that helps you close the order."
        )
        zh = "我方授權價 ¥12.20，現款現結 30% 定金見單走帳。"
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_bilingual(
                zh, tampered_en, authorized_price=12.20, walk_away_ceiling=15.50,
            )

    def test_english_card_ceiling_breach_is_caught(self):
        tampered_en = "We can go as high as 17.20/unit if needed."
        zh = "我方授權價 ¥12.20，現款現結 30% 定金見單走帳。"
        with self.assertRaises(GuardrailViolationError):
            NumericGuardrailValidator.validate_bilingual(
                zh, tampered_en, authorized_price=12.20, walk_away_ceiling=15.50,
            )

    def test_rendered_english_card_passes_guardrail(self):
        """實際渲染出的高管卡不得被自家護欄拒絕（否則呼叫端只能停用護欄）。"""
        for floor in (None, 12.0):
            obj = TacticalActionObject(
                authorized_price=12.20, walk_away_ceiling=15.50,
                breakdown_risk=0.072, expected_margin=3.30,
                estimated_floor_price=12.96, counterpart_offered_price=14.50,
                product_name="Aerospace Aluminum Enclosure",
                deposit_pct=0.30, moq=1500,
            )
            card = EnglishExecutiveRenderer.render_executive_card(obj)
            with self.subTest(floor=floor):
                self.assertTrue(
                    NumericGuardrailValidator.validate_bilingual(
                        ChineseSurfaceRenderer.render(obj), card,
                        authorized_price=12.20, walk_away_ceiling=15.50,
                        authorized_floor=floor,
                    )
                )


# ===========================================================================
# C2 — 品名夾帶數字繞過護欄
# ===========================================================================
class TestProductNameInjection(unittest.TestCase):
    """修正前：`product_name` 原樣插值，兩個越界價格隨品名進話術並通過護欄。"""

    def test_rendered_draft_no_longer_carries_out_of_bounds_prices(self):
        obj = TacticalActionObject(
            authorized_price=12.00, walk_away_ceiling=16.20,
            tactical_mode=TacticalModeEnum.LEAN_IN,
            product_name="核心走量訂單 15.50 RMB 包郵 USD16.90",
        )
        draft = ChineseSurfaceRenderer.render(obj)
        # 修正前此呼叫回傳 True（護欄全盲），且草稿中確實含 15.50 與 16.90。
        self.assertTrue(
            NumericGuardrailValidator.validate_reply(
                draft, authorized_price=12.00, walk_away_ceiling=16.20,
            )
        )
        self.assertNotIn("15.50", draft)
        self.assertNotIn("16.90", draft)

    def test_legitimate_product_name_with_digits_is_preserved(self):
        """控制組：含數字但非貨幣的品名不得被淨化破壞。"""
        for name in ("304不銹鋼法蘭", "LiFePO4電池組", "201不鏽鋼件"):
            obj = TacticalActionObject(
                authorized_price=12.00, walk_away_ceiling=16.20, product_name=name,
            )
            with self.subTest(name=name):
                self.assertIn(name, ChineseSurfaceRenderer.render(obj))


# ===========================================================================
# C3 — `python -O` 會剝除 assert，令護欄消失
# ===========================================================================
class TestGuardrailSurvivesOptimisation(unittest.TestCase):
    """修正前：護欄以 `assert` 實作，`python -O` 下對越界對象回傳 True。"""

    def test_guardrail_source_contains_no_assert(self):
        """靜態回歸：護欄不得再依賴可被剝除的 `assert` 敘述。"""
        for src in (
            inspect.getsource(ActionObjectASTGuardrail),
            inspect.getsource(ASTMandateGuardrail),
        ):
            tree = ast.parse(src)
            # 只檢查「被執行的程式碼」中的 assert，docstring 內文字不算。
            asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
            self.assertEqual(
                asserts, [],
                "安全護欄不得使用 assert（python -O 會剝除，護欄靜默失效）",
            )

    def test_guardrail_still_blocks_with_assertions_stripped(self):
        """以 `-O` 的最終效果驗證：即使 assert 被剝除，護欄仍必須攔阻。

        做法是直接移除 AST 中所有 Assert 節點後再編譯執行 —— 這正是 `-O`
        對本段程式碼所做的事，不需另起一個最佳化直譯器程序。
        """
        from src.legacy.logrolling import ASTMandateGuardrail as _G  # noqa: F401  (存在性檢查)

        # 1. logrolling：AST 護欄在 assert 被剝除後仍須攔阻越界方案。
        tree = ast.parse(ASTMandateGuardrail._GUARDRAIL_SNIPPET)
        tree.body[0].body = [n for n in tree.body[0].body if not isinstance(n, ast.Assert)]
        scope: dict = {}
        exec(compile(tree, "<optimised_guardrail>", "exec"), scope)
        evaluate = scope["evaluate_mandate_invariants"]
        mandate = _mandate()
        mandate_dict = {
            "walk_away_ceiling": mandate.walk_away_ceiling,
            "max_acceptable_lead_days": mandate.max_acceptable_lead_days,
            "max_allowed_deposit_pct": mandate.max_allowed_deposit_pct,
            "max_allowed_moq": mandate.max_allowed_moq,
            "allowed_tradeoffs": [t.value for t in mandate.allowed_tradeoffs],
        }
        with self.assertRaises(ValueError):
            evaluate(
                {
                    "proposed_price": 99.0,
                    "proposed_lead_days": 20,
                    "proposed_deposit_pct": 0.50,
                    "proposed_moq": 500,
                    "tradeoff_types_used": ["DEPOSIT_FOR_LEADTIME"],
                },
                mandate_dict,
            )

        # 2. bilingual：ActionObjectASTGuardrail 的對應檢查。
        tree2 = ast.parse(ActionObjectASTGuardrail._GUARDRAIL_SNIPPET)
        tree2.body[0].body = [n for n in tree2.body[0].body if not isinstance(n, ast.Assert)]
        scope2: dict = {}
        exec(compile(tree2, "<optimised_ast_guardrail>", "exec"), scope2)
        check = scope2["_ast_check"]
        with self.assertRaises(ValueError):
            check(999.0, 15.0, 0.95, 5)

    def test_normal_mode_still_blocks(self):
        """一般模式（未加 -O）行為不得退化。"""
        with self.assertRaises(GuardrailViolationError):
            ActionObjectASTGuardrail.verify(
                TacticalActionObject(
                    authorized_price=999.0, walk_away_ceiling=15.0,
                    deposit_pct=0.95, lead_days=5,
                )
            )


# ===========================================================================
# C5 — Stage-2 LLM 欄位未夾制
# ===========================================================================
class TestStage2FieldClamping(unittest.TestCase):
    """修正前：LLM JSON 的 `mentioned_price` 原樣流入決策輸入，無 Stage-1 的夾制。"""

    INNOCUOUS = "工廠今天師傅請假，機器在保養，我們明天再聊細節。"

    def _classify_with_mocked_llm(self, json_content: str):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": json_content}}]}
        with patch("requests.post", return_value=resp), patch.dict(
            "os.environ", {"EVOLUS_API_KEY": "dummy_key"}
        ):
            return InboundIntentClassifier.classify(self.INNOCUOUS, enable_llm_fallback=True)

    def test_negative_mentioned_price_is_clamped(self):
        res = self._classify_with_mocked_llm(
            '{"primary_intent": "NORMAL_COUNTER_OFFER", "confidence": 0.9, '
            '"mentioned_price": -3.5, "demanded_moq": null, '
            '"tactical_rationale": "x", "requires_escalation": false}'
        )
        self.assertIsNone(res.mentioned_price)

    def test_absurdly_large_price_is_clamped(self):
        res = self._classify_with_mocked_llm(
            '{"primary_intent": "NORMAL_COUNTER_OFFER", "confidence": 0.9, '
            '"mentioned_price": 1e9, "demanded_moq": null, '
            '"tactical_rationale": "x", "requires_escalation": false}'
        )
        self.assertIsNone(res.mentioned_price)

    def test_moq_and_confidence_and_mold_fee_are_clamped(self):
        res = self._classify_with_mocked_llm(
            '{"primary_intent": "NORMAL_COUNTER_OFFER", "confidence": 7.5, '
            '"mentioned_price": 11.0, "demanded_moq": -400, '
            '"demanded_mold_fee": -100, '
            '"tactical_rationale": "x", "requires_escalation": false}'
        )
        self.assertEqual(res.mentioned_price, 11.0)   # 合法值仍保留
        self.assertIsNone(res.demanded_moq)
        self.assertIsNone(res.demanded_mold_fee)
        self.assertLessEqual(res.confidence, 1.0)

    def test_stage1_bounds_still_apply(self):
        """控制組：Stage-1 既有的 0.1..50000 夾制不得退化。"""
        res = InboundIntentClassifier.classify_stage1_only("最低批發價 14.80 元/把，500個起訂")
        self.assertEqual(res.mentioned_price, 14.80)


# ===========================================================================
# H3 — 輸入截斷打敗合規升級閘門
# ===========================================================================
class TestEscalationGateIndependentOfLength(unittest.TestCase):
    """修正前：先截斷到 1000 字元才比對安全樣式，超長閒聊即可稀釋風險訊號。"""

    DIVERSION = "加個微信私下聊，走線下轉私帳"

    def test_long_padding_cannot_defeat_escalation(self):
        padded = ("今天天氣不錯，師傅在調機。" * 100) + self.DIVERSION
        self.assertGreater(len(padded), 1000)
        res = InboundIntentClassifier.classify(padded, enable_llm_fallback=False)
        self.assertTrue(res.requires_escalation)
        self.assertEqual(res.primary_intent.value, "OFFLINE_BYPASS_DIVERT")

    def test_short_version_still_escalates(self):
        """控制組：短文本行為不得改變。"""
        res = InboundIntentClassifier.classify(self.DIVERSION, enable_llm_fallback=False)
        self.assertTrue(res.requires_escalation)

    def test_negated_diversion_is_not_escalated(self):
        """控制組：明文拒絕線下交易者不得被誤標為需要升級。"""
        res = InboundIntentClassifier.classify("我們不加微信，也不用走線下，全部走平台。", enable_llm_fallback=False)
        self.assertFalse(res.requires_escalation)

    def test_material_degrade_after_padding_also_escalates(self):
        padded = ("常規閒聊內容，機器在調機。" * 100) + "這批可以用二料替代嗎"
        res = InboundIntentClassifier.classify(padded, enable_llm_fallback=False)
        self.assertTrue(res.requires_escalation)


# ===========================================================================
# 護欄自身不得成為 DoS 向量
# ===========================================================================
class TestGuardrailBoundedOnLongDraft(unittest.TestCase):
    """修正前：護欄正則對超長草稿呈二次成長（64k 字元實測 ~88 秒）。"""

    def test_long_draft_is_bounded(self):
        payload = "我方意向價 ¥12.30 " + ("\u3000" * 64000)
        t0 = time.perf_counter()
        try:
            NumericGuardrailValidator.validate_reply(
                payload, authorized_price=12.30, walk_away_ceiling=14.00,
            )
        except GuardrailViolationError:
            pass
        dt = time.perf_counter() - t0
        self.assertLess(dt, 2.0, f"護欄對 {len(payload)} 字元耗時 {dt:.2f}s（未受界）")


# ===========================================================================
# §2.4 / §2.5 — Logrolling 驗證順序、英文話術、供應商剩餘
# ===========================================================================
class TestLogrollingGuardrailOrder(unittest.TestCase):

    def test_absurd_combo_is_not_recommended_as_pareto_win(self):
        """審計 §2.4 重現：報價 9.00 / 定金 0.60 搭配 mandate 12/16.2。

        修正前：SUCCEEDED、推薦價 10.50、定金 50%、`ast_guardrail_passed=True`，
        而該方案供應商剩餘為 -2474.69（工廠明確虧損）。
        修正後：虧損方案不得作為「帕雷托改善」被推薦。
        """
        mandate = CorporateMandate(
            target_price=12,
            walk_away_ceiling=16.2,
            baseline_lead_days=25,
            max_acceptable_lead_days=35,
            min_desired_lead_days=12,
            baseline_moq=500,
            max_allowed_moq=1200,
        )
        offer = SupplierCurrentOffer(price=9.00, deposit_pct=0.60, lead_days=25, moq=500)
        res = LogrollingEngine.evaluate(mandate, offer)

        best = res.best_recommendation
        self.assertGreaterEqual(
            best.supplier_surplus_estimate, 0.0,
            "推薦給主管的方案不得是供應商明確虧損的方案",
        )
        for prop in res.all_pareto_proposals:
            with self.subTest(strategy=prop.strategy_name):
                self.assertGreaterEqual(prop.supplier_surplus_estimate, 0.0)

    def test_infeasible_candidates_never_reach_selection(self):
        """候選在建立時即受授權邊界把關（verification-order 修正）。"""
        mandate = _mandate(max_allowed_deposit_pct=0.50, max_allowed_moq=1200)
        offer = SupplierCurrentOffer(price=15.0, lead_days=25, deposit_pct=0.30, moq=500)
        res = LogrollingEngine.evaluate(mandate, offer)
        for prop in res.all_pareto_proposals:
            with self.subTest(strategy=prop.strategy_name):
                self.assertLessEqual(prop.proposed_price, mandate.walk_away_ceiling + 1e-6)
                self.assertLessEqual(prop.proposed_deposit_pct, mandate.max_allowed_deposit_pct + 1e-6)
                self.assertLessEqual(prop.proposed_moq, mandate.max_allowed_moq)
                self.assertLessEqual(prop.proposed_lead_days, mandate.max_acceptable_lead_days)

    def test_english_script_is_guardrailed(self):
        """§2.3/§2.4：英文話術 (`buyer_script_en`) 舊版完全未受檢。"""
        mandate = _mandate()
        bad_proposal = {
            "proposed_price": 14.0,
            "proposed_lead_days": 20,
            "proposed_deposit_pct": 0.50,
            "proposed_moq": 500,
            "tradeoff_types_used": ["DEPOSIT_FOR_LEADTIME"],
        }
        with self.assertRaises(ASTMandateViolationError):
            ASTMandateGuardrail.verify_proposal(
                bad_proposal, mandate,
                buyer_script="我方預付 50% 定金。",
                buyer_script_en="Our treasury approved a 90% cash deposit today.",
            )

    def test_ast_guardrail_passed_reflects_reality(self):
        """§2.5：舊版在原始碼中硬寫 `ast_guardrail_passed=True`。"""
        src = inspect.getsource(LogrollingEngine.evaluate)
        self.assertNotIn(
            "ast_guardrail_passed=True", src,
            "ast_guardrail_passed 不得再是常數（§2.5 恆真值）",
        )
        res = LogrollingEngine.evaluate(
            _mandate(), SupplierCurrentOffer(price=15.0, lead_days=25, deposit_pct=0.30, moq=500)
        )
        self.assertIsInstance(res.ast_guardrail_passed, bool)

    def test_evaluate_signature_and_result_fields_stable(self):
        """INTERFACE FREEZE：main.py 依賴的簽名與欄位名稱不得改變。"""
        sig = inspect.signature(LogrollingEngine.evaluate)
        self.assertEqual(list(sig.parameters), ["mandate", "offer"])
        res = LogrollingEngine.evaluate(
            _mandate(), SupplierCurrentOffer(price=15.0, lead_days=25, deposit_pct=0.30, moq=500)
        )
        for field in (
            "best_recommendation", "all_pareto_proposals", "original_supplier_offer",
            "total_candidates_evaluated", "ast_guardrail_passed", "summary_report",
        ):
            self.assertTrue(hasattr(res, field), f"LogrollingResult 缺少欄位 {field}")
        for field in (
            "proposed_price", "proposed_lead_days", "proposed_deposit_pct",
            "proposed_moq", "mold_fee_exempted", "tradeoff_types_used",
            "buyer_surplus_value", "buyer_effective_unit_saving",
            "supplier_surplus_estimate", "buyer_script_zh", "buyer_script_en",
        ):
            self.assertTrue(
                hasattr(res.best_recommendation, field),
                f"LogrollingProposal 缺少欄位 {field}",
            )


# ===========================================================================
# §5.5 — 量膨脹不再凌駕真實價格讓步
# ===========================================================================
class TestLogrollingSurplusAccounting(unittest.TestCase):

    def test_volume_inflation_does_not_beat_real_price_concession(self):
        """修正前：`price_surplus=(ceiling-p)*q` 讓加量成為免費獲利。

        以相同 mandate、**相同持有成本基數**比較兩個真實候選：
          A) 批量膨脹：量 5000 件、價僅讓 0.10（多出的量本身就要付持有成本）
          B) 真實讓價：量 1000 件、價大幅讓 2.00
        修正前 A 的「買方剩餘」因量而膨脹到 43,500 並凌駕 B；修正後 B 必須勝出，
        因為超出需求基準量（500）的庫存在修正後不再計入價格剩餘。
        """
        mandate = _mandate(baseline_moq=500, max_allowed_moq=5000)
        offer = SupplierCurrentOffer(price=15.0, lead_days=25, deposit_pct=0.30, moq=500)

        inflation = LogrollingEngine._create_proposal(
            name="inflation", p=14.90, l=25, d=0.30, q=5000,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        concession = LogrollingEngine._create_proposal(
            name="concession", p=13.00, l=25, d=0.30, q=1000,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        self.assertGreater(
            concession.buyer_surplus_value, inflation.buyer_surplus_value,
            "真實價格讓步必須勝過單純的量膨脹（§5.5）",
        )

    def test_extra_quantity_is_not_free_money(self):
        """加量不得以完整價差計入價格剩餘，且必須支付超額持有成本。"""
        mandate = _mandate()
        offer = SupplierCurrentOffer(price=15.0, lead_days=25, deposit_pct=0.30, moq=500)
        small_q = LogrollingEngine._create_proposal(
            name="q500", p=13.00, l=25, d=0.30, q=500,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        large_q = LogrollingEngine._create_proposal(
            name="q1200", p=13.00, l=25, d=0.30, q=1200,
            mold_exempt=False, tradeoffs=[], mandate=mandate, offer=offer,
        )
        # 舊公式會給 1200 件 3600 的價格剩餘；修正後額外 700 件完全不計入剩餘。
        naive_full_credit = (mandate.walk_away_ceiling - 13.00) * 1200
        self.assertLess(
            large_q.buyer_surplus_value, naive_full_credit,
            "超額庫存不得以完整價差計入（量增不是免費的錢）",
        )
        required_credit = (mandate.walk_away_ceiling - 13.00) * 500
        holding_cost = 13.00 * 700 * mandate.excess_holding_cost_rate
        self.assertAlmostEqual(
            large_q.buyer_surplus_value, required_credit - holding_cost, places=2,
        )
        # 每一件額外庫存的邊際貢獻必須為**負**（純成本），不可能高於基準量的邊際貢獻。
        marginal = (large_q.buyer_surplus_value - small_q.buyer_surplus_value) / 700.0
        self.assertLess(marginal, 0.0)


# ===========================================================================
# §5.2 — LOGROLL 渲染忠實度
# ===========================================================================
class TestLogrollRendererFidelity(unittest.TestCase):
    """修正前：LOGROLL 分支只提交期與模具費，完全未提定金比例與批量。"""

    def _obj(self, **kw):
        base = dict(
            authorized_price=13.80, walk_away_ceiling=16.00,
            tactical_mode=TacticalModeEnum.LOGROLL,
            product_name="客製碳纖維自動傘", supplier_contact_name="王總",
            lead_days=18, deposit_pct=0.50, moq=900,
            mold_fee_exempted=True, waive_mold_fee_amount=3000.0,
        )
        base.update(kw)
        return TacticalActionObject(**base)

    def test_logroll_renders_deposit_and_moq(self):
        rendered = ChineseSurfaceRenderer.render(self._obj())
        self.assertIn("50%", rendered)
        self.assertIn("900", rendered)
        # 既有敘事不得退化
        self.assertIn("¥13.80", rendered)
        self.assertIn("18 天", rendered)
        self.assertIn("免除模具費 ¥3000", rendered)

    def test_logroll_rendered_draft_passes_own_guardrail(self):
        """話術與決策必須一致：渲染結果不得被自家數值護欄拒絕。"""
        obj = self._obj()
        rendered = ChineseSurfaceRenderer.render(obj)
        self.assertTrue(
            NumericGuardrailValidator.validate_reply(
                rendered, authorized_price=obj.authorized_price,
                walk_away_ceiling=obj.walk_away_ceiling,
            )
        )

    def test_other_branches_wording_unchanged(self):
        """控制組：僅 LOGROLL 分支調整，其他分支措辭不得變動。"""
        lean = TacticalActionObject(
            authorized_price=12.50, walk_away_ceiling=15.00,
            product_name="自動傘", supplier_contact_name="李總", deposit_pct=0.30,
        )
        rendered = ChineseSurfaceRenderer.render(lean)
        self.assertIn("是我們下半年的核心走量訂單", rendered)
        self.assertIn("首批試單給到 ¥12.50 出廠價", rendered)


if __name__ == "__main__":
    unittest.main()
