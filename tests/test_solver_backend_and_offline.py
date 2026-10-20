"""Solver backend selection and offline-demo credential handling.

Default behaviour stays NumPy. Placeholder keys copied from .env.example must
not place a network call.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.engine.backend import configured_use_gpu, describe_solver_backend, resolve_use_gpu
from src.engine.llm_config import evolus_credentials, real_secret, resolve_chat_endpoint
from src.engine.solver import solve
from src.app.orchestrator import draft_email
from src.engine.bilingual import CrossLingualSanityChecker, InboundIntentClassifier


class TestSolverBackendConfig(unittest.TestCase):
    def test_default_and_unknown_stay_on_cpu(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRADEFLOW_SOLVER_BACKEND", None)
            self.assertFalse(configured_use_gpu())
            self.assertFalse(resolve_use_gpu(None))
        with patch.dict(os.environ, {"TRADEFLOW_SOLVER_BACKEND": "not-a-backend"}):
            self.assertFalse(configured_use_gpu())

    def test_explicit_flag_overrides_config(self):
        with patch.dict(os.environ, {"TRADEFLOW_SOLVER_BACKEND": "cupy"}):
            self.assertTrue(configured_use_gpu())
            self.assertFalse(resolve_use_gpu(False))
            self.assertTrue(resolve_use_gpu(True))
            self.assertTrue(resolve_use_gpu(None))

    def test_numpy_name_forces_cpu_even_if_cupy_word_absent(self):
        with patch.dict(os.environ, {"TRADEFLOW_SOLVER_BACKEND": "numpy"}):
            self.assertFalse(resolve_use_gpu(None))

    def test_solve_still_returns_on_cpu(self):
        with patch.dict(os.environ, {"TRADEFLOW_SOLVER_BACKEND": "numpy"}):
            res = solve(n_opp_samples=32, value_ceiling=1.35)
        self.assertEqual(res["n_samples"], 32)
        self.assertIn("best", res)

    def test_describe_does_not_claim_an_amd_gpu_here(self):
        info = describe_solver_backend()
        self.assertIn(info["actual"], ("numpy", "cupy"))
        if info["actual"] == "numpy":
            self.assertFalse(info["ran_on_amd_gpu"])


class TestPlaceholderCredentials(unittest.TestCase):
    def test_real_secret_rejects_placeholders(self):
        self.assertIsNone(real_secret(None))
        self.assertIsNone(real_secret(""))
        self.assertIsNone(real_secret("__FILL_ME__"))
        self.assertIsNone(real_secret("  changeme  "))
        self.assertEqual(real_secret("dummy_key"), "dummy_key")

    def test_evolus_key_is_not_sent_to_the_amd_host(self):
        env = {
            "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
            "VLLM_API_KEY": "__FILL_ME__",
            "EVOLUS_API_KEY": "dummy_key",
            "EVOLUS_APP_ID": "__FILL_ME__",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertIsNone(resolve_chat_endpoint())
            self.assertIsNone(evolus_credentials())

    def test_evolus_only_config_still_resolves(self):
        env = {
            "EVOLUS_API_KEY": "dummy_key",
            "EVOLUS_APP_ID": "app-123",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("VLLM_API_KEY", None)
            os.environ.pop("VLLM_BASE_URL", None)
            endpoint = resolve_chat_endpoint()
            self.assertIsNotNone(endpoint)
            self.assertEqual(endpoint["provider"], "evolus")
            self.assertIn("evolus.ai", endpoint["base_url"])

    def test_placeholder_draft_does_not_call_the_network(self):
        env = {
            "EVOLUS_API_KEY": "__FILL_ME__",
            "EVOLUS_APP_ID": "__FILL_ME__",
            "VLLM_API_KEY": "__FILL_ME__",
            "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
        }
        with patch.dict(os.environ, env, clear=False), patch("requests.post") as post:
            text = draft_email("Supplier quoted 15 yuan at MOQ 500, please draft a reply.")
        post.assert_not_called()
        self.assertIn("offline stub", text)

    def test_placeholder_stage2_does_not_call_the_network(self):
        env = {
            "EVOLUS_API_KEY": "__FILL_ME__",
            "VLLM_API_KEY": "__FILL_ME__",
            "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
        }
        with patch.dict(os.environ, env, clear=False), patch("requests.post") as post:
            res = InboundIntentClassifier.classify_with_llm("工廠今天師傅請假，明天再聊。")
        post.assert_not_called()
        self.assertIsNone(res)

    def test_placeholder_nativeness_audit_does_not_call_the_network(self):
        env = {
            "EVOLUS_API_KEY": "__FILL_ME__",
            "VLLM_API_KEY": "__FILL_ME__",
            "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
        }
        with patch.dict(os.environ, env, clear=False), patch("requests.post") as post:
            audit = CrossLingualSanityChecker.check_semantic_nativeness("这个价可以谈，你再让一点。")
        post.assert_not_called()
        self.assertTrue(audit["is_native"])

    def test_health_stays_offline_with_placeholder_keys(self):
        from fastapi.testclient import TestClient
        from src.app.main import app

        env = {
            "EVOLUS_API_KEY": "__FILL_ME__",
            "EVOLUS_APP_ID": "__FILL_ME__",
            "VLLM_API_KEY": "__FILL_ME__",
            "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
            "TRADEFLOW_SOLVER_BACKEND": "numpy",
        }
        with patch.dict(os.environ, env, clear=False):
            body = TestClient(app).get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["offline_demo"])
        self.assertFalse(body["llm"]["configured"])
        self.assertEqual(body["solver"]["actual"], "numpy")
        self.assertFalse(body["solver"]["ran_on_amd_gpu"])


if __name__ == "__main__":
    unittest.main()
