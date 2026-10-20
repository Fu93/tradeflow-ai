"""HMAC-SHA256 linked audit log for one desk deal.

entry_hash = HMAC-SHA256(TRADEFLOW_CHAIN_KEY, canonical JSON of
{deal_id, seq, type, ts, payload, prev_hash}).

The key is read from the environment on each append and verify. It is never
written to the database. TRADEFLOW_CHAIN_ANCHOR, when set to a file path or
an http(s) URL, receives the chain head (seq and MAC) outside the database.
Without that anchor, a truncated tail of an otherwise valid chain still verifies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

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


class ChainKeyMissing(RuntimeError):
    """Desk chain writes need TRADEFLOW_CHAIN_KEY and the process does not have it."""


def chain_key_configured() -> bool:
    return bool(os.environ.get("TRADEFLOW_CHAIN_KEY", "").strip())


def chain_key() -> bytes:
    raw = os.environ.get("TRADEFLOW_CHAIN_KEY", "").strip()
    if not raw:
        raise ChainKeyMissing("TRADEFLOW_CHAIN_KEY is not set.")
    return raw.encode("utf-8")


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
    return hmac.new(chain_key(), canonical(body), hashlib.sha256).hexdigest()


def _anchor_target() -> str:
    return os.environ.get("TRADEFLOW_CHAIN_ANCHOR", "").strip()


def _is_url(target: str) -> bool:
    return target.startswith("http://") or target.startswith("https://")


def _anchor_timeout() -> float:
    from src.config.heuristics import CHAIN_ANCHOR_TIMEOUT_SECONDS

    return float(CHAIN_ANCHOR_TIMEOUT_SECONDS)


def _export_anchor(deal_id: str, seq: int, mac: str) -> None:
    """Write the new head before the row is inserted. A failed export leaves the chain unchanged."""
    target = _anchor_target()
    if not target:
        return
    record = {"deal_id": deal_id, "seq": seq, "entry_hash": mac}
    raw = json.dumps(record, sort_keys=True).encode("utf-8")
    if _is_url(target):
        request = urllib.request.Request(
            target,
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=_anchor_timeout()) as response:
            response.read()
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(raw.decode("utf-8") + "\n")


def _read_anchor_file(target: str, deal_id: str) -> dict[str, Any] | None:
    path = Path(target)
    if not path.is_file():
        return None
    found: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict) and str(item.get("deal_id") or "") == deal_id:
            found = item
    return found


def _read_anchor_url(target: str, deal_id: str) -> dict[str, Any] | None:
    parts = urlsplit(target)
    query = urlencode({"deal_id": deal_id})
    joined = parts.query + ("&" if parts.query else "") + query
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, joined, parts.fragment))
    with urllib.request.urlopen(url, timeout=_anchor_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return {"seq": -1, "entry_hash": ""}
    return payload


def _read_anchor(deal_id: str) -> dict[str, Any] | None:
    target = _anchor_target()
    if not target:
        return None
    try:
        if _is_url(target):
            return _read_anchor_url(target, deal_id)
        return _read_anchor_file(target, deal_id)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return {"seq": -1, "entry_hash": ""}


def _anchor_matches(anchored: dict[str, Any], head_seq: int, head_mac: str) -> bool:
    return int(anchored["seq"]) == head_seq and str(anchored["entry_hash"]) == head_mac


def _broken(seq: int, count: int, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "broken_seq": seq,
        "head": None,
        "count": count,
        "entries": entries,
    }


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
    _export_anchor(deal_id, seq, digest)
    conn.execute(
        """
        INSERT INTO desk_chain (deal_id, seq, type, ts, payload_json, prev_hash, entry_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (deal_id, seq, type_, ts, json.dumps(stored, sort_keys=True, ensure_ascii=False), prev, digest),
    )
    return {"deal_id": deal_id, "seq": seq, "type": type_, "entry_hash": digest, "prev_hash": prev}


def verify(conn: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    """Recompute every MAC. broken_seq is the first link that does not match."""
    if not chain_key_configured():
        return _broken(1, 0, [])
    rows = conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    entries: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        try:
            expected = entry_hash(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, row["prev_hash"])
        except ChainKeyMissing:
            return _broken(int(row["seq"]), len(entries) + 1, entries)
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
            return _broken(int(row["seq"]), len(entries), entries)
        prev = row["entry_hash"]
    head = prev if entries else GENESIS
    anchored = _read_anchor(deal_id)
    if anchored is not None and not _anchor_matches(anchored, len(entries), head if entries else ""):
        return _broken(len(entries) or 1, len(entries), entries)
    return {
        "ok": True,
        "broken_seq": None,
        "head": head,
        "count": len(entries),
        "entries": entries,
    }
