"""HMAC-SHA256 linked audit log for one desk deal.

entry_hash = HMAC-SHA256(key, canonical JSON of
{deal_id, seq, type, ts, payload, prev_hash, key_id}).

A row written before key ids were stored (the round-5 chain) has an empty
key_id. Its MAC is HMAC-SHA256 of {deal_id, seq, type, ts, payload, prev_hash}
with TRADEFLOW_CHAIN_KEY. Those rows stay in place and verify. They are not
re-signed. An intact pre-key SHA-256 chain is a different object: only the
explicit migration archives it.

The key is read from the environment on each append and verify. It is never
written to the database. TRADEFLOW_CHAIN_KEY is the key for TRADEFLOW_CHAIN_KEY_ID
(default ``primary``). TRADEFLOW_CHAIN_KEYS holds retired ``id:secret`` pairs so
an older row still verifies after a rotation. A round-5 row has no key id, so
it verifies only with the current key.

TRADEFLOW_CHAIN_ANCHOR, when set to a file path or an http(s) URL, receives the
chain head after the database row is written. The file index is replaced
atomically. A corrupt index is rebuilt from the log for the deal that is being
read; other deals keep verifying from their own lines. A failed export marks
the deal pending and verification fails closed. A missing anchor file or a
missing deal line is also a failure when an anchor is configured. Without an
anchor, verify reports ``anchor not configured`` and a truncated tail still
verifies.

A head that the anchor recorded and the chain no longer contains is
``anchor missing because the chain was cut``. An anchor that cannot be read
is ``anchor missing because the anchor store was down``. Re-anchor appends
only when every recorded anchor head is still in the chain and the links
after it verify.

Each anchor record also carries the append-only export chain, HMAC-SHA256
with the same chain key: every exported head, and every exported approval.
A URL anchor that keeps only its latest body still returns that chain.
A missing or rewritten chain is not treated as "this approval was never
exported". Another approval waits for an operator ``export_recovery``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
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


def round5_digest(deal_id: str, seq: int, type_: str, ts: str, payload: Any, prev_hash: str) -> str:
    """HMAC of a round-5 body. That body has no key id."""
    body = {
        "deal_id": deal_id,
        "seq": seq,
        "type": type_,
        "ts": ts,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    signed = hmac.new(chain_key(), canonical(body), hashlib.sha256)
    return signed.hexdigest()


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


def _export_mac(body: dict[str, Any]) -> str:
    """HMAC of one export record. The chain key is not written into the anchor."""
    signed = hmac.new(chain_key(), canonical(body), hashlib.sha256)
    return signed.hexdigest()


def _export_body_from(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "deal_id": str(record.get("deal_id") or ""),
        "seq": int(record["seq"]),
        "entry_hash": str(record.get("entry_hash") or ""),
        "type": str(record.get("type") or ""),
        "prev_export": str(record.get("prev_export") or ""),
        "approvals": record.get("approvals"),
        "exports": record.get("exports"),
    }
    for key in ("draft_id", "body_sha256", "transmission_sha256"):
        if record.get(key):
            body[key] = str(record.get(key))
    return body


def _verified_export(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """The signed export chain carried by this anchor record.

    None means the bytes are not a chain this key will vouch for. A latest-only
    anchor is enough, because the latest record holds the whole chain.
    """
    if not isinstance(record, dict) or not _real_anchor_head(record):
        return None
    mac = str(record.get("export_mac") or "")
    approvals = record.get("approvals")
    exports = record.get("exports")
    if not mac or not isinstance(approvals, list) or not isinstance(exports, list):
        return None
    body = _export_body_from(record)
    expected = _export_mac(body)
    if len(mac) != len(expected) or not hmac.compare_digest(expected, mac):
        return None
    return {
        "approvals": [item for item in approvals if isinstance(item, dict)],
        "exports": [item for item in exports if isinstance(item, dict)],
        "export_mac": mac,
    }


def _empty_export() -> dict[str, Any]:
    return {"approvals": [], "exports": [], "export_mac": GENESIS}


def _anchor_not_found(exc: BaseException) -> bool:
    from src.config.heuristics import HTTP_NOT_FOUND

    return isinstance(exc, urllib.error.HTTPError) and exc.code == int(HTTP_NOT_FOUND)


def _current_anchor_record(deal_id: str) -> dict[str, Any]:
    """The latest anchor bytes. A missing deal is an empty store. Any other error propagates."""
    target = _anchor_target()
    if not target:
        return _anchor_absent()
    try:
        if _is_url(target):
            found = _read_anchor_url(target, deal_id)
        else:
            found = _read_anchor_file(target, deal_id)
    except urllib.error.HTTPError as exc:
        if _anchor_not_found(exc):
            return _anchor_absent()
        raise
    if not isinstance(found, dict):
        return _anchor_absent()
    return found


def _prior_export(deal_id: str, type_: str) -> dict[str, Any]:
    """The chain this export extends. Recovery is the only fresh start."""
    if type_ == "export_recovery":
        return _empty_export()
    record = _current_anchor_record(deal_id)
    if not _real_anchor_head(record):
        return _empty_export()
    verified = _verified_export(record)
    if verified is None:
        raise OSError("anchor history does not verify")
    return verified


def _anchor_absent() -> dict[str, Any]:
    return {"seq": -1, "entry_hash": ""}


def _anchor_is_required() -> bool:
    return bool(_anchor_target())


def _anchor_record(
    deal_id: str,
    seq: int,
    mac: str,
    type_: str,
    payload: dict[str, Any] | None,
    prior: dict[str, Any],
) -> dict[str, Any]:
    """The bytes the anchor stores. The latest record holds the signed export chain."""
    approvals = [dict(item) for item in list(prior.get("approvals") or []) if isinstance(item, dict)]
    exports = [dict(item) for item in list(prior.get("exports") or []) if isinstance(item, dict)]
    head: dict[str, Any] = {"seq": seq, "entry_hash": mac, "type": type_}
    if isinstance(payload, dict):
        for key in ("draft_id", "body_sha256", "transmission_sha256"):
            value = payload.get(key)
            if value:
                head[key] = str(value)
    exports.append(dict(head))
    if type_ == "send_approved":
        approvals.append(dict(head))
    body: dict[str, Any] = {
        "deal_id": deal_id,
        "seq": seq,
        "entry_hash": mac,
        "type": type_,
        "prev_export": str(prior.get("export_mac") or GENESIS),
        "approvals": approvals,
        "exports": exports,
    }
    for key in ("draft_id", "body_sha256", "transmission_sha256"):
        if head.get(key):
            body[key] = head[key]
    record = dict(body)
    record["export_mac"] = _export_mac(body)
    return record


def _export_anchor(
    deal_id: str,
    seq: int,
    mac: str,
    type_: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Write the head after the database row exists. A failure leaves the row and marks it pending."""
    target = _anchor_target()
    if not target:
        return
    prior = _prior_export(deal_id, type_)
    record = _anchor_record(deal_id, seq, mac, type_, payload, prior)
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
        handle.flush()
        os.fsync(handle.fileno())
    index_path = _index_path(path)
    index = _load_index(index_path, path)
    slot = index.get(deal_id)
    if not isinstance(slot, dict):
        slot = {}
    heads = slot.get("heads")
    if not isinstance(heads, list):
        heads = []
        if slot.get("entry_hash") and slot.get("offset") is not None:
            heads.append(
                {
                    "offset": slot.get("offset"),
                    "seq": slot.get("seq"),
                    "entry_hash": slot.get("entry_hash"),
                    "type": "",
                }
            )
    remembered = dict(record)
    remembered["offset"] = offset
    heads.append(remembered)
    index[deal_id] = {"offset": offset, "seq": seq, "entry_hash": mac, "heads": heads}
    index["_end"] = path.stat().st_size
    _write_index(index_path, index)


def _write_index(index_path: Path, index: dict[str, Any]) -> None:
    """Replace the index via a temp file, fsync, and rename."""
    directory = index_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".anchor-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(index, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, index_path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _index_from_log(log_path: Path) -> dict[str, Any]:
    """Every valid line of each deal, plus the latest offset. A broken line is skipped."""
    found: dict[str, Any] = {}
    if not log_path.is_file():
        return found
    offset = 0
    with log_path.open("rb") as handle:
        for raw in handle:
            start = offset
            offset += len(raw)
            try:
                item = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                continue
            if not isinstance(item, dict):
                continue
            deal_id = str(item.get("deal_id") or "")
            if not deal_id or "seq" not in item or "entry_hash" not in item:
                continue
            slot = found.get(deal_id)
            if not isinstance(slot, dict):
                slot = {"heads": []}
            heads = slot.get("heads")
            if not isinstance(heads, list):
                heads = []
            remembered = {
                "offset": start,
                "seq": item.get("seq"),
                "entry_hash": item.get("entry_hash"),
                "type": str(item.get("type") or ""),
            }
            for key in ("draft_id", "body_sha256", "transmission_sha256"):
                if item.get(key):
                    remembered[key] = str(item.get(key))
            heads.append(remembered)
            found[deal_id] = {
                "offset": start,
                "seq": item.get("seq"),
                "entry_hash": item.get("entry_hash"),
                "heads": heads,
            }
    found["_end"] = offset
    return found


def _load_index(index_path: Path, log_path: Path) -> dict[str, Any]:
    if index_path.is_file():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            loaded = None
        if isinstance(loaded, dict):
            return loaded
    return _index_from_log(log_path)


def _scan_anchor_log(path: Path, deal_id: str) -> dict[str, Any]:
    """The last valid line for this deal. Other deals are not failed by a bad line."""
    found: dict[str, Any] | None = None
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    item = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeError):
                    continue
                if isinstance(item, dict) and str(item.get("deal_id") or "") == deal_id:
                    found = item
    except OSError:
        return _anchor_absent()
    if found is None:
        return _anchor_absent()
    return found


def _read_anchor_file(target: str, deal_id: str) -> dict[str, Any] | None:
    """One indexed line for this deal. A corrupt index falls back to that deal's log lines."""
    path = Path(target)
    if not path.is_file():
        return _anchor_absent()
    index_path = _index_path(path)
    if not index_path.is_file():
        return _scan_anchor_log(path, deal_id)
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _scan_anchor_log(path, deal_id)
    if not isinstance(index, dict):
        return _scan_anchor_log(path, deal_id)
    slot = index.get(deal_id)
    if not isinstance(slot, dict) or "offset" not in slot:
        return _anchor_absent()
    try:
        with path.open("rb") as handle:
            handle.seek(int(slot["offset"]))
            line = handle.readline()
        item = json.loads(line.decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return _scan_anchor_log(path, deal_id)
    if not isinstance(item, dict) or str(item.get("deal_id") or "") != deal_id:
        return _scan_anchor_log(path, deal_id)
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


def _indexed_heads(path: Path, deal_id: str) -> list[dict[str, Any]] | None:
    """Heads from a complete index. None means the index cannot be trusted.

    A matching index is one seek of the latest line. The log is not read through.
    """
    index_path = _index_path(path)
    if not index_path.is_file():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        size = path.stat().st_size
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(index, dict) or index.get("_end") != size:
        return None
    slot = index.get(deal_id)
    if not isinstance(slot, dict) or "offset" not in slot:
        return []
    item = _read_anchor_file(str(path), deal_id)
    if not isinstance(item, dict) or not _real_anchor_head(item):
        return None
    try:
        same = int(item.get("seq", -1)) == int(slot.get("seq", -2)) and str(item.get("entry_hash") or "") == str(
            slot.get("entry_hash") or ""
        )
    except (TypeError, ValueError):
        return None
    if not same:
        return None
    verified = _verified_export(item)
    if verified is not None:
        signed = [head for head in verified["exports"] if _real_anchor_head(head)]
        if signed:
            return signed
    heads = slot.get("heads")
    if isinstance(heads, list) and heads:
        return [head for head in heads if isinstance(head, dict)]
    return [item]


def _url_heads(item: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Heads carried by the latest URL record. A signed chain beats a single head."""
    if not isinstance(item, dict):
        return []
    verified = _verified_export(item)
    if verified is not None:
        signed = [head for head in verified["exports"] if _real_anchor_head(head)]
        if signed:
            return signed
    raw = item.get("heads")
    if isinstance(raw, list):
        found = [head for head in raw if _real_anchor_head(head)]
        if found:
            return found
    if _real_anchor_head(item):
        return [item]
    return []


def _heads_from_log(path: Path, deal_id: str) -> list[dict[str, Any]]:
    """Every valid anchor line for this deal, in log order."""
    found: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                item = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                continue
            if isinstance(item, dict) and str(item.get("deal_id") or "") == deal_id and _real_anchor_head(item):
                found.append(item)
    return found


def _anchor_observation(deal_id: str) -> dict[str, Any]:
    """The recorded heads, or why they could not be read.

    ``status`` is ``unset``, ``absent``, ``down``, or ``head``. A missing file
    is absent. An error talking to the store is down. Those two are not the
    same event.
    """
    target = _anchor_target()
    if not target:
        return {"status": "unset", "heads": []}
    try:
        if _is_url(target):
            item = _read_anchor_url(target, deal_id)
            heads = _url_heads(item)
            if not heads:
                return {"status": "absent", "heads": []}
            return {"status": "head", "heads": heads}
        path = Path(target)
        if not path.is_file():
            return {"status": "absent", "heads": []}
        indexed = _indexed_heads(path, deal_id)
        heads = _heads_from_log(path, deal_id) if indexed is None else indexed
    except urllib.error.HTTPError as exc:
        if _anchor_not_found(exc):
            return {"status": "absent", "heads": []}
        return {"status": "down", "heads": []}
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError, UnicodeError):
        return {"status": "down", "heads": []}
    if not heads:
        return {"status": "absent", "heads": []}
    return {"status": "head", "heads": heads}


def _recorded_heads_in_chain(entries: list[dict[str, Any]], heads: list[dict[str, Any]]) -> bool:
    """Every anchored head is still this chain. A missing head is a cut."""
    known = {(int(item["seq"]), str(item["entry_hash"])) for item in entries}
    for head in heads:
        if not _real_anchor_head(head):
            continue
        if (int(head["seq"]), str(head["entry_hash"])) not in known:
            return False
    return True


def export_history(deal_id: str) -> dict[str, Any]:
    """The signed approval chain, or why it cannot be used.

    ``status`` is ``unset`` (no anchor), ``absent`` (nothing stored), ``down``
    (the store could not be read), ``invalid`` (bytes that this key will not
    vouch for), or ``ok``. A missing or invalid chain is not an empty one.
    """
    target = _anchor_target()
    if not target:
        return {"status": "unset", "approvals": []}
    try:
        record = _current_anchor_record(deal_id)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError, UnicodeError):
        return {"status": "down", "approvals": []}
    if not _real_anchor_head(record):
        return {"status": "absent", "approvals": []}
    verified = _verified_export(record)
    if verified is None:
        return {"status": "invalid", "approvals": []}
    return {"status": "ok", "approvals": list(verified["approvals"])}


def reanchor_refusal(deal_id: str, entries: list[dict[str, Any]]) -> str | None:
    """Refuse a re-anchor that would sign a chain shorter than the anchor remembers.

    A readable head must still be in this chain. No readable head, when an
    anchor is configured, is the same refusal: the cut cannot be ruled out.
    A store that errors is reported separately and is not signed over.
    An anchor whose export chain does not verify is not signed over either.
    """
    history = export_history(deal_id)
    if history["status"] == "down":
        return "Re-anchor refused. The anchor store was down."
    if history["status"] == "invalid":
        return "Re-anchor refused. The anchor history does not verify."
    observed = _anchor_observation(deal_id)
    if observed["status"] == "unset":
        return None
    if observed["status"] == "down":
        return "Re-anchor refused. The anchor store was down."
    if observed["status"] != "head":
        return "Re-anchor refused. Possible truncation: the chain was cut."
    if _recorded_heads_in_chain(entries, list(observed["heads"])):
        return None
    return "Re-anchor refused. Possible truncation: the chain was cut."


def _head_is_approval(head: dict[str, Any], *, draft_id: str, body_sha256: str, transmission_sha256: str) -> bool:
    """True when this anchor line is the approval of this envelope."""
    if str(head.get("type") or "") != "send_approved":
        return False
    same_envelope = bool(transmission_sha256) and str(head.get("transmission_sha256") or "") == transmission_sha256
    same_body = (
        bool(draft_id)
        and str(head.get("draft_id") or "") == str(draft_id)
        and str(head.get("body_sha256") or "") == body_sha256
    )
    return same_envelope or same_body


def approval_recorded(
    deal_id: str,
    entries: list[dict[str, Any]],
    *,
    draft_id: str,
    body_sha256: str,
    transmission_sha256: str,
) -> bool:
    """The signed anchor chain already has this approval. A later copy is a duplicate.

    The database rows after that approval are not what decides it. Only an
    export chain this key verifies can say the approval is absent. A missing
    or rewritten chain is not that evidence.
    """
    history = export_history(deal_id)
    if history["status"] != "ok":
        return False
    known = {
        (int(item["seq"]), str(item["entry_hash"])): item
        for item in entries
        if item.get("seq") is not None and item.get("entry_hash")
    }
    for head in list(history.get("approvals") or []):
        if not isinstance(head, dict):
            continue
        if _head_is_approval(
            head,
            draft_id=draft_id,
            body_sha256=body_sha256,
            transmission_sha256=transmission_sha256,
        ):
            return True
        if not _real_anchor_head(head):
            continue
        matched = known.get((int(head["seq"]), str(head["entry_hash"])))
        if matched is None:
            continue
        payload = dict(matched.get("payload") or {})
        payload["type"] = matched.get("type")
        if _head_is_approval(
            payload,
            draft_id=draft_id,
            body_sha256=body_sha256,
            transmission_sha256=transmission_sha256,
        ):
            return True
    return False


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


def _blank_key_id(row: Any) -> bool:
    try:
        stored = row["key_id"]
    except (KeyError, IndexError):
        return True
    return stored is None or str(stored).strip() == ""


def _row_key_id(row: Any) -> str:
    if _blank_key_id(row):
        return ""
    return str(row["key_id"])


def _expected_mac(row: Any, payload: Any) -> str:
    if _blank_key_id(row):
        return round5_digest(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, row["prev_hash"])
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
    anchor_ok = True
    try:
        _export_anchor(deal_id, seq, digest, type_, stored)
        if type_ in {"reanchor", "export_recovery"} or not _pending_anchor(conn, deal_id):
            _clear_anchor_pending(conn, deal_id)
    except Exception:
        _remember_anchor_pending(conn, deal_id, seq, digest)
        anchor_ok = False
    return {
        "deal_id": deal_id,
        "seq": seq,
        "type": type_,
        "entry_hash": digest,
        "prev_hash": prev,
        "key_id": key_id,
        "anchor_ok": anchor_ok,
    }


def _real_anchor_head(item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    try:
        seq = int(item.get("seq", -1))
    except (TypeError, ValueError):
        return False
    return seq >= 0 and bool(str(item.get("entry_hash") or ""))


def anchor_head_present(deal_id: str) -> bool:
    """True when this deal already has an anchor head. An unreadable anchor counts as present."""
    target = _anchor_target()
    if not target:
        return False
    try:
        if _is_url(target):
            item = _read_anchor_url(target, deal_id)
        else:
            item = _read_anchor_file(target, deal_id)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError, UnicodeError):
        return True
    return _real_anchor_head(item)


def verify_links(conn: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    """Recompute every MAC. The anchor and the pending mark are not consulted."""
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
    return {
        "ok": True,
        "broken_seq": None,
        "head": head,
        "count": len(entries),
        "entries": entries,
    }


def verify(conn: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    """Recompute every MAC, then require the anchor when one is configured."""
    result = verify_links(conn, deal_id)
    if not result["ok"]:
        return _annotate(result)
    entries = list(result.get("entries") or [])
    head = str(result.get("head") or "")
    if _pending_anchor(conn, deal_id):
        pending = _annotate(_broken(len(entries) or 1, len(entries), entries))
        pending["anchor"] = "anchor missing because the anchor store was down"
        return pending
    observed = _anchor_observation(deal_id)
    if observed["status"] == "down":
        down = _annotate(_broken(len(entries) or 1, len(entries), entries))
        down["anchor"] = "anchor missing because the anchor store was down"
        return down
    anchored = _read_anchor(deal_id)
    if _anchor_is_required():
        if anchored is None or not _anchor_matches(anchored, len(entries), head if entries else ""):
            broken = _annotate(_broken(len(entries) or 1, len(entries), entries))
            if observed["status"] == "head" and not _recorded_heads_in_chain(entries, list(observed["heads"])):
                broken["anchor"] = "anchor missing because the chain was cut"
            return broken
    return _annotate(result)
