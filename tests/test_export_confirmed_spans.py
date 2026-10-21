"""Lock the data flywheel export: accepted spans -> real training rows.

Also guards the privacy property: the default output filename must be
git-ignored, so real supplier correspondence cannot be committed by
accident.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _store_with_confirmation(tmp_path):
    # No os.environ writes: conftest already provides TRADEFLOW_CHAIN_KEY,
    # and service-level calls need no API key (that is HTTP auth). A test
    # that writes os.environ directly poisons every later test in the same
    # process (this file's first version did exactly that and broke 23
    # desk-dependent tests in the full suite while passing standalone).
    db = str(tmp_path / "desk.db")
    from src.desk.store import DeskStore
    from src.desk.service import accept_review, create_deal, ingest_message

    store = DeskStore(db)
    opened = create_deal(
        store,
        product="Widget",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        currency="CNY",
        suppliers=[
            {"name": f"S{i}", "address": f"s{i}@example.test", "channel": "email"}
            for i in range(4)
        ],
    )
    deal_id, supplier = opened["deal"]["id"], opened["suppliers"][0]["id"]
    ingested = ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    store.close()
    return db


def test_export_rows_and_gitignore(tmp_path):
    db = _store_with_confirmation(tmp_path)
    out = tmp_path / "spans.jsonl"
    proc = subprocess.run(
        [sys.executable, "scripts/export_confirmed_spans.py", "--db", db, "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["raw_text"] == "单价 14.80 元"
    assert row["chosen_token"] == "14.80"
    assert row["confirmed_unit_price"] == 14.8
    assert row["source"] == "paste"
    # Privacy: the default export name is git-ignored on purpose.
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "confirmed_spans.jsonl" in ignore
