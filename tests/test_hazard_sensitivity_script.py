"""Lock the hazard-transfer sensitivity measurement.

The claim this guards: the eBay fit's own statistical noise costs
per-millionths of objective value, while transfer-scale misfit costs
measurably more — so the transfer is a bounded, one-sided, quantified
limitation (LIMITATIONS section 13.8), not an unmeasured one.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sensitivity_table_separates_noise_from_transfer(tmp_path):
    out = tmp_path / "hazard_sensitivity.json"
    proc = subprocess.run(
        [sys.executable, "scripts/hazard_sensitivity.py", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = json.loads(out.read_text(encoding="utf-8"))
    rows = blob["rows"]
    families = {row["family"] for row in rows}
    assert families == {"baseline", "statistical", "transfer"}
    stat_max = max(r["loss_vs_planned"] for r in rows if r["family"] == "statistical")
    transfer_max = max(r["loss_vs_planned"] for r in rows if r["family"] == "transfer")
    assert stat_max < 1e-3, stat_max  # sampling noise is priced in
    assert transfer_max > stat_max * 10, (stat_max, transfer_max)  # transfer is the real exposure
