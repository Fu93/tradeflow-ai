"""Input checks for addresses, product text, and mail threading headers.

A supplier address is one addr-spec. A product becomes the mail subject, so a
control character is rejected when the deal is saved. Threading headers are
reused only when each token is one message-id.
"""
from __future__ import annotations

import re
import math

from src.desk.store import DeskRejected
from src.config.heuristics import MINOR_SCALE, UNIT_SANITY_MULTIPLE


def require_price_band(target: float, ceiling: float, multiple: float = UNIT_SANITY_MULTIPLE) -> None:
    """Reject finite inputs whose derived bounds overflow or underflow."""
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in (target, ceiling, multiple)):
        raise DeskRejected("Prices and the plausibility multiple must be finite and above zero.")
    high = max(float(target), float(ceiling)) * float(multiple)
    low = float(target) / float(multiple)
    if not math.isfinite(high) or not math.isfinite(high / float(target)) or not math.isfinite(low) or low <= 0:
        raise DeskRejected("The price range is too large or too small for stable arithmetic.")
    if math.ulp(high) > 1.0 / MINOR_SCALE:
        raise DeskRejected("The price range is too large to preserve currency minor-unit precision.")


def require_landed_total(unit: float, freight: float | None, fees: float | None, duty: float | None) -> None:
    parts = [float(value) for value in (freight, fees, duty) if value is not None]
    if any(not math.isfinite(value) or value < 0 for value in parts):
        raise DeskRejected("Landed-cost inputs must be finite and non-negative.")
    total = (float(unit) + float(freight or 0) + float(fees or 0)) * (1.0 + float(duty or 0))
    if not math.isfinite(total):
        raise DeskRejected("Landed-cost inputs exceed the finite arithmetic range.")
    if math.ulp(total) > 1.0 / MINOR_SCALE:
        raise DeskRejected("Landed-cost inputs are too large to preserve currency minor-unit precision.")

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


# Suppliers reached in a chat app rather than by mail. Their drafts are copied
# and sent by a person; the desk never mails them.
CHAT_CHANNELS = ("wechat", "whatsapp", "chat")
EMAIL_CHANNEL = "email"


def require_contact(value: str, channel: str) -> str:
    """An email address for the email channel; a single-line handle for a chat channel."""
    if channel == EMAIL_CHANNEL:
        return require_address(value)
    if channel not in CHAT_CHANNELS:
        raise DeskRejected("Supplier channel must be email, wechat, whatsapp or chat.")
    text = str(value or "").strip()
    if not text or any(not char.isprintable() for char in text):
        raise DeskRejected("A chat contact must be one line of printable text.")
    if any(mark in text for mark in ("<", ">", ";", ",")):
        raise DeskRejected("A chat contact cannot contain < > ; or commas.")
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
