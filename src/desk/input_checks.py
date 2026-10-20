"""Input checks for addresses, product text, and mail threading headers.

A supplier address is one addr-spec. A product becomes the mail subject, so a
control character is rejected when the deal is saved. Threading headers are
reused only when each token is one message-id.
"""
from __future__ import annotations

import re

from src.desk.store import DeskRejected

_ADDR = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
_MSGID = re.compile(
    r"<(?:[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+)@(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)>"
)


def require_plain(value: str, label: str) -> str:
    """Reject ASCII controls, including CR, LF, and DEL."""
    if any(not char.isprintable() for char in value):
        raise DeskRejected(f"{label} cannot contain control characters.")
    return value


def require_address(value: str) -> str:
    """Exactly one RFC 5322 addr-spec. Lists, display names, and folds are rejected."""
    text = str(value or "").strip()
    if any(not char.isprintable() for char in text):
        raise DeskRejected("Supplier address must be a single email address.")
    if any(mark in text for mark in (",", ";", " ", "<", ">", "(", ")")):
        raise DeskRejected("Supplier address must be a single email address.")
    if text.count("@") != 1 or not _ADDR.fullmatch(text):
        raise DeskRejected("Supplier address must be a single email address.")
    return text


def message_id_ok(value: str) -> bool:
    text = str(value or "").strip()
    if not text or any(not char.isprintable() for char in text):
        return False
    if any(mark in text for mark in (" ", "\t", ",")):
        return False
    return _MSGID.fullmatch(text) is not None


def message_id_or_blank(value: object) -> str:
    text = str(value or "").strip()
    return text if message_id_ok(text) else ""


def references_or_blank(value: object) -> str:
    kept: list[str] = []
    for token in str(value or "").split():
        if message_id_ok(token) and token not in kept:
            kept.append(token)
    return " ".join(kept)
