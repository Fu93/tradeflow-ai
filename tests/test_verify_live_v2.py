"""Lock the v2 product surface: verify_live.py must pass on a clean env.

The 2026-10-20 v2 phase-1 commit killed the v1 session flow by design
(a posted price is not a confirmation) but left verify_live.py walking
the dead surface, so the README-taught verification script failed at
step 3 with a 404 while 776 tests stayed green. Nothing in the suite
ran the script. This test runs it in a subprocess with a clean
environment and fails CI if the product walkthrough breaks again.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_live_walks_the_v2_desk_surface():
    proc = subprocess.run(
        [sys.executable, "verify_live.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, f"verify_live.py failed:\n{proc.stdout}\n{proc.stderr}"
    assert "V2 DESK SURFACE VERIFIED END TO END" in proc.stdout
    assert "[PASS] 9. Ed25519 approval recorded in the chain" in proc.stdout
