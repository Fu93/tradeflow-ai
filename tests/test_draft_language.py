"""The paste-back draft keeps the supplier's language after approve and reload."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.session.schema import SessionSpec

ENGLISH = (
    "Hi, the price is 3.20 USD/pc for 1000 pcs, MOQ 500. "
    "Lead time 30 days, 30% deposit."
)
CHINESE = "最低批發價 14.80 元/把，500個起訂"


class TestDraftLanguage(unittest.TestCase):
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

    def _open(self, session_id: str, text: str) -> dict:
        spec = SessionSpec(
            session_id=session_id,
            product_name="Widget",
            target_price=12.0,
            ceiling_price=16.2,
            moq=1000,
            max_rounds=5,
        )
        api_main.session_mgr.create_session(spec)
        opened = self.client.post(
            f"/api/v1/sessions/{session_id}/round",
            json={"opponent_raw_text": text, "mode": "copilot"},
            headers=self.headers,
        )
        self.assertEqual(opened.status_code, 200, opened.text[:400])
        return opened.json()

    def _approve(self, session_id: str) -> dict:
        approved = self.client.post(
            f"/api/v1/sessions/{session_id}/approve",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(approved.status_code, 200, approved.text[:400])
        return approved.json()

    def _stored(self, session_id: str) -> dict:
        fetched = self.client.get(
            f"/api/v1/sessions/{session_id}",
            headers=self.headers,
        )
        self.assertEqual(fetched.status_code, 200, fetched.text[:400])
        return fetched.json()

    def test_english_draft_survives_hold_approve_and_fetch(self):
        body = self._open("DRAFT_EN_01", ENGLISH)
        price = body["recommended_offer_price"]
        reply = body["reply_text"]
        self.assertIn(f"CNY {price:.2f}", reply)
        self.assertNotRegex(reply, r"[\u4e00-\u9fff]")
        opponent = body["session"]["history"][-1]
        self.assertEqual(opponent["metadata"]["draft_language"], "en")
        self.assertEqual(opponent["metadata"]["generated_reply"], reply)

        held = self.client.post(
            "/api/v1/sessions/DRAFT_EN_01/approve",
            json={"action": "hold"},
            headers=self.headers,
        )
        self.assertEqual(held.status_code, 200, held.text[:400])
        self.assertEqual(held.json()["autopilot_state"], "AWAITING_HUMAN_DECISION")
        after_hold = self._stored("DRAFT_EN_01")
        self.assertEqual(after_hold["history"][-1]["metadata"]["generated_reply"], reply)
        self.assertEqual(after_hold["history"][-1]["metadata"]["draft_language"], "en")
        self.assertNotIn("buyer_human_approved", [turn["sender"] for turn in after_hold["history"]])

        approved = self._approve("DRAFT_EN_01")
        self.assertEqual(approved["applied_price"], price)
        self.assertEqual(approved["reply_text"], reply)
        self.assertIn(f"CNY {price:.2f}", approved["reply_text"])
        self.assertNotRegex(approved["reply_text"], r"[\u4e00-\u9fff]")

        stored = self._stored("DRAFT_EN_01")
        human = stored["history"][-1]
        self.assertEqual(human["sender"], "buyer_human_approved")
        self.assertEqual(human["raw_text"], reply)
        self.assertEqual(human["metadata"]["draft_language"], "en")
        self.assertEqual(human["parsed_price"], price)

    def test_chinese_draft_stays_chinese(self):
        body = self._open("DRAFT_ZH_01", CHINESE)
        price = body["recommended_offer_price"]
        reply = body["reply_text"]
        self.assertRegex(reply, r"[\u4e00-\u9fff]")
        self.assertNotIn("Our offer is CNY", reply)
        self.assertEqual(body["session"]["history"][-1]["metadata"]["draft_language"], "zh")

        approved = self._approve("DRAFT_ZH_01")
        self.assertEqual(approved["applied_price"], price)
        self.assertEqual(approved["reply_text"], reply)
        self.assertRegex(approved["reply_text"], r"[\u4e00-\u9fff]")
        self.assertNotIn("Our offer is CNY", approved["reply_text"])

        stored = self._stored("DRAFT_ZH_01")
        human = stored["history"][-1]
        self.assertEqual(human["raw_text"], reply)
        self.assertEqual(human["metadata"]["draft_language"], "zh")
        self.assertEqual(human["parsed_price"], price)
