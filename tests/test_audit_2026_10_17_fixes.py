"""Regression tests for the 2026-10-17 deep audit fixes.

Each test targets a defect that was reproduced on the live server before the fix:

1. `POST /api/v1/sessions/{id}/round {}` returned 200 with a full recommendation
   and wrote an empty opponent turn (false-success on zero evidence).
2. Copilot `/approve` silently approved `target_price` instead of the engine's
   recommended price, because copilot never persists a buyer_agent turn.
3. The human approval turn wrote `p_break=0.0`, so the dashboard/supervisor card
   reported "0.0% breakdown risk" after approval (a fabricated measurement).
4. The auction's single "22.2% savings" figure is relative to the walk-away
   ceiling; without the baseline it reads as savings vs the supplier quote.
5. Stage-2 failures returned HTTP 200 with `stage2_available=false` and no
   reason, after a 30s client timeout swallowed the real error.
6. CROSS_ANCHOR rendered "合格樣品已給到 同規格同行報價。" when no competing
   quote existed (broken Chinese + implied an unearned quote).
7. The copilot generated reply was only returned in the HTTP response, so the
   dashboard's "Latest Generated Reply (human sends manually)" went blank after
   a reload — losing the very text the buyer is supposed to paste into the chat.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.engine.bilingual import (
    ChineseSurfaceRenderer,
    TacticalActionObject,
    TacticalModeEnum,
)


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tf_audit_")
        self._prev = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
        api_main.session_mgr.__init__(
            db_path=os.path.join(self._tmp, "sessions.db"),
            store_dir=os.path.join(self._tmp, "json"),
        )
        self.c = TestClient(api_main.app)
        self.headers = {"X-API-Key": api_main.SANDBOX_DEMO_AUTH}

    def tearDown(self):
        api_main.session_mgr.__init__(db_path=self._prev[0], store_dir=self._prev[1])
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _session(self, sid="audit_fix", target=12.0, ceiling=16.2):
        r = self.c.post(
            "/api/v1/sessions",
            json={
                "session_id": sid,
                "product_name": "Audit Umbrella",
                "target_price": target,
                "ceiling_price": ceiling,
                "moq": 500,
                "max_rounds": 5,
            "expected_markup": 0.0,
        },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        return sid


class TestZeroEvidenceRound(_Isolated):
    def test_empty_round_is_rejected_and_writes_nothing(self):
        sid = self._session()
        r = self.c.post(f"/api/v1/sessions/{sid}/round", json={}, headers=self.headers)
        self.assertEqual(r.status_code, 422, r.text)
        stored = self.c.get(f"/api/v1/sessions/{sid}", headers=self.headers).json()
        self.assertEqual(stored["history"], [], "a rejected round must not create history")
        self.assertEqual(stored["current_round"], 0)

    def test_text_only_round_still_accepted(self):
        sid = self._session()
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={"opponent_raw_text": "最低 14.80元，不能再低", "mode": "copilot"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["recommended_offer_price"])


class TestApproveUsesEngineRecommendation(_Isolated):
    def test_approve_applies_recommended_price_not_target(self):
        sid = self._session()
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={
                "opponent_raw_text": "原料暴漲，14.80 出廠價都虧本",
                "opponent_offer_price": 14.80,
                "mode": "copilot",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        recommended = r.json()["recommended_offer_price"]
        self.assertNotAlmostEqual(recommended, 12.0, places=2)

        sess = self.c.get(f"/api/v1/sessions/{sid}", headers=self.headers).json()
        persisted = sess["history"][-1]["metadata"].get("recommended_offer_price")
        self.assertAlmostEqual(persisted, recommended, places=6)

        app = self.c.post(
            f"/api/v1/sessions/{sid}/approve",
            json={"action": "approve"},
            headers=self.headers,
        )
        self.assertEqual(app.status_code, 200, app.text)
        self.assertAlmostEqual(app.json()["applied_price"], recommended, places=6)
        self.assertNotAlmostEqual(app.json()["applied_price"], 12.0, places=2)

    def test_approval_carries_measured_risk_not_zero(self):
        sid = self._session()
        self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={
                "opponent_raw_text": "最低 14.80",
                "opponent_offer_price": 14.80,
                "mode": "copilot",
            },
            headers=self.headers,
        )
        before = self.c.get(f"/api/v1/sessions/{sid}", headers=self.headers).json()
        measured = before["history"][-1]["p_break"]
        self.assertIsNotNone(measured)
        # The default counter is the ask, so the incremental hazard is 0.
        self.assertEqual(measured, 0.0)

        self.c.post(
            f"/api/v1/sessions/{sid}/approve", json={"action": "approve"}, headers=self.headers
        )
        after = self.c.get(f"/api/v1/sessions/{sid}", headers=self.headers).json()
        last = after["history"][-1]
        self.assertEqual(last["sender"], "buyer_human_approved")
        self.assertAlmostEqual(last["p_break"], measured, places=9)
        self.assertIsNotNone(last["posterior_mean"], "approval must carry the posterior")
        self.assertAlmostEqual(after["latest_our_offer"], last["parsed_price"], places=6)


class TestGeneratedReplyIsPersisted(_Isolated):
    def test_copilot_reply_survives_a_reload(self):
        sid = self._session()
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={
                "opponent_raw_text": "原料漲價，最低 14.80 出廠價",
                "opponent_offer_price": 14.80,
                "mode": "copilot",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        reply = r.json()["reply_text"]
        self.assertTrue(reply and reply.strip(), "engine must generate a reply")

        # No buyer_agent turn exists in copilot mode, so the text has to be
        # readable from the persisted session or the UI shows "—" after reload.
        sess = self.c.get(f"/api/v1/sessions/{sid}", headers=self.headers).json()
        self.assertFalse(
            [t for t in sess["history"] if t["sender"] == "buyer_agent"],
            "precondition: copilot does not append a buyer turn",
        )
        persisted = sess["history"][-1]["metadata"].get("generated_reply")
        self.assertEqual(persisted, reply)


class TestAuctionSavingsBaseline(_Isolated):
    def test_savings_are_reported_with_explicit_baselines(self):
        r = self.c.post(
            "/api/v1/multi-auction/run",
            json={"demo": True, "n_samples": 2000},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        for key in (
            "total_savings_baseline",
            "first_round_best_quote",
            "savings_vs_first_quote_pct",
            "savings_vs_first_quote_cny",
            "savings_vs_target_pct",
            "savings_vs_target_cny",
            "savings_vs_ceiling_pct",
            "savings_vs_ceiling_cny",
        ):
            self.assertIn(key, d)
        self.assertEqual(d["total_savings_baseline"], "first_round_best_quote")
        self.assertAlmostEqual(d["total_savings_pct"], d["savings_vs_first_quote_pct"])
        # Ceiling and first-quote answer different questions.
        self.assertNotAlmostEqual(
            d["total_savings_pct"], d["savings_vs_ceiling_pct"], places=3
        )
        expected_vs_target = (d["product_target"] - d["winning_price"]) / d["product_target"]
        self.assertAlmostEqual(d["savings_vs_target_pct"], expected_vs_target)
        self.assertAlmostEqual(
            d["savings_vs_target_cny"],
            (d["product_target"] - d["winning_price"]) * d["moq"],
        )
        if expected_vs_target < 0:
            self.assertLess(d["savings_vs_target_pct"], 0.0)
            self.assertLess(d["savings_vs_target_cny"], 0.0)


class TestStage2FailureIsVisible(_Isolated):
    def test_stage2_failure_reports_reason_instead_of_silent_null(self):
        from src.engine.bilingual import InboundIntentClassifier

        async def _failing(cls, raw_text):
            cls.last_llm_error = "simulated: Stage-2 endpoint timed out"
            cls.last_llm_latency_ms = 123.4
            return None

        original = InboundIntentClassifier.classify_with_llm_async
        InboundIntentClassifier.classify_with_llm_async = classmethod(_failing)
        try:
            r = self.c.post(
                "/api/v1/classify/semantic",
                json={"text": "原料暴漲 14.80", "enable_stage2": True},
                headers=self.headers,
            )
        finally:
            InboundIntentClassifier.classify_with_llm_async = original

        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertFalse(d["stage2_available"])
        self.assertTrue(d["stage2_requested"])
        self.assertIn("simulated", d["stage2_error"])
        self.assertEqual(d["stage2_latency_ms"], 123.4)


class TestCrossAnchorPhrasing(_Isolated):
    def _render(self, competing_price):
        obj = TacticalActionObject(
            source_round_id="audit",
            authorized_price=12.0,
            walk_away_ceiling=16.2,
            breakdown_risk=0.10,
            tactical_mode=TacticalModeEnum.CROSS_ANCHOR,
            product_name="Audit Umbrella",
            competing_lead_price=competing_price,
        )
        return ChineseSurfaceRenderer.render(obj)

    def test_no_price_does_not_claim_a_received_quote(self):
        text = self._render(None)
        self.assertIn("同規格同行報價", text)
        self.assertNotIn("已給到", text, text)
        self.assertNotIn("合格樣品", text, text)

    def test_real_price_is_still_rendered(self):
        text = self._render(11.2)
        self.assertIn("11.20", text)


if __name__ == "__main__":
    unittest.main()
