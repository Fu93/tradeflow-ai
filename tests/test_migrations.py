"""Schema migrations: version tracking, idempotency, and rollback refusal.

The pre-migration mechanism ran ``CREATE TABLE IF NOT EXISTS`` plus guarded
``ALTER TABLE`` on every process start and recorded nothing. These tests pin the
three properties that made that insufficient: a version is written, re-opening is
a no-op, and a database written by a newer build is refused rather than
half-understood.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.desk import migrations
from src.desk.migrations import CURRENT_SCHEMA_VERSION, SchemaTooNew, apply_all, user_version
from src.desk.store import DeskStore


def test_a_fresh_database_is_stamped_with_the_current_version(tmp_path):
    store = DeskStore(str(tmp_path / "desk.db"))
    try:
        assert user_version(store.conn) == CURRENT_SCHEMA_VERSION
        assert store.schema["applied"] == ["baseline", "desk_users"]
        assert store.schema["from_version"] == 0
    finally:
        store.close()


def test_reopening_is_a_no_op(tmp_path):
    path = str(tmp_path / "desk.db")
    DeskStore(path).close()
    again = DeskStore(path)
    try:
        assert again.schema["applied"] == []
        assert again.schema["from_version"] == CURRENT_SCHEMA_VERSION
        assert user_version(again.conn) == CURRENT_SCHEMA_VERSION
    finally:
        again.close()


def test_a_database_from_before_versioning_is_adopted_not_rebuilt(tmp_path):
    """An existing database has user_version 0 but a complete schema.

    Migration 1 is idempotent by construction, so the upgrade is a stamp rather
    than a rebuild. This is the case that matters for every deployment that
    already exists.
    """
    path = str(tmp_path / "desk.db")
    DeskStore(path).close()
    # Simulate the pre-versioning state: full schema, version 0.
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 0")
    raw.execute(
        "INSERT INTO deals (id, product, quantity, target_price, ceiling_price, risk_tolerance, seed, created_at, updated_at)"
        " VALUES ('legacy-deal', 'Widget', 10, 1.0, 2.0, 0.15, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    store = DeskStore(path)
    try:
        assert store.schema["applied"] == ["baseline", "desk_users"]
        assert user_version(store.conn) == CURRENT_SCHEMA_VERSION
        # The row survived the adoption.
        assert store.get_deal("legacy-deal") is not None
    finally:
        store.close()


def test_a_newer_schema_is_refused(tmp_path):
    """A rollback must not write into a schema it cannot read."""
    path = str(tmp_path / "desk.db")
    DeskStore(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaTooNew, match="newer than this build"):
        DeskStore(path)


def test_apply_all_reports_the_transition(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "raw.db"))
    try:
        record = apply_all(conn)
        assert record["to_version"] == CURRENT_SCHEMA_VERSION
        assert record["current_version"] == CURRENT_SCHEMA_VERSION
        assert record["applied"] == ["baseline", "desk_users"]
        # Second call is a no-op.
        assert apply_all(conn)["applied"] == []
    finally:
        conn.close()


def test_every_migration_has_a_unique_ascending_version():
    versions = [item.version for item in migrations.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions[-1] == CURRENT_SCHEMA_VERSION


def test_the_identifier_guard_rejects_an_unknown_table():
    """ALTER TABLE cannot be parameterised, so the names are checked instead."""
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="unknown table"):
            migrations._ensure_column(conn, "sqlite_master", "x", "TEXT")
        with pytest.raises(ValueError, match="non-identifier"):
            migrations._ensure_column(conn, "deals", "x; DROP TABLE deals", "TEXT")
    finally:
        conn.close()


def test_the_expected_indexes_exist(tmp_path):
    """The indexes added for the read paths must survive a schema rebuild."""
    store = DeskStore(str(tmp_path / "desk.db"))
    try:
        names = {row[0] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for expected in (
            "quote_events_deal_received",
            "drafts_deal_status",
            "decisions_deal",
            "hazard_outcomes_deal",
            "desk_chain_deal_seq",
        ):
            assert expected in names, expected
    finally:
        store.close()
