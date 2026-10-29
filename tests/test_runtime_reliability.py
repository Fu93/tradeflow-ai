"""Destructive drills run only against disposable databases and fake transports."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from scripts.backup_desk_db import backup, restore
from src.desk import service
from src.desk.store import DeskStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "local-runtime-test-chain")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", "")
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "0")
    with closing(DeskStore(str(tmp_path / "desk.db"))) as store:
        snap = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
            risk_tolerance=.5, seed=11,
            suppliers=[{"name": n, "address": n.lower() + "@example.test"} for n in "ABCD"],
        )
        yield store, snap["deal"]["id"], snap["suppliers"][0]["id"]


def test_real_process_crash_transport_and_restore_drill(tmp_path):
    result = tmp_path / "faults.json"
    run = subprocess.run([sys.executable, str(ROOT / "scripts/audit_runtime_reliability.py"),
                          "--output", str(result)], cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(result.read_text())
    assert report["ok"] and all(report["checks"].values())


def test_failed_outcome_rolls_back_and_keeps_previous_snapshot(desk, monkeypatch):
    store, deal_id, supplier = desk
    event = service.ingest_message(store, deal_id, source="paste", raw_text="单价 28.00 元。",
                                   supplier_id=supplier)["event"]
    service.accept_review(store, deal_id, event["id"], supplier, chosen_token="28.00")
    before = store.get_snapshot(deal_id)
    original = store.append_chain

    def fail(*args, **kwargs):
        if args[1] == "outcome":
            raise sqlite3.OperationalError("injected disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "append_chain", fail)
    with pytest.raises(sqlite3.OperationalError):
        service.record_outcome(store, deal_id, supplier, outcome="walk_away")
    assert store.outcomes(deal_id) == []
    assert store.get_snapshot(deal_id) == before
    assert not store.conn.in_transaction


def test_other_thread_cannot_commit_a_failed_atomic_unit(desk):
    store, deal_id, _ = desk
    entered, other_started = threading.Event(), threading.Event()
    original_product = store.get_deal(deal_id)["product"]

    def failing():
        with pytest.raises(RuntimeError, match="injected"):
            with store.atomic():
                store.conn.execute("UPDATE deals SET product='uncommitted' WHERE id=?", (deal_id,))
                entered.set()
                assert other_started.wait(3)
                raise RuntimeError("injected failure")

    def independent():
        assert entered.wait(3)
        other_started.set()
        store.touch(deal_id)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(failing), workers.submit(independent)]
        for future in futures:
            future.result(timeout=5)
    with sqlite3.connect(store.path) as independent_reader:
        persisted = independent_reader.execute("SELECT product FROM deals WHERE id=?", (deal_id,)).fetchone()[0]
        assert persisted == original_product
    assert not store.conn.in_transaction


def test_nested_failure_uses_savepoint_and_outer_work_can_commit(desk):
    store, deal_id, _ = desk
    with store.atomic():
        store.conn.execute("UPDATE deals SET product='outer' WHERE id=?", (deal_id,))
        with pytest.raises(RuntimeError):
            with store.atomic():
                store.conn.execute("UPDATE deals SET product='inner' WHERE id=?", (deal_id,))
                raise RuntimeError("inner failed")
    assert store.get_deal(deal_id)["product"] == "outer"


def test_failed_commit_releases_transaction_and_next_change_succeeds(desk):
    store, deal_id, _ = desk
    store.conn.execute("CREATE TABLE deferred_parent (id INTEGER PRIMARY KEY)")
    store.conn.execute("CREATE TABLE deferred_child (id INTEGER REFERENCES deferred_parent(id) "
                       "DEFERRABLE INITIALLY DEFERRED)")
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        with store.atomic():
            store.conn.execute("INSERT INTO deferred_child VALUES (999)")
    assert not store.conn.in_transaction
    store.touch(deal_id)
    assert store.probe_write()["ok"]


def test_restore_refuses_journals_and_newer_schema_without_touching_target(desk, tmp_path):
    store, _, _ = desk
    copy = tmp_path / "backup.db"
    backup(Path(store.path), copy)
    # A live SQLite connection has journal sidecars. It must remain untouched.
    original = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    assert restore(copy, Path(store.path), True) == 1
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest() == original
    newer = tmp_path / "newer.db"
    with sqlite3.connect(copy) as source, sqlite3.connect(newer) as dest:
        source.backup(dest)
        dest.execute("PRAGMA user_version=999")
    absent = tmp_path / "absent.db"
    assert restore(newer, absent, True) == 1
    assert not absent.exists()


def test_live_backup_validates_the_snapshot_not_later_source_state(desk, tmp_path, monkeypatch):
    from scripts import backup_desk_db

    store, deal_id, _ = desk
    original = backup_desk_db._verify
    inserted = False

    def verify(conn):
        nonlocal inserted
        # Add a new deal AFTER SQLite's consistent copy but BEFORE verification.
        if not inserted:
            inserted = True
            service.create_deal(
                store, product="later product", quantity=500, target_price=20, ceiling_price=30,
                risk_tolerance=.5, seed=12,
                suppliers=[{"name": n, "address": n.lower() + "@example.test"} for n in "EFGH"],
            )
        return original(conn)

    monkeypatch.setattr(backup_desk_db, "_verify", verify)
    output = tmp_path / "online.db"
    result = backup(Path(store.path), output)
    assert result["deals"] == 1
    with sqlite3.connect(output) as conn:
        assert conn.execute("SELECT id FROM deals").fetchall() == [(deal_id,)]
    assert len(store.list_deals()) == 2
