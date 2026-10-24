"""Numbered SQLite schema migrations, tracked with ``PRAGMA user_version``.

Why this module exists
----------------------
The desk schema used to be created by a single ``_create()`` method that ran
``CREATE TABLE IF NOT EXISTS`` and then a list of ``ALTER TABLE ... ADD COLUMN``
calls guarded by a ``PRAGMA table_info`` check. That mechanism is additive only
and it records nothing:

* A rename, a type change, a backfill, or an index drop cannot be expressed.
* ``PRAGMA user_version`` was never read or written, so a database carried no
  record of which shape it was in. A deploy that rolled back left a schema the
  previous code only half-understood, and nothing could detect it.
* The column check re-ran on every process start, so "did the schema change"
  was unanswerable without diffing ``sqlite_master`` by hand.

This module gives the schema a version number and a place to put the next
change. It does not rewrite history: migration 1 is exactly the schema the old
``_create()`` produced, and every statement in it is idempotent, so opening an
existing database is a no-op that simply stamps the version it already had.

Atomicity, stated honestly: ``sqlite3.Connection.executescript`` issues an
implicit COMMIT before it runs, so a migration is not wrapped in one
transaction. The guarantee this module actually makes is weaker and more useful
than "atomic": ``user_version`` is only advanced after a migration function
returns without raising, and every migration is written to be safe to re-run.
A process killed mid-migration therefore retries it rather than skipping it.

Adding a migration
------------------
1. Append a ``Migration`` with the next version number.
2. Bump ``CURRENT_SCHEMA_VERSION``.
3. Make the body idempotent where it reasonably can be (``IF NOT EXISTS``,
   guard on ``table_info``), because it will be re-run if the process dies.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable

from src.config.heuristics import DEAL_CURRENCY
from src.desk import audit_chain

logger = logging.getLogger("tradeflow.desk.migrations")

# The version this code expects. A database at a *higher* version was written by
# a newer build; opening it read-write risks corrupting rows this code cannot
# interpret, so that case is refused rather than silently tolerated.
CURRENT_SCHEMA_VERSION = 2

# Identifiers that may appear in the DDL this module builds by string
# formatting. ``ALTER TABLE`` cannot be parameterised, so the names are checked
# against a closed set instead. Nothing here is caller-controlled, but a guard
# costs nothing and removes the question.
_ALLOWED_TABLES = frozenset(
    {
        "deals",
        "suppliers",
        "quote_events",
        "drafts",
        "snapshots",
        "mailbox_seen",
        "decisions",
        "hazard_outcomes",
        "desk_chain",
        "desk_chain_legacy",
        "desk_users",
    }
)


def user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA does not accept bound parameters; the value is an int we produced.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column when it is missing. ``table`` must be in the allowlist."""
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"refusing to alter an unknown table: {table!r}")
    if not column.replace("_", "").isalnum():
        raise ValueError(f"refusing a non-identifier column name: {column!r}")
    found = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in found:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _indexes(conn: sqlite3.Connection) -> None:
    """Indexes for the columns the read paths actually filter on.

    ``EXPLAIN QUERY PLAN`` showed four full table scans on the hot paths: the
    event list ordered by ``received_at``, the draft lookups, the hazard outcome
    lookups, and the decision lookups. All four were served by a ``SCAN`` plus a
    temporary B-tree. The ``quote_external`` index is *partial*
    (``WHERE external_id IS NOT NULL``), so it cannot answer a plain ``deal_id``
    filter — a common mistake worth stating explicitly.
    """
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS quote_events_deal_received
            ON quote_events(deal_id, received_at);
        CREATE INDEX IF NOT EXISTS quote_events_deal_supplier
            ON quote_events(deal_id, supplier_id);
        CREATE INDEX IF NOT EXISTS drafts_deal_supplier
            ON drafts(deal_id, supplier_id);
        CREATE INDEX IF NOT EXISTS drafts_deal_status
            ON drafts(deal_id, status);
        CREATE INDEX IF NOT EXISTS suppliers_deal
            ON suppliers(deal_id);
        CREATE INDEX IF NOT EXISTS hazard_outcomes_deal
            ON hazard_outcomes(deal_id);
        CREATE INDEX IF NOT EXISTS decisions_deal
            ON decisions(deal_id);
        CREATE INDEX IF NOT EXISTS desk_chain_deal_seq
            ON desk_chain(deal_id, seq);
        """
    )


def _baseline(conn: sqlite3.Connection) -> None:
    """Version 1: the schema the pre-migration ``_create()`` produced.

    Byte-for-byte the same DDL, in the same order, because an existing database
    must reach this version by doing nothing. The only addition is the
    identifier guard on ``_ensure_column``.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            target_price REAL NOT NULL,
            ceiling_price REAL NOT NULL,
            risk_tolerance REAL NOT NULL,
            seed INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS suppliers (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            channel TEXT NOT NULL,
            belief_mean REAL,
            belief_std REAL,
            hazard_events INTEGER NOT NULL DEFAULT 0,
            hazard_walks INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (deal_id) REFERENCES deals(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS suppliers_deal_address
            ON suppliers(deal_id, address COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS quote_events (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            supplier_id TEXT,
            source TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            parsed_json TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            external_id TEXT,
            received_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS quote_external
            ON quote_events(deal_id, external_id)
            WHERE external_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            supplier_id TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            human_approved INTEGER NOT NULL DEFAULT 0,
            transmitted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            gmail_draft_id TEXT,
            gmail_error TEXT
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            deal_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mailbox_seen (
            mailbox_key TEXT NOT NULL,
            external_id TEXT NOT NULL,
            PRIMARY KEY (mailbox_key, external_id)
        );
        """
    )
    for column, ddl in (
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("body_sha256", "TEXT"),
        ("send_error", "TEXT"),
        ("reply_to", "TEXT"),
        ("references_header", "TEXT"),
        ("message_id", "TEXT"),
        ("seq", "INTEGER"),
    ):
        _ensure_column(conn, "drafts", column, ddl)
    conn.execute("UPDATE drafts SET seq = rowid WHERE seq IS NULL")
    for column, ddl in (
        ("hazard_intercept", "REAL"),
        ("hazard_tau", "REAL"),
        ("hazard_quote_gap", "REAL"),
        ("reserve_prior_mean", "REAL"),
        ("reserve_prior_std", "REAL"),
        ("expected_markup", "REAL"),
        ("search_cost", "REAL"),
        ("include_unverified", "INTEGER NOT NULL DEFAULT 0"),
        ("hazard_reserve_gap", "REAL"),
        ("bayes_obs_noise", "REAL"),
        ("unit_sanity_multiple", "REAL"),
        ("currency", "TEXT"),
    ):
        _ensure_column(conn, "deals", column, ddl)
    conn.execute(
        "UPDATE deals SET currency = ? WHERE currency IS NULL OR currency = ''",
        (DEAL_CURRENCY,),
    )
    _ensure_column(conn, "quote_events", "raw_sha256", "TEXT")
    _ensure_column(conn, "drafts", "decision_id", "TEXT")
    _ensure_column(conn, "snapshots", "decision_id", "TEXT")
    conn.executescript(audit_chain.SCHEMA)
    _ensure_column(conn, "desk_chain", "key_id", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            supplier_id TEXT NOT NULL,
            record_json TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
        BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
        BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS hazard_outcomes (
            id TEXT PRIMARY KEY,
            deal_id TEXT NOT NULL,
            supplier_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            quote REAL,
            offer REAL,
            agreed_price REAL,
            created_at TEXT NOT NULL
        );
        """
    )
    _indexes(conn)


def _desk_users(conn: sqlite3.Connection) -> None:
    """Named operator keys, added 2026-10-24 (Phase 2 of the upgrade plan).

    The desk had exactly one credential — ``TRADEFLOW_API_KEY``, shared by
    every buyer in the organisation — so the audit chain could not say who
    acted and revoking one person meant rotating the key for everyone. This
    table stores named keys as SHA-256 hashes only; the raw secret is shown
    once at issue time and never persisted.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS desk_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );
        """
    )


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


# Ordered. Each entry runs once, in order, and only when the database is below
# its version.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="baseline", apply=_baseline),
    Migration(version=2, name="desk_users", apply=_desk_users),
)


class SchemaTooNew(RuntimeError):
    """The database was written by a build newer than this one."""


def apply_all(conn: sqlite3.Connection) -> dict[str, object]:
    """Bring the database up to ``CURRENT_SCHEMA_VERSION``.

    Returns a small record of what happened so a caller (and the readiness probe)
    can report it without re-querying. Refuses to touch a database whose version
    is higher than this build understands: that is a rollback, and the previous
    binary must not write into a schema it cannot read.
    """
    start = user_version(conn)
    if start > CURRENT_SCHEMA_VERSION:
        raise SchemaTooNew(
            f"database schema version {start} is newer than this build "
            f"({CURRENT_SCHEMA_VERSION}); refusing to write. Deploy the newer build."
        )
    applied: list[str] = []
    for migration in MIGRATIONS:
        if migration.version <= start:
            continue
        logger.info("applying schema migration %s (%s)", migration.version, migration.name)
        migration.apply(conn)
        # Advanced only after the body returns without raising, so a crash
        # mid-migration retries rather than skipping.
        _set_user_version(conn, migration.version)
        conn.commit()
        applied.append(migration.name)
    end = user_version(conn)
    if applied:
        logger.info("schema migrated %s -> %s (%s)", start, end, ", ".join(applied))
    return {
        "from_version": start,
        "to_version": end,
        "current_version": CURRENT_SCHEMA_VERSION,
        "applied": applied,
    }
