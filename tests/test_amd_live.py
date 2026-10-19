"""AMD live mode reads a quote. It cannot change the solver's price."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.app.rate_limit import amd_rate_limiter
from src.engine.bilingual import InboundIntentClassifier, InboundTacticalClassification
from src.engine.quote_fields import price_the_solver_may_use, validate_fields
from src.engine.quote_read import read_supplier_quote

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (
    "SIMULATION — not a real supplier screenshot.\n"
    "单价：14.80元。5000個。21天。30%定金。\n"
)


class TestPriceGuard(unittest.TestCase):
    def test_llm_price_is_ignored_by_the_helper(self):
        self.assertEqual(price_the_solver_may_use(14.8, 1.0, 99.0), 14.8)
        self.assertEqual(price_the_solver_may_use(None, 14.8, 1.0), 14.8)
        self.assertIsNone(price_the_solver_may_use(None, None, 1.0))

    def test_bounds_reject_a_wild_model_payload(self):
        checked = validate_fields(
            {"price": 1e9, "moq": -4, "lead_time_days": 9000, "deposit_pct": 90, "our_price": 1}
        )
        self.assertIsNone(checked["price"])
        self.assertIsNone(checked["moq"])
        self.assertIsNone(checked["lead_time_days"])
        self.assertIsNone(checked["deposit_pct"])
        self.assertGreaterEqual(len(checked["rejected"]), 3)

    def test_round_keeps_the_stage1_price_when_stage2_disagrees(self):
        def fake_classify(raw_text, enable_llm_fallback=True):
            stage1 = InboundIntentClassifier.classify_stage1_only(raw_text)
            return InboundTacticalClassification(
                primary_intent=stage1.primary_intent,
                confidence=stage1.confidence,
                mentioned_price=1.0,
                demanded_moq=9_999_999,
                tactical_rationale="model wanted a different price",
            )

        client = TestClient(api_main.app)
        for sid in ("GUARD_QUIET", "GUARD_LOUD"):
            created = client.post("/api/v1/sessions", json={
                "session_id": sid,
                "product_name": "Sample",
                "target_price": 12.0,
                "ceiling_price": 16.2,
            })
            self.assertEqual(created.status_code, 200, created.text)
        with patch.object(InboundIntentClassifier, "classify", staticmethod(fake_classify)):
            quiet = client.post(
                "/api/v1/sessions/GUARD_QUIET/round",
                json={"opponent_raw_text": SAMPLE, "mode": "copilot", "amd_live": True},
            )
            loud = client.post(
                "/api/v1/sessions/GUARD_LOUD/round",
                json={
                    "opponent_raw_text": SAMPLE,
                    "mode": "copilot",
                    "deep_semantic": True,
                    "amd_live": True,
                },
            )
        self.assertEqual(quiet.status_code, 200, quiet.text)
        self.assertEqual(loud.status_code, 200, loud.text)
        self.assertEqual(
            quiet.json()["recommended_offer_price"],
            loud.json()["recommended_offer_price"],
        )
        self.assertEqual(loud.json()["price_source"], "stage1_regex")
        self.assertFalse(loud.json()["amd_live"]["decides_price"])
        self.assertEqual(loud.json()["llm_price_ignored"], 1.0)
        self.assertNotEqual(loud.json()["recommended_offer_price"], 1.0)


class TestQuoteRead(unittest.TestCase):
    def test_offline_read_does_not_call_the_network(self):
        with patch("src.engine.quote_read.requests.post") as post:
            result = read_supplier_quote(SAMPLE, amd_live=False)
        post.assert_not_called()
        self.assertEqual(result["status"], "offline_fallback")
        self.assertIsNone(result["buyer_price"])
        self.assertFalse(result["decides_price"])
        self.assertEqual(result["fields"]["price"], 14.8)
        self.assertIn("Offline fallback", result["display"])
        self.assertNotIn("AMD GPU Cloud", result["display"])

    def test_live_reading_cannot_set_a_buyer_price(self):
        class Response:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "price": 1.0,
                                        "moq": 5000,
                                        "lead_time_days": 21,
                                        "deposit_pct": 30,
                                        "reading": "Supplier asked for 1.",
                                        "our_price": 1.0,
                                    }
                                )
                            }
                        }
                    ],
                }

        env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
        with patch.dict(os.environ, env, clear=False), patch(
            "src.engine.quote_read.requests.post", return_value=Response()
        ) as post:
            result = read_supplier_quote(SAMPLE, amd_live=True)
        post.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["buyer_price"])
        self.assertFalse(result["decides_price"])
        self.assertTrue(result["discarded_buyer_price"])
        self.assertEqual(result["fields"]["price"], 1.0)
        self.assertTrue(result["display"].startswith("AMD GPU Cloud · Gemma-4-31B-IT · "))
        self.assertIn("18 tokens", result["display"])

    def test_timeout_is_not_shown_as_a_successful_live_call(self):
        import requests

        env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
        with patch.dict(os.environ, env, clear=False), patch(
            "src.engine.quote_read.requests.post", side_effect=requests.Timeout("slow")
        ):
            result = read_supplier_quote(SAMPLE, amd_live=True)
        self.assertEqual(result["status"], "timeout")
        self.assertNotEqual(result["status"], "ok")
        self.assertTrue(result["display"].startswith("Offline fallback"))
        self.assertNotIn("AMD GPU Cloud", result["display"])
        self.assertIsNone(result["buyer_price"])
        self.assertFalse(result["decides_price"])

    def test_http_error_is_not_shown_as_a_successful_live_call(self):
        class Response:
            status_code = 500
            text = "nope"

            def json(self):
                return {"error": "nope"}

        env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
        with patch.dict(os.environ, env, clear=False), patch(
            "src.engine.quote_read.requests.post", return_value=Response()
        ):
            result = read_supplier_quote(SAMPLE, amd_live=True)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("AMD GPU Cloud", result["display"])
        self.assertIn("HTTP 500", result["error"])
        self.assertIsNone(result["buyer_price"])
        self.assertFalse(result["decides_price"])

    def test_unparsable_body_is_not_shown_as_a_successful_live_call(self):
        class Response:
            status_code = 200
            text = "hello"

            def json(self):
                return {"choices": [{"message": {"content": "not json"}}], "usage": {"total_tokens": 4}}

        env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
        with patch.dict(os.environ, env, clear=False), patch(
            "src.engine.quote_read.requests.post", return_value=Response()
        ):
            result = read_supplier_quote(SAMPLE, amd_live=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "no parsable JSON")
        self.assertTrue(result["display"].startswith("Offline fallback"))
        self.assertNotIn("AMD GPU Cloud", result["display"])
        self.assertIsNone(result["buyer_price"])

    def test_image_without_text_stays_offline_without_a_key(self):
        with patch.dict(os.environ, {"VLLM_API_KEY": ""}, clear=False):
            result = read_supplier_quote("", amd_live=True, image_base64="abcd")
        self.assertEqual(result["status"], "needs_text")
        self.assertFalse(result["image_used"])

    def test_samples_are_labeled_simulations_and_parse(self):
        from src.engine.quote_fields import extract_stage1

        folder = ROOT / "src" / "web" / "static" / "samples"
        for name in ("quote_01", "quote_02", "quote_03"):
            text = (folder / f"{name}.txt").read_text(encoding="utf-8")
            png = (folder / f"{name}.png").read_bytes()
            self.assertIn("SIMULATION", text)
            self.assertIn(b"SIMULATION", png)
            fields = extract_stage1(text)
            self.assertIsNotNone(fields["price"])
            self.assertIsNotNone(fields["moq"])
            self.assertIsNotNone(fields["lead_time_days"])
            self.assertIsNotNone(fields["deposit_pct"])


class TestAmdRateLimit(unittest.TestCase):
    def tearDown(self):
        amd_rate_limiter.reset()

    def test_live_reads_are_capped_when_a_key_is_configured(self):
        amd_rate_limiter.reset(per_minute=2, per_hour=2)
        env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
        client = TestClient(api_main.app)

        class Response:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "usage": {"total_tokens": 3},
                    "choices": [{"message": {"content": '{"price": 14.8, "reading": "ok"}'}}],
                }

        with patch.dict(os.environ, env, clear=False), patch(
            "src.engine.quote_read.requests.post", return_value=Response()
        ):
            first = client.post("/api/v1/quotes/read", json={"text": SAMPLE, "amd_live": True})
            second = client.post("/api/v1/quotes/read", json={"text": SAMPLE, "amd_live": True})
            third = client.post("/api/v1/quotes/read", json={"text": SAMPLE, "amd_live": True})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(third.status_code, 429, third.text)
        self.assertNotIn("test-key-not-real", third.text)
        self.assertNotIn(SAMPLE, third.text)


class TestComparisonFile(unittest.TestCase):
    def test_committed_comparison_is_an_offline_simulation(self):
        payload = json.loads((ROOT / "results" / "llm_vs_tradeflow.json").read_text(encoding="utf-8"))
        self.assertFalse(payload["ran_on_amd"])
        self.assertFalse(payload["live_endpoint_used"])
        self.assertTrue(payload["simulated"])
        self.assertEqual(payload["llm_buyer"], "offline_accept_opening")
        self.assertEqual(payload["seed"], 42)
        self.assertEqual(payload["tradeflow"]["budget_violations"], 0)
        self.assertGreater(payload["offline_llm"]["budget_violation_rate"], 0)


if __name__ == "__main__":
    unittest.main()
