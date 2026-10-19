"""Desk actions. Ingest never sends. Sending is a separate, explicit call."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from email.message import EmailMessage
from typing import Any, Awaitable, Callable

from src.config.heuristics import BREAKDOWN_TRIP_THRESHOLD, RISK_TOLERANCE_FLOOR, RISK_TOLERANCE_MAX
from src.desk import mailbox, recompute
from src.desk.parse import parse_quote
from src.desk.store import DeskRejected, DeskStore, get_store

logger = logging.getLogger("tradeflow.desk")

SOURCES = {"email", "chat-import", "paste"}
Sender = Callable[[str, str, str], Awaitable[bool]]


class SendRefused(Exception):
    """A draft was not approved by a person."""


def _address_key(value: str) -> str:
    return (value or "").strip().casefold()


def _match_supplier(suppliers: list[dict[str, Any]], sender: str | None, supplier_id: str | None):
    if supplier_id:
        for row in suppliers:
            if row["id"] == supplier_id:
                return row
        raise DeskRejected("That supplier is not on this deal.")
    key = _address_key(sender or "")
    if not key:
        return None
    for row in suppliers:
        if _address_key(row["address"]) == key:
            return row
    return None


def create_deal(
    store: DeskStore,
    *,
    product: str,
    quantity: int,
    target_price: float,
    ceiling_price: float,
    risk_tolerance: float | None,
    seed: int,
    suppliers: list[dict[str, str]],
) -> dict[str, Any]:
    names = [item["name"].strip() for item in suppliers]
    addresses = [item["address"].strip() for item in suppliers]
    if not 4 <= len(suppliers) <= 5:
        raise DeskRejected("A deal needs four or five suppliers.")
    if any(not name for name in names):
        raise DeskRejected("Each supplier needs a name.")
    if any(not address for address in addresses):
        raise DeskRejected("Each supplier needs an address.")
    folded_names = [name.casefold() for name in names]
    if len(folded_names) != len(set(folded_names)):
        raise DeskRejected("Duplicate supplier name. Each supplier needs its own name.")
    folded_addresses = [address.casefold() for address in addresses]
    if len(folded_addresses) != len(set(folded_addresses)):
        raise DeskRejected("Duplicate supplier address. Each supplier needs its own address.")
    if float(target_price) > float(ceiling_price):
        raise DeskRejected("The target must be at or below the ceiling.")
    risk = float(BREAKDOWN_TRIP_THRESHOLD if risk_tolerance is None else risk_tolerance)
    if risk < float(RISK_TOLERANCE_FLOOR) or risk > float(RISK_TOLERANCE_MAX):
        raise DeskRejected("Risk tolerance is outside the configured range.")
    deal = store.create_deal(
        product=product.strip(),
        quantity=int(quantity),
        target_price=float(target_price),
        ceiling_price=float(ceiling_price),
        risk_tolerance=risk,
        seed=int(seed),
        suppliers=[
            {
                "name": item["name"].strip(),
                "address": item["address"].strip(),
                "channel": (item.get("channel") or "email").strip() or "email",
            }
            for item in suppliers
        ],
    )
    return publish(store, deal["id"])


def ingest_message(
    store: DeskStore,
    deal_id: str,
    *,
    source: str,
    raw_text: str,
    sender: str | None = None,
    supplier_id: str | None = None,
    llm_fields: dict | None = None,
    freight: float | None = None,
    duty_rate: float | None = None,
    fees: float | None = None,
    external_id: str | None = None,
    provenance_extra: dict | None = None,
) -> dict[str, Any]:
    if source not in SOURCES:
        raise DeskRejected("Quote source must be email, chat-import, or paste.")
    deal = store.get_deal(deal_id)
    if deal is None:
        raise KeyError(deal_id)
    if external_id:
        existing = store.find_event(deal_id, external_id)
        if existing is not None:
            snapshot = store.get_snapshot(deal_id)
            return {"duplicate": True, "event": existing, "snapshot": snapshot}
    reading = parse_quote(raw_text, llm_fields=llm_fields)
    parsed = reading["parsed"]
    if reading["status"] == "accepted":
        if freight is not None:
            parsed["freight"] = float(freight)
        if duty_rate is not None:
            parsed["duty_rate"] = float(duty_rate)
        if fees is not None:
            parsed["fees"] = float(fees)
    provenance = dict(reading["provenance"])
    provenance["source"] = source
    if sender:
        provenance["sender"] = sender
    if provenance_extra:
        provenance.update(provenance_extra)
    try:
        matched = _match_supplier(store.suppliers(deal_id), sender, supplier_id)
    except DeskRejected:
        raise
    status = reading["status"]
    confidence = reading["confidence"]
    linked = matched["id"] if matched is not None else None
    if status == "accepted" and linked is None:
        status = "review"
        confidence = "low"
        parsed["unit_price"] = None
        provenance["review_reason"] = "unknown sender"
    elif status != "accepted":
        parsed["unit_price"] = None
        provenance["review_reason"] = "low confidence"
    event = store.insert_event(
        deal_id=deal_id,
        supplier_id=linked,
        source=source,
        raw_text=raw_text,
        parsed=parsed,
        confidence=confidence,
        status=status,
        provenance=provenance,
        external_id=external_id,
    )
    if event is None and external_id:
        existing = store.find_event(deal_id, external_id)
        return {"duplicate": True, "event": existing, "snapshot": store.get_snapshot(deal_id)}
    store.touch(deal_id)
    snapshot = publish(store, deal_id)
    _maybe_gmail_drafts(store, deal_id, snapshot)
    return {"duplicate": False, "event": event, "snapshot": store.get_snapshot(deal_id)}


def accept_review(store: DeskStore, deal_id: str, event_id: str, supplier_id: str) -> dict[str, Any]:
    event = store.event(deal_id, event_id)
    if event is None:
        raise KeyError(event_id)
    supplier = store.supplier(deal_id, supplier_id)
    if supplier is None:
        raise DeskRejected("That supplier is not on this deal.")
    reading = parse_quote(event["raw_text"], llm_fields=None)
    if reading["status"] != "accepted" or reading["parsed"].get("unit_price") is None:
        raise DeskRejected("This quote has no single traceable price, so it stays in review.")
    provenance = dict(event["provenance"])
    provenance["review_accepted_by"] = "human"
    provenance["review_reason"] = None
    store.set_event_status(
        event_id,
        status="accepted",
        supplier_id=supplier_id,
        parsed=reading["parsed"],
        confidence="high",
        provenance=provenance,
    )
    store.touch(deal_id)
    publish(store, deal_id)
    return store.get_snapshot(deal_id) or {}


def publish(store: DeskStore, deal_id: str) -> dict[str, Any]:
    snapshot = recompute.recompute(store, deal_id)
    BUS.publish(deal_id, snapshot)
    return snapshot


def _maybe_gmail_drafts(store: DeskStore, deal_id: str, snapshot: dict[str, Any]) -> None:
    if not mailbox.gmail_drafts_enabled():
        return
    try:
        opener = urllib_opener()
        token = mailbox.refresh_access_token(opener)
    except Exception as exc:  # noqa: BLE001 - a draft failure must not drop the quote
        logger.warning("Gmail draft setup failed: %s", type(exc).__name__)
        return
    product = str((snapshot.get("deal") or {}).get("product") or "")
    for draft in snapshot.get("drafts") or []:
        if draft.get("status") != "draft" or draft.get("gmail_draft_id"):
            continue
        try:
            raw = _draft_rfc822(product, draft["body"])
            gmail_id = mailbox.create_gmail_draft(opener, token, raw)
            store.set_gmail_draft(draft["id"], gmail_id=gmail_id or None, error=None)
        except Exception as exc:  # noqa: BLE001
            store.set_gmail_draft(draft["id"], gmail_id=None, error=type(exc).__name__)


def urllib_opener():
    import urllib.request

    return urllib.request.build_opener()


def _draft_rfc822(product: str, body: str) -> bytes:
    message = EmailMessage()
    message["Subject"] = f"Counter for {product}"
    message.set_content(body)
    return message.as_bytes()


def smtp_enabled() -> bool:
    if os.environ.get("DESK_SMTP_SEND", "").strip() != "1":
        return False
    return all(os.environ.get(key, "").strip() for key in ("SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"))


async def transmit(recipient: str, body: str, product: str) -> bool:
    """Hand an already-approved draft to SMTP. Not used unless smtp_enabled()."""
    from src.transport.email_std import EmailStdTransport

    transport = EmailStdTransport()
    if transport.is_mock_mode:
        return False
    return bool(
        await transport.send_message(
            recipient,
            body,
            subject=f"Counter for {product}",
            metadata={"human_approved": True, "product": product},
        )
    )


async def send_draft(
    store: DeskStore,
    draft_id: str,
    *,
    human_approved: bool,
    sender: Sender | None = None,
) -> dict[str, Any]:
    if human_approved is not True:
        raise SendRefused("A person has to approve this draft before it can be sent.")
    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(draft_id)
    if draft["status"] == "sent":
        return draft
    deal = store.get_deal(draft["deal_id"])
    supplier = store.supplier(draft["deal_id"], draft["supplier_id"])
    if deal is None or supplier is None:
        raise KeyError(draft_id)
    transmitted = False
    if smtp_enabled():
        fn = sender or transmit
        transmitted = bool(await fn(supplier["address"], draft["body"], str(deal["product"])))
    store.mark_sent(draft_id, transmitted=transmitted)
    updated = store.get_draft(draft_id)
    return updated or draft


def _external_id(item: dict[str, Any]) -> str:
    message_id = (item.get("message_id") or "").strip()
    if message_id:
        return message_id
    digest = hashlib.sha256(item.get("raw") or b"").hexdigest()
    return f"sha256:{digest}"


def _route_deals(store: DeskStore, sender: str, deal_id: str | None) -> list[str]:
    deals = [store.get_deal(deal_id)] if deal_id else store.list_deals()
    deals = [deal for deal in deals if deal]
    key = _address_key(sender)
    matched: list[str] = []
    for deal in deals:
        for supplier in store.suppliers(deal["id"]):
            if _address_key(supplier["address"]) == key and key:
                matched.append(deal["id"])
                break
    if matched:
        return matched
    if deal_id and deals:
        return [deal_id]
    if deals:
        return [deals[0]["id"]]
    return []


def ingest_mailbox_items(store: DeskStore, items: list[dict[str, Any]], *, deal_id: str | None, channel: str) -> dict[str, int]:
    counts = {"ingested": 0, "review": 0, "duplicates": 0}
    for item in items:
        targets = _route_deals(store, item.get("sender") or "", deal_id)
        if not targets:
            continue
        extra = {
            "channel": channel,
            "message_id": item.get("message_id"),
            "subject": item.get("subject"),
            "fixture": item.get("fixture"),
        }
        for target in targets:
            result = ingest_message(
                store,
                target,
                source="email",
                raw_text=item.get("body") or "",
                sender=item.get("sender") or "",
                external_id=_external_id(item),
                provenance_extra=extra,
            )
            if result.get("duplicate"):
                counts["duplicates"] += 1
                continue
            counts["ingested"] += 1
            event = result.get("event") or {}
            if event.get("status") == "review":
                counts["review"] += 1
    return counts


def poll_imap_once(
    store: DeskStore | None = None,
    *,
    deal_id: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    use_ssl: bool | None = None,
) -> dict[str, Any]:
    desk = store or get_store()
    if deal_id and desk.get_deal(deal_id) is None:
        raise KeyError(deal_id)
    if host is None:
        settings = mailbox.imap_settings_from_env()
        if settings is None:
            return {"status": "not_configured", "ingested": 0, "review": 0, "duplicates": 0}
    else:
        settings = {
            "host": host,
            "port": int(port or 143),
            "username": username or "",
            "password": password or "",
            "use_ssl": bool(use_ssl),
        }
    if not _route_deals(desk, "", deal_id) and not desk.list_deals() and not deal_id:
        return {"status": "no-deals", "ingested": 0, "review": 0, "duplicates": 0}
    if deal_id is None and not desk.list_deals():
        return {"status": "no-deals", "ingested": 0, "review": 0, "duplicates": 0}
    items = mailbox.pull_unseen(
        host=settings["host"],
        port=int(settings["port"]),
        username=settings["username"],
        password=settings["password"],
        use_ssl=bool(settings["use_ssl"]),
    )
    counts = ingest_mailbox_items(desk, items, deal_id=deal_id, channel="imap")
    counts["status"] = "ok"
    return counts


def poll_gmail_once(store: DeskStore | None = None, *, deal_id: str | None = None, opener=None) -> dict[str, Any]:
    desk = store or get_store()
    if not mailbox.gmail_configured():
        return {"status": "not_configured", "ingested": 0, "review": 0, "duplicates": 0}
    if deal_id is None and not desk.list_deals():
        return {"status": "no-deals", "ingested": 0, "review": 0, "duplicates": 0}
    client = opener or urllib_opener()
    items = mailbox.pull_gmail_unread(client)
    counts = ingest_mailbox_items(desk, items, deal_id=deal_id, channel="gmail")
    counts["status"] = "ok"
    return counts


def poll_configured(store: DeskStore | None = None) -> None:
    desk = store or get_store()
    if mailbox.imap_settings_from_env() is not None and desk.list_deals():
        poll_imap_once(desk)
    if mailbox.gmail_configured() and desk.list_deals():
        poll_gmail_once(desk)


def polling_enabled() -> bool:
    return mailbox.imap_settings_from_env() is not None or mailbox.gmail_configured()


def poll_seconds() -> int:
    raw = os.environ.get("IMAP_POLL_SECONDS", "60").strip() or "60"
    try:
        value = int(raw)
    except ValueError:
        return 60
    if value < 1:
        return 1
    return value


async def poll_forever(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(poll_configured)
        except Exception:  # noqa: BLE001 - a bad poll must not stop the process
            logger.exception("desk mailbox poll failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds())
        except asyncio.TimeoutError:
            continue


class DeskBus:
    """In-process fan-out. A full queue drops the oldest snapshot."""

    def __init__(self) -> None:
        import queue
        import threading

        self._queue = queue
        self._lock = threading.Lock()
        self._subs: dict[str, list] = {}

    def subscribe(self, deal_id: str):
        mailbox_queue = self._queue.Queue(maxsize=8)
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


BUS = DeskBus()
