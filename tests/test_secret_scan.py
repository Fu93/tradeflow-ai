"""Pre-push secret scan.

With a .env file, credential values are the needles. Without one, the
scanner still reads tracked files (or the tree, when this is not a
checkout) for private-key and token patterns and says that credential
values were not compared. An empty needle set still refuses to pass.
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

    def test_env_absent_scans_files_and_reports_what_it_did(self):
        """No .env is a real pattern scan, with an explicit report."""
        missing = os.path.join(self.tmp, "does-not-exist.env")
        tree = self._make_tree()

        r = _run(["--env", missing, "--root", tree])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No .env", r.stdout)
        self.assertIn("Credential values were not compared", r.stdout)
        self.assertIn("[PASS] No secret pattern", r.stdout)
        self.assertIn("not a credential-value verification", r.stdout)
        self.assertNotIn("Refusing to report PASS", r.stdout)
        self.assertNotIn("[PASS] No credential value found", r.stdout)

    def test_env_absent_with_flag_still_scans(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        tree = self._make_tree()

        r = _run(["--env", missing, "--root", tree, "--allow-missing-env"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("allow-missing-env", r.stdout)
        self.assertIn("[PASS] No secret pattern", r.stdout)
        self.assertIn("Credential values were not compared", r.stdout)
        self.assertNotIn("[PASS] No credential value found", r.stdout)

    def test_private_key_pattern_fails_without_an_env(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        tree = self._make_tree()
        # Split so this test file is not itself a pattern hit.
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        with open(os.path.join(tree, "leaked.py"), "w", encoding="utf-8") as f:
            f.write(marker + "\n")

        r = _run(["--env", missing, "--root", tree])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Secret pattern found", r.stdout)
        self.assertIn("private key", r.stdout)
        self.assertIn("leaked.py", r.stdout)

    def test_pattern_hit_fails_even_when_env_needles_are_absent_from_the_tree(self):
        env = os.path.join(self.tmp, ".env")
        self._make_env(env)
        tree = self._make_tree()
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        with open(os.path.join(tree, "leaked.py"), "w", encoding="utf-8") as f:
            f.write(marker + "\n")

        r = _run(["--env", env, "--root", tree])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("private key", r.stdout)

    def test_git_index_ignores_an_untracked_secret_and_fails_once_tracked(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        with open(os.path.join(repo, "ok.txt"), "w", encoding="utf-8") as f:
            f.write("clean\n")
        subprocess.run(["git", "-C", repo, "add", "ok.txt"], check=True)
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        with open(os.path.join(repo, "secret.pem"), "w", encoding="utf-8") as f:
            f.write(marker + "\n")

        clean = _run(["--env", missing, "--root", repo])
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.assertIn("tracked", clean.stdout)
        self.assertIn("[PASS] No secret pattern", clean.stdout)

        subprocess.run(["git", "-C", repo, "add", "secret.pem"], check=True)
        leaked = _run(["--env", missing, "--root", repo])
        self.assertEqual(leaked.returncode, 1, leaked.stdout + leaked.stderr)
        self.assertIn("secret.pem", leaked.stdout)
        self.assertIn("private key", leaked.stdout)

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

    def test_this_checkout_without_env_reports_a_pattern_scan(self):
        missing = os.path.join(self.tmp, "does-not-exist.env")
        r = _run(["--env", missing, "--root", ROOT])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("tracked", r.stdout)
        self.assertIn("No .env", r.stdout)
        self.assertIn("[PASS] No secret pattern", r.stdout)
        self.assertIn("not a credential-value verification", r.stdout)

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
