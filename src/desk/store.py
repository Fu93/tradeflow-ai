"""SQLite desk. One file, reopened after a restart."""
from __future__ import annotations

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
            """
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

    def replace_open_draft(self, deal_id: str, supplier_id: str, body: str) -> dict[str, Any]:
        with self._lock:
            existing = self.conn.execute(
                """
                SELECT * FROM drafts
                WHERE deal_id = ? AND supplier_id = ? AND status = 'draft'
                ORDER BY created_at DESC LIMIT 1
                """,
                (deal_id, supplier_id),
            ).fetchone()
            if existing:
                if existing["body"] != body:
                    self.conn.execute(
                        """
                        UPDATE drafts
                        SET body = ?, gmail_draft_id = NULL, gmail_error = NULL
                        WHERE id = ?
                        """,
                        (body, existing["id"]),
                    )
                    self.conn.commit()
                row = self.conn.execute(
                    "SELECT * FROM drafts WHERE id = ?",
                    (existing["id"],),
                ).fetchone()
                return dict(row)
            draft_id = uuid.uuid4().hex
            now = _now()
            self.conn.execute(
                """
                INSERT INTO drafts (
                    id, deal_id, supplier_id, body, status, human_approved,
                    transmitted, created_at, sent_at, gmail_draft_id, gmail_error
                ) VALUES (?, ?, ?, ?, 'draft', 0, 0, ?, NULL, NULL, NULL)
                """,
                (draft_id, deal_id, supplier_id, body, now),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT * FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            return dict(row)

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

    def mark_sent(self, draft_id: str, *, transmitted: bool) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE drafts
                SET status = 'sent', human_approved = 1, transmitted = ?, sent_at = ?
                WHERE id = ?
                """,
                (1 if transmitted else 0, _now(), draft_id),
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
