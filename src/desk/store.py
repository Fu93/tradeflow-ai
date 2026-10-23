"""SQLite desk. One file, reopened after a restart."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config.heuristics import DEAL_CURRENCY
from src.desk import audit_chain


class DeskRejected(ValueError):
    """The caller asked for a deal the desk will not store."""


class DeskUnavailable(DeskRejected):
    """The desk cannot open a deal because the chain key is missing."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def db_path() -> str:
    explicit = os.environ.get("TRADEFLOW_DESK_DB", "").strip()
    if explicit:
        return explicit
    store = os.environ.get("TRADEFLOW_STORE_DIR", "").strip()
    if store:
        return os.path.join(store, "desk.db")
    return os.path.join("data", "desk.db")


class DeskStore:
    def __init__(self, path: str) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def probe_write(self) -> dict[str, Any]:
        """Readiness probe: prove the file is writable, not merely openable.

        A store whose directory went read-only, whose disk is full, or whose
        WAL cannot checkpoint still opens and serves reads. `/health` reported
        `ok` in every one of those cases. This performs one real write in a
        dedicated single-row table, so a read-only mount or a full disk surfaces
        as `ok: False` instead of a 500 on the first quote.

        It does not touch the audit chain and does not append business data.
        """
        with self._lock:
            try:
                self.conn.execute(
                    "CREATE TABLE IF NOT EXISTS _readiness_probe (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT)"
                )
                self.conn.execute(
                    "INSERT INTO _readiness_probe (id, at) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET at = excluded.at",
                    (_now(),),
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            return {"ok": True, "detail": "writable"}

    def _create_indexes(self) -> None:
        """Indexes for the columns the read paths actually filter on.

        ``EXPLAIN QUERY PLAN`` showed four full table scans on the hot paths:
        the event list ordered by ``received_at``, the draft lookups, the hazard
        outcome lookups, and the decision lookups. All four were served by a
        ``SCAN`` plus a temporary B-tree. The existing ``quote_external`` index
        is *partial* (``WHERE external_id IS NOT NULL``), so it cannot answer a
        plain ``deal_id`` filter — a common mistake worth stating explicitly.

        Created separately from the table DDL so a pre-existing database picks
        them up on the next open without a migration.
        """
        self.conn.executescript(
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
            """
        )
        # The remaining tables are created by later migration steps, so their
        # indexes are added where the table is guaranteed to exist.
        for statement in (
            "CREATE INDEX IF NOT EXISTS hazard_outcomes_deal ON hazard_outcomes(deal_id)",
            "CREATE INDEX IF NOT EXISTS decisions_deal ON decisions(deal_id)",
            "CREATE INDEX IF NOT EXISTS desk_chain_deal_seq ON desk_chain(deal_id, seq)",
        ):
            try:
                self.conn.execute(statement)
            except sqlite3.Error:
                # A database that has never run the step that creates this table
                # is not an error; the index is created on the next open.
                continue

    def _create(self) -> None:
        self.conn.executescript(
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
        self._ensure_column("drafts", "version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("drafts", "body_sha256", "TEXT")
        self._ensure_column("drafts", "send_error", "TEXT")
        self._ensure_column("drafts", "reply_to", "TEXT")
        self._ensure_column("drafts", "references_header", "TEXT")
        self._ensure_column("drafts", "message_id", "TEXT")
        self._ensure_column("drafts", "seq", "INTEGER")
        self.conn.execute("UPDATE drafts SET seq = rowid WHERE seq IS NULL")
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
        ):
            self._ensure_column("deals", column, ddl)
        self._ensure_column("deals", "currency", "TEXT")
        self.conn.execute(
            "UPDATE deals SET currency = ? WHERE currency IS NULL OR currency = ''",
            (DEAL_CURRENCY,),
        )
        self._ensure_column("quote_events", "raw_sha256", "TEXT")
        self._ensure_column("drafts", "decision_id", "TEXT")
        self._ensure_column("snapshots", "decision_id", "TEXT")
        self.conn.executescript(audit_chain.SCHEMA)
        self._ensure_column("desk_chain", "key_id", "TEXT")
        self.conn.executescript(
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
        self.conn.executescript(
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
        self._create_indexes()
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        found = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in found:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _supplier_snapshot(self, deal_id: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT id, name, address FROM suppliers WHERE deal_id = ? ORDER BY name, id",
            (deal_id,),
        ).fetchall()
        return [
            {"id": str(row["id"]), "name": str(row["name"]), "address": str(row["address"])}
            for row in rows
        ]

    def _opened_payload(self, deal: Any, *, legacy_head: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "currency": str(deal["currency"] or DEAL_CURRENCY),
            "ceiling": float(deal["ceiling_price"]),
            "target_price": float(deal["target_price"]),
            "quantity": int(deal["quantity"]),
            "product": str(deal["product"]),
            "risk_tolerance": float(deal["risk_tolerance"]),
            "migrated": True,
            "note": "migrated, pre-chain inputs unverified",
            "suppliers": self._supplier_snapshot(str(deal["id"])),
        }
        if legacy_head:
            payload["legacy_head"] = legacy_head
            payload["legacy"] = audit_chain.LEGACY_NOTE
        return payload

    def _archive_unkeyed_chain(self, deal_id: str, rows: list[Any]) -> str:
        """Copy a pre-key chain aside and remove it so a keyed chain can start at genesis."""
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO desk_chain_legacy (
                    deal_id, seq, type, ts, payload_json, prev_hash, entry_hash, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["deal_id"],
                    int(row["seq"]),
                    row["type"],
                    row["ts"],
                    row["payload_json"],
                    row["prev_hash"],
                    row["entry_hash"],
                    audit_chain.LEGACY_NOTE,
                ),
            )
        self.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
        self.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
        self.conn.execute("DELETE FROM desk_chain WHERE deal_id = ?", (deal_id,))
        self.conn.executescript(audit_chain.SCHEMA)
        return str(rows[-1]["entry_hash"])

    def migrate_deal(self, deal_id: str) -> None:
        """Operator action. Opening the database does not migrate.

        A keyed chain, including a round-5 HMAC chain, is left untouched.
        An existing anchor head is left untouched. An intact pre-key SHA-256
        chain is archived and a new keyed chain starts at genesis. A deal
        with no chain rows gets one migrated head. Accepted quotes return
        to review either way.
        """
        with self._lock:
            if not audit_chain.chain_key_configured():
                raise DeskRejected("Desk migration requires TRADEFLOW_CHAIN_KEY.")
            deal = self._deal(deal_id)
            if deal is None:
                raise KeyError(deal_id)
            if audit_chain.anchor_head_present(deal_id):
                raise DeskRejected("This deal already has an anchor head. Migration will not overwrite it.")
            self._migrate_legacy_chains(deal)

    def _drop_snapshot(self, deal_id: str) -> None:
        self.conn.execute("DELETE FROM snapshots WHERE deal_id = ?", (deal_id,))
        self.conn.commit()

    def _return_quotes_to_review(self, deal_id: str) -> None:
        self.conn.execute(
            "UPDATE quote_events SET status = 'review' WHERE deal_id = ? AND status = 'accepted'",
            (deal_id,),
        )

    def _migrate_legacy_chains(self, deal: Any) -> None:
        """Archive one intact pre-key chain, or open a head when the deal has none."""
        rows = self.conn.execute(
            "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
            (deal["id"],),
        ).fetchall()
        if audit_chain.legacy_unkeyed(list(rows)):
            ingested = []
            for row in rows:
                if row["type"] != "quote_ingested":
                    continue
                ingested.append(json.loads(row["payload_json"]))
            legacy_head = self._archive_unkeyed_chain(str(deal["id"]), list(rows))
            self._return_quotes_to_review(str(deal["id"]))
            self.conn.commit()
            self.append_chain(deal["id"], "deal_opened", self._opened_payload(deal, legacy_head=legacy_head))
            for payload in ingested:
                self.append_chain(deal["id"], "quote_ingested", payload)
            self._drop_snapshot(str(deal["id"]))
            return
        if rows:
            raise DeskRejected("This deal already has a keyed chain. Migration will not replace it.")
        opened = self.conn.execute(
            "SELECT seq FROM desk_chain WHERE deal_id = ? AND type = 'deal_opened' ORDER BY seq ASC LIMIT 1",
            (deal["id"],),
        ).fetchone()
        if opened is not None:
            raise DeskRejected("This deal already has a keyed chain. Migration will not replace it.")
        self._return_quotes_to_review(str(deal["id"]))
        self.conn.commit()
        self.append_chain(deal["id"], "deal_opened", self._opened_payload(deal, legacy_head=None))
        self._drop_snapshot(str(deal["id"]))

    def update_limits(
        self,
        deal_id: str,
        *,
        product: str,
        quantity: int,
        target_price: float,
        ceiling_price: float,
        risk_tolerance: float,
        currency: str,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE deals
                SET product = ?, quantity = ?, target_price = ?, ceiling_price = ?,
                    risk_tolerance = ?, currency = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    product,
                    int(quantity),
                    float(target_price),
                    float(ceiling_price),
                    float(risk_tolerance),
                    currency,
                    _now(),
                    deal_id,
                ),
            )
            self.conn.commit()

    def create_deal(
        self,
        *,
        product: str,
        quantity: int,
        target_price: float,
        ceiling_price: float,
        risk_tolerance: float,
        seed: int,
        suppliers: list[dict[str, str]],
        currency: str = DEAL_CURRENCY,
        commit: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            deal_id = uuid.uuid4().hex
            now = _now()
            self.conn.execute(
                """
                INSERT INTO deals (
                    id, product, quantity, target_price, ceiling_price,
                    risk_tolerance, seed, created_at, updated_at, currency
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal_id,
                    product,
                    int(quantity),
                    float(target_price),
                    float(ceiling_price),
                    float(risk_tolerance),
                    int(seed),
                    now,
                    now,
                    currency,
                ),
            )
            rows = []
            for item in suppliers:
                supplier_id = uuid.uuid4().hex
                self.conn.execute(
                    """
                    INSERT INTO suppliers (
                        id, deal_id, name, address, channel,
                        belief_mean, belief_std, hazard_events, hazard_walks
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, 0)
                    """,
                    (
                        supplier_id,
                        deal_id,
                        item["name"].strip(),
                        item["address"].strip(),
                        (item.get("channel") or "email").strip() or "email",
                    ),
                )
                rows.append(supplier_id)
            self._finish_create(commit)
            deal = self._deal(deal_id)
            if deal is None:
                raise KeyError(deal_id)
            deal["supplier_ids"] = rows
            return deal

    def _finish_create(self, commit: bool) -> None:
        if commit:
            self.conn.commit()  # create_deal

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self.conn.rollback()

    def update_supplier(self, supplier_id: str, *, name: str, address: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE suppliers SET name = ?, address = ? WHERE id = ?",
                (name, address, supplier_id),
            )
            self.conn.commit()

    def _deal(self, deal_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
        return dict(row) if row else None

    def get_deal(self, deal_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._deal(deal_id)

    def list_deals(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM deals ORDER BY updated_at DESC, id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def touch(self, deal_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE deals SET updated_at = ? WHERE id = ?",
                (_now(), deal_id),
            )
            self.conn.commit()

    def suppliers(self, deal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM suppliers WHERE deal_id = ? ORDER BY name, id",
                (deal_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def supplier(self, deal_id: str, supplier_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM suppliers WHERE deal_id = ? AND id = ?",
                (deal_id, supplier_id),
            ).fetchone()
            return dict(row) if row else None

    def set_belief(self, supplier_id: str, mean: float, std: float) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE suppliers SET belief_mean = ?, belief_std = ? WHERE id = ?",
                (float(mean), float(std), supplier_id),
            )
            self.conn.commit()

    def find_event(self, deal_id: str, external_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM quote_events WHERE deal_id = ? AND external_id = ?",
                (deal_id, external_id),
            ).fetchone()
            return self._event(row) if row else None

    def insert_event(
        self,
        *,
        deal_id: str,
        supplier_id: str | None,
        source: str,
        raw_text: str,
        parsed: dict[str, Any],
        confidence: str,
        status: str,
        provenance: dict[str, Any],
        external_id: str | None,
    ) -> dict[str, Any] | None:
        with self._lock:
            event_id = uuid.uuid4().hex
            try:
                self.conn.execute(
                    """
                    INSERT INTO quote_events (
                        id, deal_id, supplier_id, source, raw_text, parsed_json,
                        confidence, status, provenance_json, external_id, received_at,
                        raw_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        deal_id,
                        supplier_id,
                        source,
                        raw_text,
                        json.dumps(parsed, ensure_ascii=False),
                        confidence,
                        status,
                        json.dumps(provenance, ensure_ascii=False),
                        external_id,
                        _now(),
                        hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest(),
                    ),
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                self.conn.rollback()
                return None
            row = self.conn.execute(
                "SELECT * FROM quote_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            return self._event(row)

    def events(self, deal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM quote_events
                WHERE deal_id = ?
                ORDER BY received_at ASC, id ASC
                """,
                (deal_id,),
            ).fetchall()
            loaded = []
            for row in rows:
                event = self._event(row)
                if event is not None:
                    loaded.append(event)
            return loaded

    def event(self, deal_id: str, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM quote_events WHERE deal_id = ? AND id = ?",
                (deal_id, event_id),
            ).fetchone()
            return self._event(row) if row else None

    def set_event_status(
        self,
        event_id: str,
        *,
        status: str,
        supplier_id: str | None,
        parsed: dict[str, Any],
        confidence: str,
        provenance: dict[str, Any],
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE quote_events
                SET status = ?, supplier_id = ?, parsed_json = ?, confidence = ?,
                    provenance_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    supplier_id,
                    json.dumps(parsed, ensure_ascii=False),
                    confidence,
                    json.dumps(provenance, ensure_ascii=False),
                    event_id,
                ),
            )
            self.conn.commit()

    def save_snapshot(self, deal_id: str, payload: dict[str, Any], elapsed_ms: int) -> None:
        with self._lock:
            now = _now()
            encoded = json.dumps(payload, ensure_ascii=False)
            self.conn.execute(
                """
                INSERT INTO snapshots (deal_id, payload_json, computed_at, elapsed_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(deal_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    computed_at = excluded.computed_at,
                    elapsed_ms = excluded.elapsed_ms
                """,
                (deal_id, encoded, now, int(elapsed_ms)),
            )
            self.conn.commit()

    def get_snapshot(self, deal_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT payload_json FROM snapshots WHERE deal_id = ?",
                (deal_id,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])

    def replace_open_draft(
        self,
        deal_id: str,
        supplier_id: str,
        body: str,
        *,
        reply_to: str | None = None,
        references_header: str | None = None,
        decision_id: str | None = None,
        require_new: bool = False,
    ) -> dict[str, Any]:
        """Freeze the previous draft when the text changes.

        Approval is the hash of one version. A new quote inserts a new draft
        id instead of rewriting the text the person may already have read.
        """
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM drafts
                WHERE deal_id = ? AND supplier_id = ?
                ORDER BY seq DESC, created_at DESC, id DESC
                """,
                (deal_id, supplier_id),
            ).fetchall()
            for existing in rows:
                if require_new:
                    break
                if existing["body"] == body and existing["status"] in {
                    "draft",
                    "sent",
                    "approved",
                    "sending",
                    "failed",
                }:
                    if decision_id:
                        self.conn.execute(
                            "UPDATE drafts SET decision_id = ? WHERE id = ?",
                            (decision_id, existing["id"]),
                        )
                    self.conn.execute(
                        """
                        UPDATE drafts SET status = 'superseded'
                        WHERE deal_id = ? AND supplier_id = ? AND status = 'draft' AND id != ?
                        """,
                        (deal_id, supplier_id, existing["id"]),
                    )
                    self.conn.commit()
                    row = self.conn.execute(
                        "SELECT * FROM drafts WHERE id = ?",
                        (existing["id"],),
                    ).fetchone()
                    return dict(row)
            version = 1
            if rows:
                version = max(int(row["version"] or 1) for row in rows) + 1
            self.conn.execute(
                """
                UPDATE drafts SET status = 'superseded'
                WHERE deal_id = ? AND supplier_id = ? AND status = 'draft'
                """,
                (deal_id, supplier_id),
            )
            draft_id = uuid.uuid4().hex
            now = _now()
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            seq_row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM drafts").fetchone()
            seq = int(seq_row["seq"])
            self.conn.execute(
                """
                INSERT INTO drafts (
                    id, deal_id, supplier_id, body, status, human_approved,
                    transmitted, created_at, sent_at, gmail_draft_id, gmail_error,
                    version, body_sha256, reply_to, references_header, seq, decision_id
                ) VALUES (?, ?, ?, ?, 'draft', 0, 0, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    deal_id,
                    supplier_id,
                    body,
                    now,
                    version,
                    digest,
                    reply_to,
                    references_header,
                    seq,
                    decision_id,
                ),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            return dict(row)

    def supersede_open_drafts(self, deal_id: str, supplier_id: str) -> None:
        """A withheld or missing counter must not leave the old text sendable."""
        with self._lock:
            self.conn.execute(
                """
                UPDATE drafts SET status = 'superseded'
                WHERE deal_id = ? AND supplier_id = ? AND status = 'draft'
                """,
                (deal_id, supplier_id),
            )
            self.conn.commit()

    def claim_draft_send(self, draft_id: str, message_id: str) -> bool:
        """One in-flight send per draft. A second caller does not send again."""
        with self._lock:
            cursor = self.conn.execute(
                """
                UPDATE drafts
                SET status = 'sending', message_id = ?
                WHERE id = ? AND status IN ('draft', 'failed', 'approved')
                """,
                (message_id, draft_id),
            )
            self.conn.commit()
            return cursor.rowcount == 1

    def set_gmail_draft(self, draft_id: str, *, gmail_id: str | None, error: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE drafts SET gmail_draft_id = ?, gmail_error = ? WHERE id = ?",
                (gmail_id, error, draft_id),
            )
            self.conn.commit()

    def append_chain(
        self,
        deal_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            row = audit_chain.append(self.conn, deal_id, type_, _now(), payload)
            if commit:
                self.conn.commit()
            return row

    def verify_chain(self, deal_id: str) -> dict[str, Any]:
        """Verify the chain without holding the store lock for the O(n) pass.

        [PERF 2026-10-23] This used to run the whole MAC recomputation — and the
        anchor file/URL I/O — inside ``self._lock``, the same lock 40 other
        methods take. Measured effect: eight concurrent verifications of a
        5,000-entry chain took 666 ms of wall time, exactly eight times the
        single-threaded 84.5 ms, and a concurrent write waited 76.9 ms behind
        them. One connection with one lock and WAL mode gives no read parallelism
        at all. Fetching the rows and the pending mark under the lock and doing
        the CPU work after releasing it restores concurrency without a second
        connection and without changing a single verification result.
        """
        with self._lock:
            rows = audit_chain.chain_rows(self.conn, deal_id)
            pending = audit_chain._pending_anchor(self.conn, deal_id)
        return audit_chain.verify_with_anchor(audit_chain.verify_rows(rows), deal_id, pending)

    def verify_links(self, deal_id: str) -> dict[str, Any]:
        with self._lock:
            rows = audit_chain.chain_rows(self.conn, deal_id)
        return audit_chain.verify_rows(rows)

    def anchor_is_pending(self, deal_id: str) -> bool:
        with self._lock:
            return audit_chain._pending_anchor(self.conn, deal_id)

    def append_decision(self, record: dict[str, Any], record_sha256: str) -> str:
        with self._lock:
            decision_id = uuid.uuid4().hex
            self.conn.execute(
                """
                INSERT INTO decisions (id, deal_id, supplier_id, record_json, record_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    record["deal_id"],
                    record["supplier_id"],
                    json.dumps(record, sort_keys=True, ensure_ascii=False),
                    record_sha256,
                    _now(),
                ),
            )
            self.conn.commit()
            return decision_id

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["record"] = json.loads(item.pop("record_json"))
            return item

    def drafts(self, deal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM drafts
                WHERE deal_id = ?
                ORDER BY seq DESC, created_at DESC, id DESC
                """,
                (deal_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_draft(
        self,
        draft_id: str,
        *,
        status: str,
        transmitted: bool,
        error: str | None = None,
        message_id: str | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE drafts
                SET status = ?, human_approved = 1, transmitted = ?, sent_at = ?,
                    send_error = ?, message_id = COALESCE(?, message_id)
                WHERE id = ?
                """,
                (
                    status,
                    1 if transmitted else 0,
                    _now(),
                    error,
                    message_id,
                    draft_id,
                ),
            )
            self.conn.commit()

    def mailbox_ids(self, mailbox_key: str) -> set[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT external_id FROM mailbox_seen WHERE mailbox_key = ?",
                (mailbox_key,),
            ).fetchall()
            return {str(row["external_id"]) for row in rows}

    def set_policy(self, deal_id: str, fields: dict[str, Any]) -> None:
        allowed = {
            "hazard_intercept",
            "hazard_tau",
            "hazard_quote_gap",
            "reserve_prior_mean",
            "reserve_prior_std",
            "expected_markup",
            "search_cost",
            "include_unverified",
            "hazard_reserve_gap",
            "bayes_obs_noise",
            "unit_sanity_multiple",
        }
        updates = {key: fields[key] for key in allowed if key in fields}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock:
            self.conn.execute(
                f"UPDATE deals SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), _now(), deal_id),
            )
            self.conn.commit()

    def insert_outcome(
        self,
        *,
        deal_id: str,
        supplier_id: str,
        outcome: str,
        quote: float | None,
        offer: float | None,
        agreed_price: float | None,
    ) -> dict[str, Any]:
        with self._lock:
            outcome_id = uuid.uuid4().hex
            created = _now()
            self.conn.execute(
                """
                INSERT INTO hazard_outcomes (
                    id, deal_id, supplier_id, outcome, quote, offer, agreed_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id,
                    deal_id,
                    supplier_id,
                    outcome,
                    None if quote is None else float(quote),
                    None if offer is None else float(offer),
                    None if agreed_price is None else float(agreed_price),
                    created,
                ),
            )
            self.conn.commit()
            return {
                "id": outcome_id,
                "deal_id": deal_id,
                "supplier_id": supplier_id,
                "outcome": outcome,
                "quote": quote,
                "offer": offer,
                "agreed_price": agreed_price,
                "created_at": created,
            }

    def outcomes(self, deal_id: str, supplier_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if supplier_id is None:
                rows = self.conn.execute(
                    """
                    SELECT * FROM hazard_outcomes
                    WHERE deal_id = ?
                    ORDER BY created_at, id
                    """,
                    (deal_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT * FROM hazard_outcomes
                    WHERE deal_id = ? AND supplier_id = ?
                    ORDER BY created_at, id
                    """,
                    (deal_id, supplier_id),
                ).fetchall()
            return [dict(row) for row in rows]

    def agreed_multiples(self, product: str | None = None) -> list[float]:
        """Closed prices divided by that deal's target, for one product name.

        A closed umbrella does not move a bracket's reserve. Markup is not split out.
        """
        with self._lock:
            sql = """
                SELECT outcomes.agreed_price AS agreed_price, deals.target_price AS target_price
                FROM hazard_outcomes AS outcomes
                JOIN deals ON deals.id = outcomes.deal_id
                WHERE outcomes.outcome = 'agreed'
                  AND outcomes.agreed_price IS NOT NULL
                  AND deals.target_price > 0
            """
            params: tuple = ()
            if product:
                sql += " AND deals.product = ?"
                params = (product,)
            rows = self.conn.execute(sql, params).fetchall()
            return [float(row["agreed_price"]) / float(row["target_price"]) for row in rows]

    def set_hazard_counts(self, supplier_id: str, events: int, walks: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE suppliers SET hazard_events = ?, hazard_walks = ? WHERE id = ?",
                (int(events), int(walks), supplier_id),
            )
            self.conn.commit()

    def remember_mailbox(self, mailbox_key: str, external_id: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO mailbox_seen (mailbox_key, external_id)
                VALUES (?, ?)
                ON CONFLICT(mailbox_key, external_id) DO NOTHING
                """,
                (mailbox_key, external_id),
            )
            self.conn.commit()

    @staticmethod
    def _event(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["parsed"] = json.loads(item.pop("parsed_json") or "{}")
        item["provenance"] = json.loads(item.pop("provenance_json") or "{}")
        return item


_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def get_store(path: str | None = None) -> DeskStore:
    resolved = os.path.abspath(path or db_path())
    with _CACHE_LOCK:
        current = _CACHE.get("path")
        store = _CACHE.get("store")
        if store is None or current != resolved:
            if store is not None:
                store.close()
            store = DeskStore(resolved)
            _CACHE["store"] = store
            _CACHE["path"] = resolved
        return store


def reset_stores() -> None:
    with _CACHE_LOCK:
        store = _CACHE.pop("store", None)
        _CACHE.pop("path", None)
        if store is not None:
            store.close()
