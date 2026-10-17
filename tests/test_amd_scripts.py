"""The AMD evidence scripts must be honest when this machine has no key and no GPU."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _run(script: str, args: list[str], env: dict) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        [PY, os.path.join(ROOT, "scripts", script), *args],
        cwd=ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


class TestAmdEvidenceScripts(unittest.TestCase):
    def test_endpoint_probe_skips_a_placeholder_key(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "evidence.json")
            proc = _run(
                "verify_amd_endpoint.py",
                ["--evidence", out],
                {
                    "VLLM_API_KEY": "__FILL_ME__",
                    "VLLM_BASE_URL": "https://developer.amd.com.cn/radeon/api/v1",
                    "VLLM_MODEL": "Qwen3.8-Flash-Next",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("skipped_no_credentials", proc.stdout)
            payload = json.loads(open(out, encoding="utf-8").read())
            self.assertEqual(payload["status"], "skipped_no_credentials")
            self.assertFalse(payload["key_present"])
            blob = json.dumps(payload)
            self.assertNotIn("__FILL_ME__", blob)

    def test_gpu_benchmark_does_not_claim_an_amd_run_on_cpu(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "bench.json")
            proc = _run(
                "benchmark_amd_gpu.py",
                ["--force-cpu", "--samples", "64", "--evidence", out],
                {"TRADEFLOW_SOLVER_BACKEND": "numpy"},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            payload = json.loads(open(out, encoding="utf-8").read())
            self.assertFalse(payload["ran_on_amd_gpu"])
            self.assertEqual(payload["backend"]["actual"], "numpy")
            self.assertGreater(payload["measurement"]["elapsed_seconds"], 0)
            self.assertTrue(payload["measurement"]["mi300x_bound_is_estimate"])
            self.assertFalse(payload["measurement"]["mi300x_bound_is_measurement"])
            self.assertIsNotNone(payload["measurement"]["mi300x_hbm3_transfer_lower_bound_seconds"])


if __name__ == "__main__":
    unittest.main()
