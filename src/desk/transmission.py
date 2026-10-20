"""Deterministic send envelope. The audit chain is the only source.

The recipient, the subject, the reply headers, and the body are rendered
from chained values and hashed together. A later edit of the supplier row
or the deal row is not what gets sent.
"""
from __future__ import annotations

import hashlib
from typing import Any

from src.desk.audit_chain import canonical


def counter_subject(product: str) -> str:
    return f"Counter for {product}"


def join_references(prior: str | None, message_id: str) -> str:
    parts: list[str] = []
    for token in str(prior or "").split():
        if token and token not in parts:
            parts.append(token)
    if message_id and message_id not in parts:
        parts.append(message_id)
    return " ".join(parts)


def chained_deal(entries: list[dict[str, Any]]) -> dict[str, Any]:
    for entry in entries:
        if entry.get("type") == "deal_opened":
            return dict(entry.get("payload") or {})
    return {}


def chained_supplier(entries: list[dict[str, Any]], supplier_id: str) -> dict[str, str] | None:
    """Latest human-confirmed identity for this supplier. The deals table is not read."""
    found: dict[str, str] | None = None
    opened = chained_deal(entries)
    for item in opened.get("suppliers") or []:
        if str(item.get("id") or "") != str(supplier_id):
            continue
        found = {
            "id": str(supplier_id),
            "name": str(item.get("name") or ""),
            "address": str(item.get("address") or ""),
        }
    for entry in entries:
        payload = entry.get("payload") or {}
        if str(payload.get("supplier_id") or "") != str(supplier_id):
            continue
        if entry.get("type") == "supplier_added":
            found = {
                "id": str(supplier_id),
                "name": str(payload.get("name") or ""),
                "address": str(payload.get("address") or ""),
            }
        elif entry.get("type") == "supplier_address" and payload.get("confirmed_by") == "human":
            found = {
                "id": str(supplier_id),
                "name": str(payload.get("name") or (found or {}).get("name") or ""),
                "address": str(payload.get("address") or ""),
            }
    return found


def reply_headers(entries: list[dict[str, Any]], supplier_id: str) -> tuple[str, str]:
    """In-Reply-To and References from the chained quote, not the draft row."""
    reply_to = ""
    references = ""
    for entry in entries:
        if entry.get("type") != "quote_ingested":
            continue
        payload = entry.get("payload") or {}
        if str(payload.get("supplier_id") or "") != str(supplier_id):
            continue
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            continue
        reply_to = message_id
        references = join_references(str(payload.get("references") or ""), message_id)
    return reply_to, references


def transmission_digest(
    *,
    recipient: str,
    subject: str,
    in_reply_to: str,
    references: str,
    body: str,
) -> str:
    """One hash of the envelope that will be handed to the mailer."""
    return hashlib.sha256(
        canonical(
            {
                "to": recipient,
                "subject": subject,
                "in_reply_to": in_reply_to,
                "references": references,
                "body": body,
            }
        )
    ).hexdigest()
