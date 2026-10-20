"""HMAC-SHA256 linked audit log for one desk deal.

entry_hash = HMAC-SHA256(key, canonical JSON of
{deal_id, seq, type, ts, payload, prev_hash, key_id}).

The key is read from the environment on each append and verify. It is never
written to the database. TRADEFLOW_CHAIN_KEY is the key for TRADEFLOW_CHAIN_KEY_ID
(default ``primary``). TRADEFLOW_CHAIN_KEYS holds retired ``id:secret`` pairs so
an older row still verifies after a rotation.

TRADEFLOW_CHAIN_ANCHOR, when set to a file path or an http(s) URL, receives the
chain head after the database row is written. A failed export marks the deal
pending and verification fails closed. A missing anchor file or a missing deal
line is also a failure when an anchor is configured. Without an anchor, verify
reports ``anchor not configured`` and a truncated tail still verifies.
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
LEGACY_NOTE = "legacy unverified"

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
    key_id TEXT,
    UNIQUE (deal_id, seq)
);
CREATE TRIGGER IF NOT EXISTS desk_chain_no_update BEFORE UPDATE ON desk_chain
BEGIN SELECT RAISE(ABORT, 'desk_chain is append-only'); END;
CREATE TRIGGER IF NOT EXISTS desk_chain_no_delete BEFORE DELETE ON desk_chain
BEGIN SELECT RAISE(ABORT, 'desk_chain is append-only'); END;
CREATE TABLE IF NOT EXISTS desk_chain_legacy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS desk_chain_legacy_no_update BEFORE UPDATE ON desk_chain_legacy
BEGIN SELECT RAISE(ABORT, 'desk_chain_legacy is read-only'); END;
CREATE TRIGGER IF NOT EXISTS desk_chain_legacy_no_delete BEFORE DELETE ON desk_chain_legacy
BEGIN SELECT RAISE(ABORT, 'desk_chain_legacy is read-only'); END;
CREATE TABLE IF NOT EXISTS desk_anchor_pending (
    deal_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    entry_hash TEXT NOT NULL
);
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


def current_key_id() -> str:
    return os.environ.get("TRADEFLOW_CHAIN_KEY_ID", "").strip() or "primary"


def keyring() -> dict[str, bytes]:
    """Retired keys. ``id:secret`` pairs, separated by commas. The current key is not stored here."""
    raw = os.environ.get("TRADEFLOW_CHAIN_KEYS", "").strip()
    found: dict[str, bytes] = {}
    if not raw:
        return found
    for part in raw.split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        key_id, secret = piece.split(":", 1)
        key_id = key_id.strip()
        secret = secret.strip()
        if key_id and secret:
            found[key_id] = secret.encode("utf-8")
    return found


def normalize(payload: Any) -> Any:
    """JSON round-trip so the hashed object is what a later read rebuilds."""
    return json.loads(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False))


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _mac_body(deal_id: str, seq: int, type_: str, ts: str, payload: Any, prev_hash: str, key_id: str) -> dict[str, Any]:
    return {
        "deal_id": deal_id,
        "seq": seq,
        "type": type_,
        "ts": ts,
        "payload": payload,
        "prev_hash": prev_hash,
        "key_id": key_id,
    }


def mac_with_key(
    key: bytes,
    deal_id: str,
    seq: int,
    type_: str,
    ts: str,
    payload: Any,
    prev_hash: str,
    key_id: str,
) -> str:
    body = _mac_body(deal_id, seq, type_, ts, payload, prev_hash, key_id)
    return hmac.new(key, canonical(body), hashlib.sha256).hexdigest()


def _uses_current_key(key_id: str) -> bool:
    return key_id == current_key_id()


def entry_hash(deal_id: str, seq: int, type_: str, ts: str, payload: Any, prev_hash: str) -> str:
    body = _mac_body(deal_id, seq, type_, ts, payload, prev_hash, current_key_id())
    return hmac.new(chain_key(), canonical(body), hashlib.sha256).hexdigest()


def legacy_digest(deal_id: str, seq: int, type_: str, ts: str, payload: Any, prev_hash: str) -> str:
    """The pre-key SHA-256 link. It has no key id. New rows do not use it."""
    body = {
        "deal_id": deal_id,
        "seq": seq,
        "type": type_,
        "ts": ts,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(canonical(body)).hexdigest()


def legacy_unkeyed(rows: list[Any]) -> bool:
    """True when every row is a pre-key SHA-256 link and the links are intact."""
    if not rows:
        return False
    prev = GENESIS
    for index, row in enumerate(rows):
        payload = json.loads(row["payload_json"])
        digest = legacy_digest(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, prev)
        intact = (
            str(row["prev_hash"]) == prev
            and str(row["entry_hash"]) == digest
            and int(row["seq"]) == index + 1
        )
        if not intact:
            return False
        prev = digest
    return True


def _anchor_target() -> str:
    return os.environ.get("TRADEFLOW_CHAIN_ANCHOR", "").strip()


def _is_url(target: str) -> bool:
    return target.startswith("http://") or target.startswith("https://")


def _anchor_timeout() -> float:
    from src.config.heuristics import CHAIN_ANCHOR_TIMEOUT_SECONDS

    return float(CHAIN_ANCHOR_TIMEOUT_SECONDS)


def _index_path(path: Path) -> Path:
    return Path(str(path) + ".index")


def _anchor_absent() -> dict[str, Any]:
    return {"seq": -1, "entry_hash": ""}


def _anchor_is_required() -> bool:
    return bool(_anchor_target())


def _export_anchor(deal_id: str, seq: int, mac: str) -> None:
    """Write the head after the database row exists. A failure leaves the row and marks it pending."""
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
    line = raw.decode("utf-8") + "\n"
    with path.open("a", encoding="utf-8") as handle:
        offset = handle.tell()
        handle.write(line)
    index_path = _index_path(path)
    index: dict[str, Any] = {}
    if index_path.is_file():
        loaded = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            index = loaded
    index[deal_id] = {"offset": offset, "seq": seq, "entry_hash": mac}
    index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")


def _read_anchor_file(target: str, deal_id: str) -> dict[str, Any] | None:
    """One indexed line for this deal. The whole log is not read."""
    path = Path(target)
    index_path = _index_path(path)
    if not path.is_file() or not index_path.is_file():
        return _anchor_absent()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    slot = index.get(deal_id) if isinstance(index, dict) else None
    if not isinstance(slot, dict) or "offset" not in slot:
        return _anchor_absent()
    with path.open("rb") as handle:
        handle.seek(int(slot["offset"]))
        line = handle.readline()
    item = json.loads(line.decode("utf-8"))
    if not isinstance(item, dict) or str(item.get("deal_id") or "") != deal_id:
        return _anchor_absent()
    return item


def _read_anchor_url(target: str, deal_id: str) -> dict[str, Any] | None:
    parts = urlsplit(target)
    query = urlencode({"deal_id": deal_id})
    joined = parts.query + ("&" if parts.query else "") + query
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, joined, parts.fragment))
    with urllib.request.urlopen(url, timeout=_anchor_timeout()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or "seq" not in payload:
        return _anchor_absent()
    return payload


def _read_anchor(deal_id: str) -> dict[str, Any] | None:
    target = _anchor_target()
    if not target:
        return None
    try:
        if _is_url(target):
            return _read_anchor_url(target, deal_id)
        return _read_anchor_file(target, deal_id)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError, UnicodeError):
        return _anchor_absent()


def _anchor_matches(anchored: dict[str, Any], head_seq: int, head_mac: str) -> bool:
    return int(anchored["seq"]) == head_seq and str(anchored["entry_hash"]) == head_mac


def _remember_anchor_pending(conn: sqlite3.Connection, deal_id: str, seq: int, mac: str) -> None:
    conn.execute(
        """
        INSERT INTO desk_anchor_pending (deal_id, seq, entry_hash)
        VALUES (?, ?, ?)
        ON CONFLICT(deal_id) DO UPDATE SET seq = excluded.seq, entry_hash = excluded.entry_hash
        """,
        (deal_id, seq, mac),
    )


def _clear_anchor_pending(conn: sqlite3.Connection, deal_id: str) -> None:
    conn.execute("DELETE FROM desk_anchor_pending WHERE deal_id = ?", (deal_id,))


def _pending_anchor(conn: sqlite3.Connection, deal_id: str) -> bool:
    row = conn.execute(
        "SELECT seq FROM desk_anchor_pending WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()
    return row is not None


def _broken(seq: int, count: int, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": False,
        "broken_seq": seq,
        "head": None,
        "count": count,
        "entries": entries,
    }


def _annotate(result: dict[str, Any]) -> dict[str, Any]:
    if not _anchor_target():
        result["anchor"] = "anchor not configured"
    elif result.get("ok"):
        result["anchor"] = "anchored"
    else:
        result["anchor"] = "anchor missing"
    return result


def _row_key_id(row: Any) -> str:
    try:
        stored = row["key_id"]
    except (KeyError, IndexError):
        stored = ""
    return str(stored or "") or current_key_id()


def _expected_mac(row: Any, payload: Any) -> str:
    key_id = _row_key_id(row)
    if _uses_current_key(key_id):
        return entry_hash(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, row["prev_hash"])
    secret = keyring().get(key_id)
    if secret is None:
        raise ChainKeyMissing("TRADEFLOW_CHAIN_KEYS has no secret for this row.")
    return mac_with_key(
        secret,
        row["deal_id"],
        int(row["seq"]),
        row["type"],
        row["ts"],
        payload,
        row["prev_hash"],
        key_id,
    )


def append(conn: sqlite3.Connection, deal_id: str, type_: str, ts: str, payload: Any) -> dict[str, Any]:
    """Only write path. The database row is written before the anchor."""
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
    key_id = current_key_id()
    conn.execute(
        """
        INSERT INTO desk_chain (deal_id, seq, type, ts, payload_json, prev_hash, entry_hash, key_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (deal_id, seq, type_, ts, json.dumps(stored, sort_keys=True, ensure_ascii=False), prev, digest, key_id),
    )
    try:
        _export_anchor(deal_id, seq, digest)
        _clear_anchor_pending(conn, deal_id)
    except Exception:
        _remember_anchor_pending(conn, deal_id, seq, digest)
    return {"deal_id": deal_id, "seq": seq, "type": type_, "entry_hash": digest, "prev_hash": prev, "key_id": key_id}


def verify(conn: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    """Recompute every MAC. broken_seq is the first link that does not match."""
    if not chain_key_configured():
        return _annotate(_broken(1, 0, []))
    rows = conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    entries: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        try:
            expected = _expected_mac(row, payload)
        except ChainKeyMissing:
            return _annotate(_broken(int(row["seq"]), len(entries) + 1, entries))
        linked = row["prev_hash"] == prev and row["entry_hash"] == expected and int(row["seq"]) == len(entries) + 1
        item = {
            "seq": int(row["seq"]),
            "type": row["type"],
            "ts": row["ts"],
            "payload": payload,
            "prev_hash": row["prev_hash"],
            "entry_hash": row["entry_hash"],
            "key_id": _row_key_id(row),
            "ok": linked,
        }
        entries.append(item)
        if not linked:
            return _annotate(_broken(int(row["seq"]), len(entries), entries))
        prev = row["entry_hash"]
    head = prev if entries else GENESIS
    if _pending_anchor(conn, deal_id):
        return _annotate(_broken(len(entries) or 1, len(entries), entries))
    anchored = _read_anchor(deal_id)
    if _anchor_is_required():
        if anchored is None or not _anchor_matches(anchored, len(entries), head if entries else ""):
            return _annotate(_broken(len(entries) or 1, len(entries), entries))
    return _annotate(
        {
            "ok": True,
            "broken_seq": None,
            "head": head,
            "count": len(entries),
            "entries": entries,
        }
    )
