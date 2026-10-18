"""Walkthrough fixes: English quote fields, currency labels, and the decision desk."""
from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.engine.quote_fields import describe_supplier_price, extract_stage1
from src.engine.quote_read import read_supplier_quote
from src.engine.bilingual import render_supplier_reply, supplier_message_language
from src.engine.bilingual import TacticalActionObject, CURRENT_SCHEMA_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WALKTHROUGH = (
    "Hi, the price is 3.20 USD/pc for 1000 pcs, MOQ 500. "
    "Lead time 30 days, 30% deposit."
)


class TestEnglishQuoteReader(unittest.TestCase):
    def test_walkthrough_sentence_keeps_price_and_moq(self):
        fields = extract_stage1(WALKTHROUGH)
        self.assertEqual(describe_supplier_price(fields), "3.20 USD")
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["moq"], 500)
        self.assertIsNone(fields["price"])
        self.assertEqual(fields["lead_time_days"], 30)
        self.assertAlmostEqual(fields["deposit_pct"], 0.3)
        result = read_supplier_quote(WALKTHROUGH, amd_live=False)
        self.assertEqual(result["fields"]["moq"], 500)
        self.assertAlmostEqual(result["fields"]["quoted_amount"], 3.2)
        self.assertEqual(result["fields"]["currency"], "USD")
        self.assertIsNone(result["buyer_price"])
        self.assertFalse(result["decides_price"])

    def test_english_variants_do_not_treat_order_quantity_as_moq(self):
        cases = (
            ("USD 3.20 per piece, MOQ 400", "3.20 USD", 400, None),
            ("$3.20 per piece, min order 250", "3.20 USD", 250, None),
            ("RMB 15.60 per piece, MOQ 800", "15.60 CNY", 800, 15.6),
            ("CNY 12.00 per piece, minimum order 200", "12.00 CNY", 200, 12.0),
            ("for 1000 pcs", "missing", None, None),
        )
        for text, label, moq, cny in cases:
            with self.subTest(text=text):
                fields = extract_stage1(text)
                self.assertEqual(describe_supplier_price(fields), label)
                self.assertEqual(fields["moq"], moq)
                if cny is None:
                    self.assertIsNone(fields["price"])
                else:
                    self.assertAlmostEqual(fields["price"], cny)


class TestDraftLanguage(unittest.TestCase):
    def _action(self, price: float) -> TacticalActionObject:
        return TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id="UX_R1",
            authorized_price=price,
            walk_away_ceiling=16.2,
            product_name="Widget",
            moq=500,
        )

    def test_english_supplier_gets_an_english_draft_at_the_engine_price(self):
        self.assertEqual(supplier_message_language(WALKTHROUGH), "en")
        draft = render_supplier_reply(self._action(12.41), WALKTHROUGH)
        self.assertIn("Our offer is CNY 12.41", draft)
        self.assertNotIn("3.20", draft)
        self.assertNotIn("出廠價", draft)

    def test_chinese_supplier_keeps_a_chinese_draft(self):
        text = "最低批發價 14.80 元/把，500個起訂"
        self.assertEqual(supplier_message_language(text), "zh")
        draft = render_supplier_reply(self._action(12.41), text)
        self.assertIn("¥12.41", draft)
        self.assertNotIn("Our offer is CNY", draft)


class TestDeskRoundAndCommand(unittest.TestCase):
    def test_usd_quote_does_not_become_the_cny_counter(self):
        client = TestClient(api_main.app)
        created = client.post(
            "/api/v1/sessions",
            json={
                "session_id": "UX_USD_01",
                "product_name": "Widget",
                "target_price": 12.0,
                "ceiling_price": 16.2,
                "moq": 5000,
            },
        )
        self.assertEqual(created.status_code, 200)
        response = client.post(
            "/api/v1/sessions/UX_USD_01/round",
            json={"opponent_raw_text": WALKTHROUGH, "mode": "copilot"},
        )
        self.assertEqual(response.status_code, 200, response.text[:400])
        body = response.json()
        reply = body["reply_text"]
        authorized = body["action_object"]["authorized_price"]
        self.assertTrue(reply.startswith("Thank you. Our offer is CNY "))
        self.assertIn(f"{authorized:.2f}", reply)
        self.assertNotIn("3.20", reply)
        turn = body["session"]["history"][-1]
        self.assertIsNone(turn["parsed_price"])
        entities = turn["metadata"]["entities"]
        self.assertEqual(entities["currency"], "USD")
        self.assertEqual(entities["moq"], 500)
        self.assertAlmostEqual(entities["quoted_amount"], 3.2)
        command = client.post(
            "/api/v1/sessions/UX_USD_01/command",
            json={"command": "1"},
        )
        self.assertEqual(command.status_code, 200)
        detail = command.json()["detail"]
        self.assertIn("waiting for a person", detail)
        self.assertNotIn("AWAITING_HUMAN_DECISION", detail)


class TestDeskCopy(unittest.TestCase):
    def test_visible_fixes_are_in_the_desk(self):
        html = open(os.path.join(ROOT, "src/web/templates/index.html"), encoding="utf-8").read()
        css = open(os.path.join(ROOT, "src/web/static/css/theme.css"), encoding="utf-8").read()
        js = open(os.path.join(ROOT, "src/web/static/js/app.js"), encoding="utf-8").read()
        market = open(os.path.join(ROOT, "src/web/static/js/market_intel.js"), encoding="utf-8").read()
        self.assertLess(html.index('id="quoteSheet"'), html.index('id="tacticPanel"'))
        self.assertIn('id="approveBtn"', html)
        self.assertIn('id="holdBtn"', html)
        self.assertIn('id="rejectBtn"', html)
        self.assertIn('id="themeToggle"', html)
        self.assertIn('id="langToggle"', html)
        self.assertIn('id="openSandboxBtn"', html)
        self.assertNotIn("Connecting…", html)
        self.assertNotIn("VLLM_API_KEY", html)
        self.assertIn(".chart-empty[hidden]", css)
        self.assertIn(".quote-sample-img[hidden]", css)
        self.assertIn("function decide(", js)
        self.assertIn("function tfAmount(", js)
        self.assertIn("isHiddenSession", js)
        self.assertIn("Enter an HS code or item keyword", market)
        self.assertIn("sparkCaption", market)
        self.assertNotIn("shown.slice(0, 96)", market)
