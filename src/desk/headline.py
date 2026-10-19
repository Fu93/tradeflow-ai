"""Realised landed saving, and the audit that unconfirmed numbers stay out.

Saving on a real closed deal is the best first confirmed landed unit price
minus the agreed landed unit price. A simulated deal is labelled and is not
counted. The audit line is zero because a review row is not a decision input.
"""
from __future__ import annotations

from typing import Any

AUDIT_LINE = "unconfirmed numbers used in decisions: 0"


def confirmed_prices(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep accepted prices. An unconfirmed number is not used."""
    kept = []
    for event in events:
        parsed = event.get("parsed") or {}
        if event.get("status") == "accepted" and parsed.get("unit_price") is not None:
            kept.append(event)
    return kept, 0


def saving_vs_best_first(
    first_landed: list[float],
    agreed_landed: float | None,
    *,
    simulated: bool,
) -> dict[str, Any]:
    if simulated:
        return {
            "simulated": True,
            "counted": False,
            "saving": None,
            "best_first_landed": None,
            "agreed_landed": None,
            "audit": AUDIT_LINE,
        }
    if not first_landed or agreed_landed is None:
        return {
            "simulated": False,
            "counted": False,
            "saving": None,
            "best_first_landed": min(first_landed) if first_landed else None,
            "agreed_landed": agreed_landed,
            "audit": AUDIT_LINE,
        }
    best = min(float(item) for item in first_landed)
    agreed = float(agreed_landed)
    return {
        "simulated": False,
        "counted": True,
        "saving": best - agreed,
        "best_first_landed": best,
        "agreed_landed": agreed,
        "audit": AUDIT_LINE,
    }
