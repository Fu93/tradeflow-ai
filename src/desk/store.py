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


class DeskRejected(ValueError):
    """The caller asked for a deal the desk will not store."""


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
        for column, ddl in (
            ("hazard_intercept", "REAL"),
            ("hazard_tau", "REAL"),
            ("hazard_quote_gap", "REAL"),
            ("reserve_prior_mean", "REAL"),
            ("reserve_prior_std", "REAL"),
            ("expected_markup", "REAL"),
            ("search_cost", "REAL"),
            ("include_unverified", "INTEGER NOT NULL DEFAULT 0"),
        ):
            self._ensure_column("deals", column, ddl)
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
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        found = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in found:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

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
    ) -> dict[str, Any]:
        with self._lock:
            deal_id = uuid.uuid4().hex
            now = _now()
            self.conn.execute(
                """
                INSERT INTO deals (
                    id, product, quantity, target_price, ceiling_price,
                    risk_tolerance, seed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            self.conn.commit()
            deal = self._deal(deal_id)
            deal["supplier_ids"] = rows
            return deal

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
                        confidence, status, provenance_json, external_id, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            return [self._event(row) for row in rows]

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
                ORDER BY created_at DESC, id DESC
                """,
                (deal_id, supplier_id),
            ).fetchall()
            for existing in rows:
                if existing["body"] == body and existing["status"] in {
                    "draft",
                    "sent",
                    "approved",
                    "sending",
                    "failed",
                }:
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
            self.conn.execute(
                """
                INSERT INTO drafts (
                    id, deal_id, supplier_id, body, status, human_approved,
                    transmitted, created_at, sent_at, gmail_draft_id, gmail_error,
                    version, body_sha256, reply_to, references_header
                ) VALUES (?, ?, ?, ?, 'draft', 0, 0, ?, NULL, NULL, NULL, ?, ?, ?, ?)
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

    def drafts(self, deal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT * FROM drafts
                WHERE deal_id = ?
                ORDER BY created_at DESC, id DESC
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

    def agreed_multiples(self) -> list[float]:
        """Closed prices divided by that deal's target. Markup is not split out."""
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT outcomes.agreed_price AS agreed_price, deals.target_price AS target_price
                FROM hazard_outcomes AS outcomes
                JOIN deals ON deals.id = outcomes.deal_id
                WHERE outcomes.outcome = 'agreed'
                  AND outcomes.agreed_price IS NOT NULL
                  AND deals.target_price > 0
                """
            ).fetchall()
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
