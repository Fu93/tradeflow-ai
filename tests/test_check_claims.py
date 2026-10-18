"""The documented numbers in README.md and SUBMISSION.md must match source files."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "theoretical roofline lower-bound estimate, not measured"


def test_check_claims_script_passes():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_claims.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_mi300x_roofline_matches_formula():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "regen_mi300x_roofline.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    bench = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    specs = bench["roofline_specs"]
    assert specs["label"] == LABEL
    assert "cpu_bandwidth_gb_s" not in specs
    assert "bandwidth_ratio_cpu_to_mi300x" not in specs
    for row in bench["single_supplier_grid"]:
        expected = round((row["working_set_mb"] * 1024 * 1024) / (5.3e12), 9)
        assert row["mi300x_transfer_lower_bound_seconds"] == expected
        assert row["mi300x_transfer_lower_bound_label"] == LABEL
