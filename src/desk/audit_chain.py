"""SHA-256 linked audit log for one desk deal. Tamper-evident, not tamper-proof.

entry_hash = SHA-256(canonical JSON of {deal_id, seq, type, ts, payload, prev_hash})

canonical JSON is sorted keys, no whitespace, UTF-8. seq starts at 1. The first
prev_hash is GENESIS (64 zeros). SQLite triggers reject UPDATE and DELETE.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

GENESIS = "0000000000000000000000000000000000000000000000000000000000000000"

SCHEMA = """
CREATE TABLE IF NOT EXISTS desk_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    UNIQUE (deal_id, seq)
);
CREATE TRIGGER IF NOT EXISTS desk_chain_no_update BEFORE UPDATE ON desk_chain
BEGIN SELECT RAISE(ABORT, 'desk_chain is append-only'); END;
CREATE TRIGGER IF NOT EXISTS desk_chain_no_delete BEFORE DELETE ON desk_chain
BEGIN SELECT RAISE(ABORT, 'desk_chain is append-only'); END;
"""


def normalize(payload: Any) -> Any:
    """JSON round-trip so the hashed object is what a later read rebuilds."""
    return json.loads(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def entry_hash(deal_id: str, seq: int, type_: str, ts: str, payload: Any, prev_hash: str) -> str:
    body = {
        "deal_id": deal_id,
        "seq": seq,
        "type": type_,
        "ts": ts,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(canonical(body)).hexdigest()


def append(conn: sqlite3.Connection, deal_id: str, type_: str, ts: str, payload: Any) -> dict[str, Any]:
    """Only write path. The caller is inside the store lock and one transaction."""
    last = conn.execute(
        "SELECT seq, entry_hash FROM desk_chain WHERE deal_id = ? ORDER BY seq DESC LIMIT 1",
        (deal_id,),
    ).fetchone()
    if last is None:
        seq, prev = 1, GENESIS
    else:
        seq, prev = int(last["seq"]) + 1, str(last["entry_hash"])
    stored = normalize(payload)
    digest = entry_hash(deal_id, seq, type_, ts, stored, prev)
    conn.execute(
        """
        INSERT INTO desk_chain (deal_id, seq, type, ts, payload_json, prev_hash, entry_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (deal_id, seq, type_, ts, json.dumps(stored, sort_keys=True, ensure_ascii=False), prev, digest),
    )
    return {"deal_id": deal_id, "seq": seq, "type": type_, "entry_hash": digest, "prev_hash": prev}


def verify(conn: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    """Recompute every hash. broken_seq is the first link that does not match."""
    rows = conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    entries = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        expected = entry_hash(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, row["prev_hash"])
        ok = row["prev_hash"] == prev and row["entry_hash"] == expected and int(row["seq"]) == len(entries) + 1
        item = {
            "seq": int(row["seq"]),
            "type": row["type"],
            "ts": row["ts"],
            "payload": payload,
            "prev_hash": row["prev_hash"],
            "entry_hash": row["entry_hash"],
            "ok": ok,
        }
        entries.append(item)
        if not ok:
            return {
                "ok": False,
                "broken_seq": int(row["seq"]),
                "head": None,
                "count": len(entries),
                "entries": entries,
            }
        prev = row["entry_hash"]
    return {
        "ok": True,
        "broken_seq": None,
        "head": prev if entries else GENESIS,
        "count": len(entries),
        "entries": entries,
    }
