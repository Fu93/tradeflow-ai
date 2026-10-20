"""Realised landed saving, and the audit of numbers the decision consumed.

Saving on a real closed deal is the best first confirmed landed unit price
minus the agreed landed unit price, on the same basis. A simulated or fixture
deal is labelled and is not counted. The audit count is the number of decision
inputs whose price was not a human confirmation.
"""
from __future__ import annotations

from typing import Any


def audit_line(count: int) -> str:
    return f"unconfirmed numbers used in decisions: {int(count)}"


AUDIT_LINE = audit_line(0)


def human_confirmed(event: dict[str, Any]) -> bool:
    """A decision input is confirmed only when a person accepted that price."""
    if event.get("status") != "accepted":
        return False
    provenance = event.get("provenance") or {}
    if provenance.get("unconfirmed") or provenance.get("price_source") == "raw_text":
        return False
    who = provenance.get("review_accepted_by")
    if who is not None and who != "human":
        return False
    if provenance.get("confirmed") is False:
        return False
    return True


def _priced(event: dict[str, Any]) -> bool:
    parsed = event.get("parsed") or {}
    return parsed.get("unit_price") is not None


def confirmed_prices(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Split a decision-input list into confirmed prices and the unconfirmed count.

    A review row, or an accepted row marked unconfirmed, increments the count.
    Callers pass the numbers the solver actually consumed, not the whole log.
    """
    kept: list[dict[str, Any]] = []
    unconfirmed = 0
    for event in events:
        if not _priced(event):
            continue
        if human_confirmed(event):
            kept.append(event)
        else:
            unconfirmed += 1
    return kept, unconfirmed


def saving_vs_best_first(
    first_landed: list[float],
    agreed_landed: float | None,
    *,
    simulated: bool,
    unconfirmed: int = 0,
) -> dict[str, Any]:
    line = audit_line(unconfirmed)
    if simulated or unconfirmed:
        return {
            "simulated": bool(simulated),
            "counted": False,
            "saving": None,
            "best_first_landed": min(first_landed) if first_landed else None,
            "agreed_landed": agreed_landed,
            "audit": line,
        }
    if not first_landed or agreed_landed is None:
        return {
            "simulated": False,
            "counted": False,
            "saving": None,
            "best_first_landed": min(first_landed) if first_landed else None,
            "agreed_landed": agreed_landed,
            "audit": line,
        }
    best = min(float(item) for item in first_landed)
    agreed = float(agreed_landed)
    return {
        "simulated": False,
        "counted": True,
        "saving": best - agreed,
        "best_first_landed": best,
        "agreed_landed": agreed,
        "audit": line,
    }
