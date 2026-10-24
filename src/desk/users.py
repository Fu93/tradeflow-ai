"""Named operator keys for the desk: issue, revoke, resolve, and identify.

Why this exists
---------------
The desk had exactly one credential: ``TRADEFLOW_API_KEY``, shared by every
buyer in the organisation. Two consequences, both real:

1. Revoking one person meant rotating the key for everyone, so revocation
   never happened and departed staff kept access.
2. The audit chain could prove *that* a send was approved by a human, but not
   *which* human — every request looked identical.

A named key is a secret shown once at issue time and stored only as a
SHA-256 hash plus a short prefix for display. ``revoke`` takes effect on the
next request without touching anyone else's access. The authenticated actor
travels with the request in a context variable and is recorded inside the
audit chain entry (``append_chain``), where the keyed MAC makes it as
tamper-evident as the rest of the payload.

Boundary (stated honestly): the actor string is the key's label, bound to
the secret only by custody of that secret. This is accountability to the
same engineering standard as the rest of the chain, not legal-grade
identity proofing.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextvars import ContextVar

# The desk needs every credential to be unguessable; this is the same bar as
# MIN_API_KEY_LEN in src/app/security.py, enforced by construction here.
_KEY_BYTES = 24  # 48 hex characters
KEY_PREFIX_DISPLAY_LEN = 12

# Set by the desk authentication dependency, read by ``DeskStore.append_chain``
# so the chain entry records who acted. A request that never went through the
# dependency (the mailbox poller, a CLI tool) records no actor, and the chain
# entry simply omits the field — the same shape as every pre-existing entry.
current_actor: ContextVar[str] = ContextVar("desk_actor", default="")


def hash_key(raw: str) -> str:
    """SHA-256 of the presented key. This is what storage keeps, never the raw."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def set_current_actor(actor: str) -> None:
    current_actor.set(actor)


def actor_from_chain_context() -> str:
    """The actor for a chain entry, or ``""`` when this write has no request."""
    return current_actor.get()


def _clean_label(label: str) -> str:
    """A label is printed in operator terminals and stored in chain payloads.

    [SECURITY 2026-10-24] Unrestricted labels meant an issuer could put
    ANSI escapes into ``manage_users.py list`` output or inject line breaks
    into the actor string that lands inside signed chain entries. One
    printable, line-free line, capped at a sane length — a label names a
    person, it is not an essay.
    """
    import re

    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(label or "")).strip()
    return cleaned[:64]


def issue(conn: sqlite3.Connection, label: str) -> dict[str, str | int]:
    """Mint one named key. The raw secret is in the return value only.

    The caller (``scripts/manage_users.py``) prints it once; storage keeps
    the hash and a display prefix, and nothing else — a database holder
    cannot recover a usable key from a row.
    """
    cleaned = _clean_label(label)
    if not cleaned:
        raise ValueError("A desk user needs a non-empty label.")
    raw = "tf_" + secrets.token_hex(_KEY_BYTES)
    from src.desk.store import _now

    conn.execute(
        "INSERT INTO desk_users (key_hash, key_prefix, label, created_at) VALUES (?, ?, ?, ?)",
        (hash_key(raw), raw[:KEY_PREFIX_DISPLAY_LEN], cleaned, _now()),
    )
    conn.commit()
    return {
        "id": int(conn.execute("SELECT last_insert_rowid()").fetchone()[0]),
        "label": cleaned,
        "key": raw,
        "key_prefix": raw[:KEY_PREFIX_DISPLAY_LEN],
    }


def revoke(conn: sqlite3.Connection, user_id: int) -> bool:
    """Revoke by id. Takes effect on the next request. Idempotent."""
    from src.desk.store import _now

    cursor = conn.execute(
        "UPDATE desk_users SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (_now(), int(user_id)),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_users(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Every named key without any secret material — hash included, never shown."""
    rows = conn.execute(
        "SELECT id, key_prefix, label, created_at, revoked_at FROM desk_users ORDER BY id"
    ).fetchall()
    listed = []
    for user_id, key_prefix, label, created_at, revoked_at in rows:
        listed.append(
            {
                "id": user_id,
                "key_prefix": key_prefix,
                "label": label,
                "created_at": created_at,
                "revoked_at": revoked_at,
            }
        )
    return listed


def resolve(conn: sqlite3.Connection, raw: str) -> dict[str, object] | None:
    """The active user behind a presented key, or ``None``.

    A revoked key resolves to ``None`` exactly like an unknown one — the
    caller answers 401 either way — so revocation cannot be probed by
    comparing error messages.
    """
    text = (raw or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT id, label, revoked_at FROM desk_users WHERE key_hash = ?",
        (hash_key(text),),
    ).fetchone()
    if row is None or row[2] is not None:
        return None
    return {"id": int(row[0]), "label": str(row[1])}


def actor_label(resolved: dict[str, object]) -> str:
    """The chain-facing name of an authenticated user key."""
    return f"user:{resolved['id']}:{resolved['label']}"
