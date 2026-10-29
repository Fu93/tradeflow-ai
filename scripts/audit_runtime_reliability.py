"""Disposable crash, transport and restore drills. Never touches the live desk.

Run with: python scripts/audit_runtime_reliability.py --output /tmp/runtime.json
Uses real SQLite files and subprocess termination at write boundaries. The mail
transport is a local counter that raises; no message or paid request is sent.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKER = """
import os, sys
from src.desk import service
from src.desk.store import DeskStore
store = DeskStore(sys.argv[1])
if sys.argv[5] == 'before_audit':
    original = store.append_chain
    def interrupted(*args, **kwargs):
        if args[1] == 'review':
            os._exit(77)
        return original(*args, **kwargs)
    store.append_chain = interrupted
else:
    service.publish = lambda *args, **kwargs: os._exit(78)
service.accept_review(store, sys.argv[2], sys.argv[3], sys.argv[4], chosen_token=sys.argv[6])
"""


def audit() -> dict:
    for key in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "VLLM_API_KEY", "TAVILY_API_KEY",
                "DESK_SMTP_SEND", "IMAP_HOST", "GMAIL_CLIENT_ID", "TRADEFLOW_APPROVAL_PUBKEY"):
        os.environ.pop(key, None)
    os.environ.update(TRADEFLOW_SKIP_DOTENV="1", TRADEFLOW_ENV="sandbox", TRADEFLOW_CHAIN_KEY="local-fault-drill-chain",
                      TRADEFLOW_REQUIRE_SIGNED_APPROVAL="0", TRADEFLOW_CHAIN_ANCHOR="", TRADEFLOW_LLM_DAILY_CALLS="0",
                      TRADEFLOW_WEB_EVIDENCE_OFFLINE="1", TRADEFLOW_MARKET_OFFLINE="1")
    from src.desk import service
    from src.desk.store import DeskStore
    from src.desk.verify import draft_digest
    from scripts.backup_desk_db import backup, restore

    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "offline": True, "checks": {}}
    checks = report["checks"]
    with tempfile.TemporaryDirectory(prefix="tradeflow-fault-drill-") as folder:
        db = Path(folder) / "desk.db"
        store = DeskStore(str(db))
        snapshot = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
            risk_tolerance=.5, seed=11,
            suppliers=[{"name": n, "address": n.lower() + "@example.test"} for n in "ABCD"],
        )
        deal = snapshot["deal"]["id"]
        supplier = snapshot["suppliers"][0]["id"]

        def quote(price):
            return service.ingest_message(store, deal, source="paste", raw_text=f"单价 {price} 元。",
                                          supplier_id=supplier)["event"]["id"]

        event = quote("28.50")
        store.close()
        result = subprocess.run([sys.executable, "-c", WORKER, str(db), deal, event, supplier,
                                 "before_audit", "28.50"], cwd=ROOT, check=False)
        store = DeskStore(str(db))
        rows = store.verify_chain(deal)["entries"]
        checks["crash_before_audit_rolls_back"] = (
            result.returncode == 77 and store.event(deal, event)["status"] == "review"
            and not any(row["type"] == "review" for row in rows)
        )
        resumed = service.accept_review(store, deal, event, supplier, chosen_token="28.50")
        card = next(row for row in resumed["suppliers"] if row["id"] == supplier)
        checks["retry_after_restart_confirms_once"] = card["latest_quote"] == 28.5 and sum(
            row["type"] == "review" for row in store.verify_chain(deal)["entries"]
        ) == 1

        event = quote("28.00")
        store.close()
        result = subprocess.run([sys.executable, "-c", WORKER, str(db), deal, event, supplier,
                                 "before_publish", "28.00"], cwd=ROOT, check=False)
        store = DeskStore(str(db))
        missing_cache = store.get_snapshot(deal) is None
        recovered = service.publish(store, deal)
        card = next(row for row in recovered["suppliers"] if row["id"] == supplier)
        checks["crash_after_commit_rebuilds_snapshot"] = (
            result.returncode == 78 and missing_cache and card["latest_quote"] == 28.0
            and store.event(deal, event)["status"] == "accepted" and store.verify_chain(deal)["ok"]
        )

        draft = next(row for row in store.drafts(deal) if row["status"] == "draft")
        calls = []

        async def transport(*args, **kwargs):
            calls.append(1)
            raise TypeError("Simulated transport failure after a possible handoff")

        original_smtp = service.smtp_enabled
        service.smtp_enabled = lambda: True
        try:
            try:
                asyncio.run(service.send_draft(store, draft["id"], human_approved=True,
                                              body_sha256=draft_digest(draft["body"]), sender=transport))
            except TypeError:
                pass
            checks["transport_failure_never_retries_implicitly"] = len(calls) == 1
            refused = False
            try:
                asyncio.run(service.send_draft(store, draft["id"], human_approved=True,
                                              body_sha256=draft_digest(draft["body"]), sender=transport))
            except service.SendRefused:
                refused = True
            checks["uncertain_delivery_blocks_repeat_approval"] = refused and len(calls) == 1
        finally:
            service.smtp_enabled = original_smtp
        checks["sqlite_integrity"] = store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        expected_events = len(store.events(deal))
        expected_links = len(store.verify_chain(deal)["entries"])
        store.close()
        original_hash = hashlib.sha256(db.read_bytes()).hexdigest()
        invalid = Path(folder) / "invalid.db"
        with sqlite3.connect(invalid) as conn:
            conn.execute("CREATE TABLE unrelated(value)")
        rejected = restore(invalid, db, True)
        checks["invalid_restore_preserves_original_bytes"] = (
            rejected != 0 and hashlib.sha256(db.read_bytes()).hexdigest() == original_hash
        )
        copy = Path(folder) / "backup.db"
        restored = Path(folder) / "restored.db"
        with redirect_stdout(io.StringIO()):
            backup(db, copy)
            status = restore(copy, restored, True)
        restored_store = DeskStore(str(restored))
        checks["backup_restore_preserves_events_and_chain"] = (
            status == 0 and len(restored_store.events(deal)) == expected_events
            and len(restored_store.verify_chain(deal)["entries"]) == expected_links
            and restored_store.verify_chain(deal)["ok"]
        )
        restored_store.close()
    report["ok"] = all(checks.values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
