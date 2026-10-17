"""The documented numbers in README.md and SUBMISSION.md must match source files."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_check_claims_script_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_claims.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
