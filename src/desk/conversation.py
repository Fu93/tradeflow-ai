"""Display-only supplier conversation state. Silence never changes a decision."""
from __future__ import annotations

from typing import Any


def thread_status(
    supplier: dict[str, Any], events: list[dict[str, Any]], drafts: list[dict[str, Any]],
) -> dict[str, Any]:
    supplier_id = supplier["id"]
    linked = [event for event in events if event.get("supplier_id") == supplier_id]
    pending = sum(event["status"] == "review" for event in linked)
    current = next((
        draft for draft in drafts
        if draft["supplier_id"] == supplier_id and draft["status"] != "superseded"
    ), None)
    state, label = "no_quote", "No confirmed quote yet"
    waiting_since = None
    if supplier.get("walked_away"):
        state, label = "paused", "Supplier paused; reopen to continue"
    elif pending:
        state, label = "needs_review", "New quote waiting for your confirmation"
    elif supplier.get("quote_confirmed"):
        if current is None:
            state, label = "withheld", "Counter withheld; check the price and risk limits"
        elif current["status"] == "draft":
            state, label = "needs_approval", "Draft ready for your approval"
        elif current["status"] == "approved":
            state, label = "approved_not_sent", "Approved here; not transmitted"
        elif current["status"] == "sent" and current.get("transmitted"):
            state, label = "waiting_supplier", "Waiting for this supplier's reply"
            waiting_since = current.get("sent_at")
        elif current["status"] == "sending":
            state, label = "delivery_pending", "Delivery not yet confirmed"
        else:
            state, label = "delivery_attention", "Check delivery history before sending again"
    return {
        "state": state,
        "label": label,
        "pending_quotes": pending,
        "last_captured_at": linked[-1].get("received_at") if linked else None,
        "waiting_since": waiting_since,
    }
