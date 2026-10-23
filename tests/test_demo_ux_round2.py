"""Walkthrough fixes: English quote fields, currency labels, and the decision desk."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.engine.quote_fields import describe_supplier_price, extract_stage1
from src.engine.quote_read import read_supplier_quote
from src.engine.bilingual import render_supplier_reply, supplier_message_language
from src.engine.bilingual import TacticalActionObject, CURRENT_SCHEMA_VERSION
from src.legacy.session.schema import AutopilotState, SessionSpec

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
            ("an order of 2000 pcs", "missing", None, None),
            (
                "an order of 2000 pcs. Minimum order quantity: 500",
                "missing",
                500,
                None,
            ),
            ("3,20 EUR, MOQ 1,000 pcs", "3.20 EUR", 1000, None),
            ("GBP 4,50 per piece, MOQ 2.000 pcs", "4.50 GBP", 2000, None),
            ("1.000,50 EUR, MOQ 1,000", "1000.50 EUR", 1000, None),
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
            "expected_markup": 0.0,
        },
        )
        self.assertEqual(created.status_code, 200)
        response = client.post(
            "/api/v1/sessions/UX_USD_01/round",
            json={"opponent_raw_text": WALKTHROUGH, "mode": "copilot"},
        )
        self.assertEqual(response.status_code, 422, response.text[:400])
        self.assertIn("Confirm the supplier unit price", response.json()["detail"])
        stored = client.get("/api/v1/sessions/UX_USD_01").json()
        self.assertEqual(stored["history"], [])


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
        self.assertNotIn('action === "hold" ? "approve"', js)
        self.assertIn("clearStaleDecisionPrompt", js)
        self.assertIn("Lead time", js)
        self.assertIn("% deposit", js)
        self.assertNotIn("deposit fraction", js)
        self.assertNotIn("lead time days", js)
        self.assertIn("These fields are the supplier's numbers, not our price.", js)
        self.assertNotIn("Not recorded.", js)
        self.assertIn("for ${tfCount(pieceCount)} pcs", js)
        self.assertNotIn("walk-away ceiling</span>", js)
        self.assertIn('Number(s.rounds) === 1 ? "round" : "rounds"', js)
        self.assertIn("white-space: nowrap", css)
        self.assertIn(".market-hero.is-plain", css)
        self.assertIn(".meter-row", css)
        self.assertIn('class="masthead-about"', html)
        self.assertIn('id="quoteAbout"', html)
        self.assertIn('id="amdCallout"', html)
        self.assertIn("hidden", html[html.index('id="amdCallout"'):html.index('id="amdCallout"') + 120])
        self.assertIn('preserveAspectRatio", "none"', market)
        self.assertIn("No customs figures for this code.", market)
        self.assertIn("Escape", market)
        self.assertIn("root._suggestTimer", market)
        self.assertIn("is-plain", market)


class TestHumanDecisionApi(unittest.TestCase):
    def setUp(self):
        self.orig_db_path = api_main.session_mgr.db_path
        self.orig_store_dir = api_main.session_mgr.store_dir
        self.temp_dir = tempfile.mkdtemp()
        api_main.session_mgr.__init__(
            db_path=os.path.join(self.temp_dir, "sessions.db"),
            store_dir=os.path.join(self.temp_dir, "json"),
        )
        self.client = TestClient(api_main.app)
        self.headers = {"X-API-Key": api_main.SANDBOX_DEMO_TOKEN}

    def tearDown(self):
        api_main.session_mgr.__init__(
            db_path=self.orig_db_path,
            store_dir=self.orig_store_dir,
        )
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _awaiting(self, session_id: str) -> None:
        spec = SessionSpec(
            session_id=session_id,
            product_name="Widget",
            target_price=12.0,
            ceiling_price=16.2,
            moq=1000,
            max_rounds=5,
            expected_markup=0.0,
        )
        api_main.session_mgr.create_session(spec)
        opened = self.client.post(
            f"/api/v1/sessions/{session_id}/round",
            json={
                "opponent_raw_text": "最低 14.80 元，500個起訂",
                "opponent_offer_price": 14.80,
                "mode": "copilot",
            },
            headers=self.headers,
        )
        self.assertEqual(opened.status_code, 422, opened.text[:400])
        self.assertIn("unconfirmed numbers used in decisions: 1", opened.json()["detail"])
        stored = api_main.session_mgr.get_session(session_id)
        self.assertEqual(list(stored.history), [])

    def test_approve_then_second_approve_is_conflict(self):
        self._awaiting("UX_APPROVE_01")
        first = self.client.post(
            "/api/v1/sessions/UX_APPROVE_01/approve",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 409, first.text[:400])
        self.assertIn("not waiting for a decision", first.json()["detail"])
        second = self.client.post(
            "/api/v1/sessions/UX_APPROVE_01/approve",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 409, second.text[:400])
        self.assertNotEqual(second.status_code, 500)
        stored = api_main.session_mgr.get_session("UX_APPROVE_01")
        self.assertEqual(list(stored.history), [])
        self.assertNotIn(stored.state, (AutopilotState.COPILOT_MONITORING, AutopilotState.DEAL_CLOSED))

    def test_hold_keeps_the_deal_waiting(self):
        self._awaiting("UX_HOLD_01")
        held = self.client.post(
            "/api/v1/sessions/UX_HOLD_01/approve",
            json={"action": "hold"},
            headers=self.headers,
        )
        self.assertEqual(held.status_code, 409, held.text[:400])
        self.assertIn("not waiting for a decision", held.json()["detail"])
        stored = api_main.session_mgr.get_session("UX_HOLD_01")
        self.assertEqual(list(stored.history), [])
        senders = [turn.sender for turn in stored.history]
        self.assertNotIn("buyer_human_approved", senders)
        approved = self.client.post(
            "/api/v1/sessions/UX_HOLD_01/approve",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(approved.status_code, 409, approved.text[:400])
        self.assertNotIn("applied_price", approved.json())

    def test_reject_then_another_reject_is_conflict(self):
        self._awaiting("UX_REJECT_01")
        rejected = self.client.post(
            "/api/v1/sessions/UX_REJECT_01/approve",
            json={"action": "reject"},
            headers=self.headers,
        )
        self.assertEqual(rejected.status_code, 200, rejected.text[:400])
        self.assertEqual(rejected.json()["autopilot_state"], "WALK_AWAY")
        again = self.client.post(
            "/api/v1/sessions/UX_REJECT_01/approve",
            json={"action": "reject"},
            headers=self.headers,
        )
        self.assertEqual(again.status_code, 409, again.text[:400])
        self.assertIn("cannot be rejected", again.json()["detail"])
        stored = api_main.session_mgr.get_session("UX_REJECT_01")
        self.assertEqual(stored.state, AutopilotState.WALK_AWAY)
