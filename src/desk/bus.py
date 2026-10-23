"""In-process snapshot fan-out for the desk event stream.

Why this module exists
----------------------
``DeskBus`` was defined at the bottom of ``service.py`` and used by
``publish()``, which in turn sat in the middle of the same file. Extracting the
send gate (``desk/verify.py``) required ``publish``, which required the bus, so
the bus had to become a module of its own rather than a private detail of a
2,200-line file. It has no dependencies beyond the standard library, which makes
it the natural bottom of the desk dependency graph.

Behaviour is unchanged, including the drop-oldest policy: a subscriber that
cannot keep up loses snapshots rather than blocking the writer, because the
writer is on the request path and a stalled reader must never hold a quote.
"""
from __future__ import annotations

import queue
import threading
from typing import Any


class DeskBus:
    """In-process fan-out. A full queue drops the oldest snapshot."""

    def __init__(self) -> None:
        self._queue = queue
        self._lock = threading.Lock()
        self._subs: dict[str, list] = {}

    def subscribe(self, deal_id: str):
        mailbox_queue: queue.Queue = self._queue.Queue(maxsize=8)
        with self._lock:
            self._subs.setdefault(deal_id, []).append(mailbox_queue)
        return mailbox_queue

    def unsubscribe(self, deal_id: str, mailbox_queue) -> None:
        with self._lock:
            rows = self._subs.get(deal_id, [])
            if mailbox_queue in rows:
                rows.remove(mailbox_queue)

    def publish(self, deal_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            rows = list(self._subs.get(deal_id, []))
        for mailbox_queue in rows:
            if mailbox_queue.full():
                try:
                    mailbox_queue.get_nowait()
                except self._queue.Empty:
                    pass
            try:
                mailbox_queue.put_nowait(payload)
            except self._queue.Full:
                pass

    def subscriber_count(self, deal_id: str) -> int:
        """Observability: how many streams are attached to a deal."""
        with self._lock:
            return len(self._subs.get(deal_id, []))


BUS = DeskBus()
