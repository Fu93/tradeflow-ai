"""Fail-closed pre-push secret scan 回歸測試 (tests/test_secret_scan.py).

對應 DEEP_DEFECT_AUDIT.md §6.3：
`scripts/secret_scan.py` 從 `.env` 讀取要搜尋的憑證字串，再掃描工作樹。
在 CI 上沒有 `.env`，於是 `secrets` 為空，它掃描 0 個憑證後仍印出
`[PASS] No credential value found` 並 return 0 —— 這個閘門**從不可能失敗**。

本測試固定修正後的行為：
  * 有 .env + 有洩漏  -> exit 1
  * 有 .env + 乾淨    -> exit 0
  * 無 .env + 無 flag -> exit 1（fail-closed）
  * 無 .env + 有 flag -> exit 0（明確接受）
  * 有 .env 但解析不出憑證 -> exit 1（沒有針的掃描器不能宣稱 PASS）
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "secret_scan.py")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class TestSecretScanFailClosed(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------
    def _make_env(self, path: str, secret: str = "SUPERSECRETVALUE123456") -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"MY_API_KEY={secret}\n")
            f.write("VLLM_BASE_URL=https://example.invalid/v1\n")

    def _make_tree(self, *, leak: str | None = None) -> str:
        tree = os.path.join(self.tmp, "tree")
        os.makedirs(tree, exist_ok=True)
        with open(os.path.join(tree, "clean.txt"), "w", encoding="utf-8") as f:
            f.write("nothing sensitive here\n")
        if leak:
            with open(os.path.join(tree, "leaked.py"), "w", encoding="utf-8") as f:
                f.write(f'KEY = "{leak}"\n')
        return tree

    # -- cases ---------------------------------------------------------
    def test_env_present_with_leak_fails(self):
        env = os.path.join(self.tmp, ".env")
        secret = "SUPERSECRETVALUE123456"
        self._make_env(env, secret)
        tree = self._make_tree(leak=secret)

        r = _run(["--env", env, "--root", tree])
        self.assertEqual(r.returncode, 1, f"expected leak detection, got:\n{r.stdout}\n{r.stderr}")
        self.assertIn("SECRET LEAK", r.stdout)

    def test_env_present_clean_passes(self):
        env = os.path.join(self.tmp, ".env")
        self._make_env(env)
        tree = self._make_tree()

        r = _run(["--env", env, "--root", tree])
        self.assertEqual(r.returncode, 0, f"expected clean pass, got:\n{r.stdout}\n{r.stderr}")
        self.assertIn("[PASS]", r.stdout)

    def test_env_absent_without_flag_fails_closed(self):
        """核心回歸：這就是 CI 上實際發生的情況（沒有 .env）。"""
        missing = os.path.join(self.tmp, "does-not-exist.env")
        tree = self._make_tree()

        r = _run(["--env", missing, "--root", tree])
        self.assertEqual(
            r.returncode, 1,
            "缺 .env 時必須 fail-closed；修正前這裡會回 0 並印 PASS",
        )
        self.assertNotIn("[PASS] No credential value found", r.stdout)
        self.assertIn("Refusing to report PASS", r.stdout)

    def test_env_absent_with_flag_passes_but_does_not_claim_verification(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        tree = self._make_tree()

        r = _run(["--env", missing, "--root", tree, "--allow-missing-env"])
        self.assertEqual(r.returncode, 0, f"flag should tolerate absence:\n{r.stdout}")
        # 必須明確說明這不是安全驗證，不得印出誤導性的 PASS。
        self.assertIn("NOT a security verification", r.stdout)
        self.assertNotIn("[PASS] No credential value found", r.stdout)

    def test_empty_needle_set_fails(self):
        """.env 存在但沒有任何 >=12 字元的憑證值 -> 不能宣稱 PASS。"""
        env = os.path.join(self.tmp, ".env")
        with open(env, "w", encoding="utf-8") as f:
            f.write("SHORT=x\n")  # 太短，會被忽略
            f.write("# only comments\n")
        tree = self._make_tree()

        r = _run(["--env", env, "--root", tree])
        self.assertEqual(r.returncode, 1, f"empty needle set must fail:\n{r.stdout}")
        self.assertIn("no credential values were parsed", r.stdout)

    def test_public_config_values_do_not_fail(self):
        """URL/model 等公開設定不應被視為洩漏。"""
        env = os.path.join(self.tmp, ".env")
        self._make_env(env)
        tree = os.path.join(self.tmp, "tree2")
        os.makedirs(tree, exist_ok=True)
        with open(os.path.join(tree, "doc.md"), "w", encoding="utf-8") as f:
            f.write("Endpoint: https://example.invalid/v1\n")  # VLLM_BASE_URL 的值

        r = _run(["--env", env, "--root", tree])
        self.assertEqual(r.returncode, 0, f"public config must not fail:\n{r.stdout}")

    def test_real_repo_env_still_scans(self):
        """真實 repo 的 .env 存在時，掃描本身仍應可執行（本機開發者流程）。"""
        real_env = os.path.join(ROOT, ".env")
        if not os.path.exists(real_env):
            self.skipTest("no .env in this checkout")
        tree = self._make_tree()
        r = _run(["--env", real_env, "--root", tree])
        self.assertEqual(r.returncode, 0, f"clean tree should pass:\n{r.stdout}")


if __name__ == "__main__":
    unittest.main()
