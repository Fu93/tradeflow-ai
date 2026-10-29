"""Canonical decision order comes from human confirmations on the audit chain."""
from __future__ import annotations

from typing import Any


def confirmation_order(events: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions = {}
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("type") == "review" and payload.get("to") == "accepted":
            positions[str(payload.get("event_id") or "")] = int(entry["seq"])
    # Legacy/unbound rows remain visible to the existing migration and integrity
    # checks. They must not silently disappear from the unconfirmed-input count.
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (
        str(pair[1].get("id") or "") in positions,
        positions.get(str(pair[1].get("id") or ""), pair[0]),
    ))
    return [event for _index, event in indexed]


def decision_assumptions(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay only supported decision inputs, including explicit null resets."""
    allowed = {"reserve_prior_mean", "reserve_prior_std", "hazard_intercept", "hazard_quote_gap"}
    result = {}
    for entry in entries:
        if entry.get("type") == "assumptions":
            fields = (entry.get("payload") or {}).get("fields") or {}
            result.update({key: value for key, value in fields.items() if key in allowed})
    return result
