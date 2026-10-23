"""Recompute and publish the snapshot for one deal.

``publish()`` was defined inside ``service.py`` next to the ingest path, the
extractor pool and the mailbox poller. It is a two-line composition of
``recompute`` and the event bus, and the send gate needs it (to refresh the desk
after an anchor failure), so it belongs below both rather than between them.

Extracted 2026-10-23 so that ``desk/verify.py`` can depend on it without
importing the service layer. Behaviour is unchanged.
"""
from __future__ import annotations

from typing import Any

from src.desk import recompute
from src.desk.bus import BUS
from src.desk.store import DeskStore


def publish(store: DeskStore, deal_id: str) -> dict[str, Any]:
    """Recompute the snapshot, store it, and fan it out to open streams."""
    snapshot = recompute.recompute(store, deal_id)
    BUS.publish(deal_id, snapshot)
    return snapshot
