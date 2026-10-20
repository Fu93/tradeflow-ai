"""Desk actions. Ingest never sends. Sending is a separate, explicit call."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import queue
import threading
import time
from email.message import EmailMessage
from typing import Any, Awaitable, Callable

from src.config.heuristics import (
    BREAKDOWN_TRIP_THRESHOLD,
    DEAL_CURRENCIES,
    DEAL_CURRENCY,
    EXTRACTOR_MAX_IN_FLIGHT,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
    OPT_TOL,
    UNIT_SANITY_MULTIPLE,
)
from src.desk import mailbox, recompute
from src.desk.audit_chain import approval_recorded, chain_key_configured, export_history, reanchor_refusal
from src.desk.extract import time_left
from src.desk.input_checks import message_id_or_blank, references_or_blank, require_address, require_plain
from src.desk.names import supplier_name_key
from src.desk.parse import (
    _confirm_span,
    _visible_text,
    confirm_amount,
    conversion_pieces,
    parse_quote,
    price_from_token,
)
from src.desk.store import DeskRejected, DeskStore, DeskUnavailable, get_store
from src.desk.transmission import (
    chained_supplier,
    counter_subject,
    reply_headers,
    transmission_digest,
)

logger = logging.getLogger("tradeflow.desk")

SOURCES = {"email", "chat-import", "paste"}
OUTCOMES = {"continue", "walk_away", "agreed"}
EVENT_TRANSITIONS = {
    "review": {"accepted", "rejected"},
    "accepted": set(),
    "rejected": set(),
}


def classify_origin(raw_text: str, extra: dict | None) -> dict[str, Any]:
    """Fixture and simulated are decided here.

    A client ``simulated: true`` is a downgrade and is kept. The page URL
    or title can still upgrade a live flag to simulated. A client true is
    never stored as live.
    """
    fields = dict(extra or {})
    client_simulated = bool(fields.pop("simulated", False))
    header = str(fields.get("fixture") or "").strip()
    text = raw_text or ""
    fixture = bool(header) or ("X-TradeFlow-Fixture" in text)
    page_url = str(fields.get("page_url") or "")
    page_title = str(fields.get("page_title") or "")
    simulated = client_simulated or ("mock_chat.html" in page_url.lower()) or ("simulated" in page_title.lower())
    fields["fixture"] = header or ("present" if fixture else None)
    fields["simulated"] = simulated
    if fixture:
        fields["origin"] = "fixture"
    elif simulated:
        fields["origin"] = "simulated"
    else:
        fields["origin"] = "live"
    return fields
Sender = Callable[..., Awaitable[bool]]


class SendRefused(Exception):
    """A draft was not approved by a person."""


class DraftStale(Exception):
    """The approved text is no longer the draft on the desk."""


_DEAL_LOCKS: dict[str, threading.Lock] = {}
_DEAL_LOCKS_GUARD = threading.Lock()
class _ExtractorJob:
    """One queued extraction. ``join`` waits for the pool worker to finish it."""

    def __init__(self) -> None:
        self._done = threading.Event()

    def join(self, timeout: float | None = None) -> None:
        self._done.wait(timeout)

    def finish(self) -> None:
        self._done.set()


_EXTRACTOR_THREADS: dict[str, _ExtractorJob] = {}
_EXTRACTOR_THREADS_GUARD = threading.Lock()
_EXTRACTOR_POOL: concurrent.futures.ThreadPoolExecutor | None = None
_EXTRACTOR_POOL_GUARD = threading.Lock()
_IMPORT_GUARD = threading.Lock()
_IMPORT_STATUS = {"queued": 0, "running": 0, "finished": 0}


def deal_lock(deal_id: str) -> threading.Lock:
    with _DEAL_LOCKS_GUARD:
        lock = _DEAL_LOCKS.get(deal_id)
        if lock is None:
            lock = threading.Lock()
            _DEAL_LOCKS[deal_id] = lock
        return lock


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
    currency: str | None = None,
) -> dict[str, Any]:
    product = require_plain(product, "Product")
    names = [item["name"].strip() for item in suppliers]
    addresses = [require_address(item["address"]) for item in suppliers]
    if not 4 <= len(suppliers) <= 5:
        raise DeskRejected("A deal needs four or five suppliers.")
    if any(not name for name in names):
        raise DeskRejected("Each supplier needs a name.")
    if any(not address for address in addresses):
        raise DeskRejected("Each supplier needs an address.")
    folded_names = [supplier_name_key(name) for name in names]
    if len(folded_names) != len(set(folded_names)):
        raise DeskRejected("Duplicate supplier name. Each supplier needs its own name.")
    folded_addresses = [address.casefold() for address in addresses]
    if len(folded_addresses) != len(set(folded_addresses)):
        raise DeskRejected("Duplicate supplier address. Each supplier needs its own address.")
    if float(target_price) > float(ceiling_price):
        raise DeskRejected("The target must be at or below the ceiling.")
    code = currency or DEAL_CURRENCY
    if code not in DEAL_CURRENCIES:
        raise DeskRejected("Deal currency is not one of the registered currencies.")
    risk = float(BREAKDOWN_TRIP_THRESHOLD if risk_tolerance is None else risk_tolerance)
    if risk < float(RISK_TOLERANCE_FLOOR) or risk > float(RISK_TOLERANCE_MAX):
        raise DeskRejected("Risk tolerance is outside the configured range.")
    if chain_key_configured() is False:
        raise DeskUnavailable("desk sends disabled: chain key missing")
    try:
        deal = _create_deal_row(store, product, quantity, target_price, ceiling_price, risk, seed, code, suppliers)
        store.commit()
    except Exception:
        store.rollback()
        raise
    return publish(store, deal["id"])


def _create_deal_row(
    store: DeskStore,
    product: str,
    quantity: int,
    target_price: float,
    ceiling_price: float,
    risk: float,
    seed: int,
    code: str,
    suppliers: list[dict[str, str]],
) -> dict[str, Any]:
    deal = store.create_deal(
        product=product.strip(),
        quantity=int(quantity),
        target_price=float(target_price),
        ceiling_price=float(ceiling_price),
        risk_tolerance=risk,
        seed=int(seed),
        currency=code,
        commit=False,
        suppliers=[
            {
                "name": item["name"].strip(),
                "address": item["address"].strip(),
                "channel": (item.get("channel") or "email").strip() or "email",
            }
            for item in suppliers
        ],
    )
    identities = [
        {"id": row["id"], "name": row["name"], "address": row["address"]}
        for row in store.suppliers(deal["id"])
    ]
    store.append_chain(
        deal["id"],
        "deal_opened",
        {
            "currency": code,
            "ceiling": float(ceiling_price),
            "target_price": float(target_price),
            "quantity": int(quantity),
            "product": product.strip(),
            "risk_tolerance": risk,
            "suppliers": identities,
        },
        commit=False,
    )
    for row in identities:
        store.append_chain(
            deal["id"],
            "supplier_added",
            {
                "supplier_id": row["id"],
                "name": row["name"],
                "address": row["address"],
            },
            commit=False,
        )
    return deal


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
    prepared = _prepare_reading(
        raw_text,
        deal,
        llm_fields=llm_fields,
    )
    with deal_lock(deal_id):
        stored = _ingest_locked(
            store,
            deal_id,
            source=source,
            raw_text=raw_text,
            sender=sender,
            supplier_id=supplier_id,
            llm_fields=llm_fields,
            freight=freight,
            duty_rate=duty_rate,
            fees=fees,
            external_id=external_id,
            provenance_extra=provenance_extra,
            prepared=prepared,
        )
    _schedule_extractor(store, deal_id, raw_text, prepared, stored)
    return stored


def _outside_sanity(price: float, deal: dict[str, Any]) -> bool:
    """Auto-accept only inside a labelled multiple of the target and ceiling."""
    raw = deal.get("unit_sanity_multiple")
    multiple = float(UNIT_SANITY_MULTIPLE if raw is None else raw)
    if multiple <= 0:
        return False
    target = float(deal["target_price"])
    ceiling = float(deal["ceiling_price"])
    high = max(target, ceiling) * multiple
    low = target / multiple
    return float(price) > high or float(price) < low


def _prepare_reading(raw_text: str, deal: dict[str, Any], *, llm_fields: dict | None) -> dict[str, Any]:
    """Parse and call the extractor before the deal lock. A failure still returns a review shell."""
    from src.desk.shadow import shadow_from_message

    try:
        reading = parse_quote(
            raw_text,
            llm_fields=llm_fields,
            quantity=deal.get("quantity"),
            deal_currency=str(deal.get("currency") or DEAL_CURRENCY),
        )
    except Exception:
        reading = {
            "parsed": {},
            "provenance": {"review_reason": "the reader failed closed"},
            "status": "review",
        }
    target = float(deal["target_price"])
    ceiling = float(deal["ceiling_price"])
    multiple = float(UNIT_SANITY_MULTIPLE if deal.get("unit_sanity_multiple") is None else deal["unit_sanity_multiple"])
    high = max(target, ceiling) * multiple
    low = target / multiple if multiple else target
    from src.desk.extract import configured as extractor_configured

    try:
        shadow: dict[str, Any]
        if extractor_configured():
            shadow = {
                "would_accept": False,
                "applied": False,
                "span_value": None,
                "rule_value": None,
                "numeric_field_discarded": False,
                "auto_accept": False,
                "extractor_pending": True,
                "mentions": [],
                "rejected_rows": 0,
                "model": "",
                "prompt_sha256": "",
                "response_sha256": "",
                "attempts": [],
            }
        else:
            shadow = shadow_from_message(
                raw_text,
                reading,
                low=low,
                high=high,
                currency=str(deal.get("currency") or DEAL_CURRENCY),
            )
    except Exception:
        shadow = {
            "would_accept": False,
            "applied": False,
            "span_value": None,
            "rule_value": None,
            "numeric_field_discarded": False,
            "error": "extractor_failed",
        }
    reading = dict(reading)
    provenance = dict(reading.get("provenance") or {})
    candidates = [
        str(token) for token in (provenance.get("candidates") or [])
        if not _quantity_token(raw_text, str(token))
    ]
    suggested = provenance.get("suggested_token")
    if suggested and (
        _quantity_token(raw_text, str(suggested)) or str(suggested) not in candidates
    ):
        suggested = None
    provenance["candidates"] = candidates
    provenance["suggested_token"] = suggested
    reading["provenance"] = provenance
    return {"reading": reading, "shadow": shadow, "low": low, "high": high}


def _quantity_token(raw_text: str, token: str) -> bool:
    """A MOQ or a quantity unit is not a unit price, even when it sits near one."""
    if not token or token not in (raw_text or ""):
        return False
    import re

    for match in re.finditer(re.escape(token), raw_text):
        head = raw_text[max(0, match.start() - 12):match.start()]
        tail = raw_text[match.end():match.end() + 8]
        if re.search(r"(?i)(?:moq|起订量|起訂量|起订|起訂|最少|数量|數量)\s*$", head):
            return True
        if re.match(
            r"(?i)\s*(?:个|個|件|只|隻|条|條|双|雙|套|台|臺|箱|斤|公斤|千克|米|包|盒|打|立方|pcs|pc|pieces|k)"
            r"(?:\b|(?=[\u4e00-\u9fff])|$)",
            tail,
        ):
            return True
    return False


def _ingest_locked(
    store: DeskStore,
    deal_id: str,
    *,
    source: str,
    raw_text: str,
    sender: str | None,
    supplier_id: str | None,
    llm_fields: dict | None,
    freight: float | None,
    duty_rate: float | None,
    fees: float | None,
    external_id: str | None,
    provenance_extra: dict | None,
    prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deal = store.get_deal(deal_id)
    if deal is None:
        raise KeyError(deal_id)
    if external_id:
        existing = store.find_event(deal_id, external_id)
        if existing is not None:
            snapshot = store.get_snapshot(deal_id)
            return {"duplicate": True, "event": existing, "snapshot": snapshot}
    if prepared is None:
        prepared = _prepare_reading(raw_text, deal, llm_fields=llm_fields)
    reading = prepared["reading"]
    shadow = prepared["shadow"]
    parsed = dict(reading.get("parsed") or {})
    # Phase 1: the agreement gate only logs. The user confirms every number.
    parsed["unit_price"] = None
    provenance = dict(reading["provenance"])
    provenance["source"] = source
    provenance["reader"] = "prefill"
    provenance["rule_would_accept"] = reading["status"] == "accepted"
    provenance["shadow_would_accept"] = bool(shadow["would_accept"])
    provenance["shadow_applied"] = False
    provenance["span_value"] = shadow.get("span_value")
    provenance["rule_value"] = shadow.get("rule_value")
    provenance["auto_accept"] = False
    provenance["llm_price_ignored"] = True
    if shadow.get("response_sha256"):
        provenance["response_sha256"] = shadow["response_sha256"]
    if shadow.get("error"):
        provenance["extractor_error"] = shadow["error"]
    if shadow.get("attempts"):
        provenance["extractor_attempts"] = list(shadow["attempts"])
    if shadow.get("extractor_pending"):
        provenance["extractor_pending"] = True
    if freight is not None:
        parsed["freight"] = float(freight)
    if duty_rate is not None:
        parsed["duty_rate"] = float(duty_rate)
    if fees is not None:
        parsed["fees"] = float(fees)
    if sender:
        provenance["sender"] = sender
    provenance.update(classify_origin(raw_text, provenance_extra))
    simulated = bool(provenance.get("simulated"))
    fixture = provenance.get("origin") == "fixture"
    try:
        matched = _match_supplier(store.suppliers(deal_id), sender, supplier_id)
    except DeskRejected:
        raise
    status = "review"
    confidence = "low"
    linked = matched["id"] if matched is not None else None
    if simulated or fixture:
        provenance["review_reason"] = "fixture" if fixture else "simulated test fixture"
    elif shadow["would_accept"]:
        provenance["review_reason"] = "shadow gate would accept; confirm the prefilled amount"
    else:
        provenance["review_reason"] = provenance.get("review_reason") or "confirm the prefilled amount"
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
    if event is not None:
        store.append_chain(
            deal_id,
            "quote_ingested",
            {
                "event_id": event["id"],
                "raw_sha256": event.get("raw_sha256"),
                "source": source,
                "supplier_id": event.get("supplier_id"),
                "message_id": message_id_or_blank(provenance.get("message_id")),
                "references": references_or_blank(provenance.get("references")),
                "unit_basis": list(provenance.get("unit_basis") or []),
                "currency_evidence": str(provenance.get("currency_evidence") or ""),
                "review_reason": str(provenance.get("review_reason") or ""),
            },
        )
        store.append_chain(
            deal_id,
            "extractor_result",
            {
                "model": shadow.get("model") or "",
                "prompt_sha256": shadow.get("prompt_sha256") or "",
                "response_sha256": shadow.get("response_sha256") or "",
                "rejected_rows": int(shadow.get("rejected_rows") or 0),
                "numeric_field_discarded": bool(shadow.get("numeric_field_discarded")),
                "attempts": list(shadow.get("attempts") or []),
                "pending": bool(shadow.get("extractor_pending")),
            },
        )
    snapshot = publish(store, deal_id)
    _maybe_gmail_drafts(store, deal_id, snapshot)
    return {"duplicate": False, "event": event, "snapshot": store.get_snapshot(deal_id)}


def import_status() -> dict[str, Any]:
    """Queue depth for a burst import. The per-message budget starts when the call starts."""
    with _IMPORT_GUARD:
        snap = {key: int(value) for key, value in _IMPORT_STATUS.items()}
    snap["concurrency"] = int(EXTRACTOR_MAX_IN_FLIGHT)
    return snap


def reset_import_status() -> None:
    with _IMPORT_GUARD:
        _IMPORT_STATUS["queued"] = 0
        _IMPORT_STATUS["running"] = 0
        _IMPORT_STATUS["finished"] = 0


def _import_move(field: str, delta: int) -> None:
    with _IMPORT_GUARD:
        _IMPORT_STATUS[field] = int(_IMPORT_STATUS.get(field) or 0) + delta


def _extractor_pool() -> concurrent.futures.ThreadPoolExecutor:
    global _EXTRACTOR_POOL
    with _EXTRACTOR_POOL_GUARD:
        if _EXTRACTOR_POOL is None:
            _EXTRACTOR_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=int(EXTRACTOR_MAX_IN_FLIGHT),
                thread_name_prefix="desk-extractor-pool",
            )
        return _EXTRACTOR_POOL


def settle_extractor(event_id: str) -> None:
    """Wait for an in-flight extractor. Confirm does not call this."""
    from src.desk.extract import EXTRACTOR_TIMEOUT_BUDGET_SECONDS

    with _EXTRACTOR_THREADS_GUARD:
        job = _EXTRACTOR_THREADS.get(event_id)
    if job is None:
        return
    job.join(timeout=float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS))
    with _EXTRACTOR_THREADS_GUARD:
        if _EXTRACTOR_THREADS.get(event_id) is job:
            _EXTRACTOR_THREADS.pop(event_id, None)


def _failed_shadow() -> dict[str, Any]:
    return {
        "would_accept": False,
        "applied": False,
        "auto_accept": False,
        "error": "extractor_failed",
        "attempts": [],
        "response_sha256": "",
        "model": "",
        "prompt_sha256": "",
        "rejected_rows": 0,
        "numeric_field_discarded": False,
        "span_value": None,
        "rule_value": None,
    }


def _apply_extractor_result(
    store: DeskStore,
    deal_id: str,
    event_id: str,
    shadow: dict[str, Any],
) -> None:
    """Merge a finished extractor call. A confirmed price is left as it is."""
    event = store.event(deal_id, event_id)
    if event is None:
        return
    provenance = dict(event.get("provenance") or {})
    if not provenance.get("extractor_pending"):
        return
    if event.get("status") != "review":
        provenance["extractor_pending"] = False
        if shadow.get("response_sha256"):
            provenance["response_sha256"] = shadow["response_sha256"]
        if shadow.get("error"):
            provenance["extractor_error"] = shadow["error"]
        provenance["extractor_attempts"] = list(shadow.get("attempts") or [])
        store.set_event_status(
            event_id,
            status=str(event.get("status") or "review"),
            supplier_id=event.get("supplier_id"),
            parsed=dict(event.get("parsed") or {}),
            confidence=str(event.get("confidence") or "low"),
            provenance=provenance,
        )
        store.append_chain(
            deal_id,
            "extractor_result",
            {
                "model": shadow.get("model") or "",
                "prompt_sha256": shadow.get("prompt_sha256") or "",
                "response_sha256": shadow.get("response_sha256") or "",
                "rejected_rows": int(shadow.get("rejected_rows") or 0),
                "numeric_field_discarded": bool(shadow.get("numeric_field_discarded")),
                "attempts": list(shadow.get("attempts") or []),
                "pending": False,
            },
        )
        publish(store, deal_id)
        return
    provenance["extractor_pending"] = False
    provenance["shadow_would_accept"] = bool(shadow.get("would_accept"))
    provenance["shadow_applied"] = False
    provenance["auto_accept"] = False
    provenance["span_value"] = shadow.get("span_value")
    provenance["rule_value"] = shadow.get("rule_value")
    provenance["numeric_field_discarded"] = bool(shadow.get("numeric_field_discarded"))
    if shadow.get("response_sha256"):
        provenance["response_sha256"] = shadow["response_sha256"]
    if shadow.get("error"):
        provenance["extractor_error"] = shadow["error"]
    provenance["extractor_attempts"] = list(shadow.get("attempts") or [])
    parsed = dict(event.get("parsed") or {})
    if event.get("status") == "review":
        parsed["unit_price"] = None
    store.set_event_status(
        event_id,
        status=str(event.get("status") or "review"),
        supplier_id=event.get("supplier_id"),
        parsed=parsed,
        confidence=str(event.get("confidence") or "low"),
        provenance=provenance,
    )
    store.append_chain(
        deal_id,
        "extractor_result",
        {
            "model": shadow.get("model") or "",
            "prompt_sha256": shadow.get("prompt_sha256") or "",
            "response_sha256": shadow.get("response_sha256") or "",
            "rejected_rows": int(shadow.get("rejected_rows") or 0),
            "numeric_field_discarded": bool(shadow.get("numeric_field_discarded")),
            "attempts": list(shadow.get("attempts") or []),
            "pending": False,
        },
    )
    publish(store, deal_id)


def _schedule_extractor(
    store: DeskStore,
    deal_id: str,
    raw_text: str,
    prepared: dict[str, Any],
    stored: dict[str, Any],
) -> None:
    shadow = prepared.get("shadow") or {}
    if not shadow.get("extractor_pending") or stored.get("duplicate"):
        return
    event = stored.get("event") or {}
    event_id = str(event.get("id") or "")
    if not event_id:
        return
    deal = store.get_deal(deal_id) or {}
    currency = str(deal.get("currency") or DEAL_CURRENCY)
    reading = prepared.get("reading") or {}
    low = float(prepared.get("low") or 0.0)
    high = float(prepared.get("high") or 0.0)
    from src.desk.extract import EXTRACTOR_TIMEOUT_BUDGET_SECONDS, _hop_timeout

    budget = _hop_timeout(
        "TRADEFLOW_EXTRACTOR_TIMEOUT_BUDGET", float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS)
    )
    job = _ExtractorJob()
    _import_move("queued", 1)

    def _run() -> None:
        _import_move("queued", -1)
        _import_move("running", 1)
        try:
            from src.desk.shadow import shadow_from_message

            # Queue wait is not part of this message's budget. The clock starts here.
            remaining = time_left(budget, time.monotonic())
            if remaining <= 0:
                finished = _failed_shadow()
                finished["error"] = "extractor_unreachable"
            else:
                finished = shadow_from_message(
                    raw_text,
                    reading,
                    low=low,
                    high=high,
                    currency=currency,
                    budget_seconds=remaining,
                )
        except Exception as exc:
            logger.warning("desk extractor failed: %s", type(exc).__name__)
            finished = _failed_shadow()
        try:
            with deal_lock(deal_id):
                _apply_extractor_result(store, deal_id, event_id, finished)
        except Exception as exc:
            logger.warning("desk extractor result was not stored: %s", type(exc).__name__)
        finally:
            _import_move("running", -1)
            _import_move("finished", 1)
            job.finish()
            with _EXTRACTOR_THREADS_GUARD:
                if _EXTRACTOR_THREADS.get(event_id) is job:
                    _EXTRACTOR_THREADS.pop(event_id, None)

    with _EXTRACTOR_THREADS_GUARD:
        _EXTRACTOR_THREADS[event_id] = job
    _extractor_pool().submit(_run)


def _piece_confirmation(
    amount: float,
    basis: str,
    unit_basis: str | None,
    pieces_per_basis: float | None,
    *,
    human_confirmed: bool,
    raw_text: str,
) -> tuple[float, str, float | None] | None:
    """Deal-unit price, the confirmed basis, and the pack count the message allows.

    A stated piece basis needs no extra choice. An unknown basis becomes the
    deal unit only when the human confirm proof is true. A stated non-piece
    basis converts only at the pack count the message states, or at the
    registered dozen, and only with that same proof.
    """
    choice = str(unit_basis or "").strip()
    if basis == "per_piece":
        if pieces_per_basis is not None or choice not in {"", "per_piece"}:
            return None
        return float(amount), "per_piece", None
    if basis in {None, "", "unknown"}:
        if pieces_per_basis is not None or choice not in {"", "per_piece"}:
            return None
        if human_confirmed is not True:
            return None
        return float(amount), "per_piece", None
    if choice == "per_piece" or (choice and choice != basis):
        return None
    allowed = conversion_pieces(raw_text, basis)
    if (
        pieces_per_basis is None
        or allowed is None
        or not math.isclose(float(pieces_per_basis), float(allowed))
    ):
        return None
    if human_confirmed is not True:
        return None
    return float(amount) / float(allowed), "per_piece", float(allowed)


def accept_review(
    store: DeskStore,
    deal_id: str,
    event_id: str,
    supplier_id: str | None,
    *,
    chosen_token: str | None = None,
    reject: bool = False,
    unit_basis: str | None = None,
    pieces_per_basis: float | None = None,
    human_confirmed: bool = False,
) -> dict[str, Any]:
    with deal_lock(deal_id):
        event = store.event(deal_id, event_id)
        if event is None:
            raise KeyError(event_id)
        target = "rejected" if reject else "accepted"
        if target not in EVENT_TRANSITIONS.get(event["status"], set()):
            raise DeskRejected("An accepted quote cannot be accepted again.")
        if reject:
            provenance = dict(event["provenance"])
            provenance["review_rejected_by"] = "human"
            provenance["shadow_resolved"] = True
            provenance["shadow_matched"] = False
            parsed = dict(event["parsed"])
            parsed["unit_price"] = None
            store.set_event_status(
                event_id,
                status="rejected",
                supplier_id=event.get("supplier_id"),
                parsed=parsed,
                confidence="low",
                provenance=provenance,
            )
            store.touch(deal_id)
            store.append_chain(
                deal_id,
                "review",
                {"actor": "human", "from": event["status"], "to": "rejected", "event_id": event_id, "token": None},
            )
            publish(store, deal_id)
            return store.get_snapshot(deal_id) or {}
        if not supplier_id:
            raise DeskRejected("Choose the supplier this quote belongs to.")
        supplier = store.supplier(deal_id, supplier_id)
        if supplier is None:
            raise DeskRejected("That supplier is not on this deal.")
        deal = store.get_deal(deal_id)
        if deal is None:
            raise KeyError(deal_id)
        currency = str(deal.get("currency") or DEAL_CURRENCY)
        reading = parse_quote(
            event["raw_text"],
            llm_fields=None,
            quantity=deal.get("quantity"),
            deal_currency=currency,
        )
        previous = dict(event["parsed"])
        parsed = dict(reading["parsed"])
        for key in ("freight", "duty_rate", "fees"):
            if previous.get(key) is not None:
                parsed[key] = previous[key]
        if not chosen_token:
            raise DeskRejected("Accept confirms only the amount shown from the message.")
        if _quantity_token(event["raw_text"], chosen_token):
            raise DeskRejected("That amount is a quantity, not a unit price.")
        from src.desk.parse import confirm_amount, token_issue

        issue = token_issue(event["raw_text"], chosen_token, deal_currency=currency)
        if issue and issue != "unit mismatch":
            raise DeskRejected(issue)
        found = confirm_amount(event["raw_text"], chosen_token, deal_currency=currency)
        if found is None:
            raise DeskRejected("That amount is not a unit price in the deal currency.")
        amount, basis = found
        if issue is None and basis != "per_piece":
            issue = "unit mismatch"
        resolved = _piece_confirmation(
            amount,
            basis,
            unit_basis,
            pieces_per_basis,
            human_confirmed=human_confirmed,
            raw_text=str(event.get("raw_text") or ""),
        )
        if resolved is None:
            raise DeskRejected("unit mismatch")
        price, confirmed_basis, pieces = resolved
        if issue is None:
            checked = price_from_token(event["raw_text"], chosen_token, deal_currency=currency)
            if checked is None:
                raise DeskRejected("That amount is not a unit price in the deal currency.")
            price = checked
        parsed["unit_price"] = price
        parsed["currency"] = currency
        if price is None or _outside_sanity(float(price), deal):
            raise DeskRejected("That amount is outside the plausible unit-price band. It was not accepted.")
        provenance = dict(event["provenance"])
        provenance["review_accepted_by"] = "human"
        provenance["review_reason"] = None
        provenance["shadow_resolved"] = True
        rule_shadow = provenance.get("span_value")
        if chosen_token:
            provenance["chosen_token"] = chosen_token
        provenance["stated_basis"] = basis
        provenance["confirmed_basis"] = confirmed_basis
        provenance["pieces_per_basis"] = pieces
        provenance["shadow_matched"] = bool(
            provenance.get("shadow_would_accept")
            and rule_shadow is not None
            and price is not None
            and float(rule_shadow) == float(price)
        )
        store.set_event_status(
            event_id,
            status="accepted",
            supplier_id=supplier_id,
            parsed=parsed,
            confidence="high",
            provenance=provenance,
        )
        store.touch(deal_id)
        raw_text = str(event.get("raw_text") or "")
        store.append_chain(
            deal_id,
            "review",
            {
                "actor": "human",
                "from": event["status"],
                "to": "accepted",
                "event_id": event_id,
                "supplier_id": supplier_id,
                "token": chosen_token,
                "value": float(price),
                "currency": currency,
                "unit": "per_piece",
                "stated_basis": basis,
                "confirmed_basis": confirmed_basis,
                "pieces_per_basis": pieces,
                "human_confirmed": human_confirmed is True,
                "span": _confirm_span(_visible_text(raw_text), str(chosen_token)),
                "raw_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "freight": parsed.get("freight"),
                "duty_rate": parsed.get("duty_rate"),
                "fees": parsed.get("fees"),
                "origin": str(provenance.get("origin") or ""),
                "simulated": bool(provenance.get("simulated")),
            },
        )
        publish(store, deal_id)
        return store.get_snapshot(deal_id) or {}


def record_outcome(
    store: DeskStore,
    deal_id: str,
    supplier_id: str,
    *,
    outcome: str,
    quote: float | None = None,
    offer: float | None = None,
    agreed_price: float | None = None,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise DeskRejected("Outcome must be continue, walk_away, or agreed.")
    with deal_lock(deal_id):
        deal = store.get_deal(deal_id)
        if deal is None:
            raise KeyError(deal_id)
        supplier = store.supplier(deal_id, supplier_id)
        if supplier is None:
            raise DeskRejected("That supplier is not on this deal.")
        if outcome == "agreed" and agreed_price is None:
            raise DeskRejected("An agreed outcome needs the agreed price.")
        snapshot = store.get_snapshot(deal_id) or {}
        card = next(
            (row for row in (snapshot.get("suppliers") or []) if row.get("id") == supplier_id),
            None,
        )
        if quote is None and card is not None:
            quote = card.get("latest_quote")
        if offer is None and card is not None:
            offer = card.get("counter") if card.get("counter") is not None else card.get("latest_quote")
        if quote is None or offer is None:
            raise DeskRejected("Record the quote and the offer this outcome answers.")
        store.insert_outcome(
            deal_id=deal_id,
            supplier_id=supplier_id,
            outcome=outcome,
            quote=float(quote),
            offer=float(offer),
            agreed_price=None if agreed_price is None else float(agreed_price),
        )
        store.append_chain(
            deal_id,
            "outcome",
            {
                "supplier_id": supplier_id,
                "outcome": outcome,
                "quote": float(quote),
                "offer": float(offer),
                "agreed_price": None if agreed_price is None else float(agreed_price),
            },
        )
        store.touch(deal_id)
        return publish(store, deal_id)


def confirm_policy(
    store: DeskStore,
    deal_id: str,
    *,
    human_confirmed: bool,
    product: str,
    quantity: int,
    target_price: float,
    ceiling_price: float,
    risk_tolerance: float,
    currency: str,
) -> dict[str, Any]:
    """The limits a migrated deal may send against. The database row is not enough."""
    if human_confirmed is not True:
        raise DeskRejected("A person has to confirm these policy limits.")
    product = require_plain(str(product or ""), "Product")
    if not product.strip():
        raise DeskRejected("Each deal needs a product.")
    code = str(currency or "")
    if code not in DEAL_CURRENCIES:
        raise DeskRejected("Deal currency is not one of the registered currencies.")
    if int(quantity) < 1:
        raise DeskRejected("Quantity must be at least one.")
    if float(target_price) > float(ceiling_price):
        raise DeskRejected("The target must be at or below the ceiling.")
    risk = float(risk_tolerance)
    if risk < float(RISK_TOLERANCE_FLOOR) or risk > float(RISK_TOLERANCE_MAX):
        raise DeskRejected("Risk tolerance is outside the configured range.")
    with deal_lock(deal_id):
        deal = store.get_deal(deal_id)
        if deal is None:
            raise KeyError(deal_id)
        opened = _deal_opened(list(store.verify_links(deal_id).get("entries") or []))
        if not opened.get("migrated"):
            raise DeskRejected("Policy confirmation is for a migrated deal.")
        previous = {
            "product": str(deal.get("product") or ""),
            "quantity": int(deal.get("quantity") or 0),
            "target_price": float(deal.get("target_price") or 0.0),
            "ceiling": float(deal.get("ceiling_price") or 0.0),
            "risk_tolerance": float(deal.get("risk_tolerance") or 0.0),
            "currency": str(deal.get("currency") or ""),
        }
        store.update_limits(
            deal_id,
            product=product.strip(),
            quantity=int(quantity),
            target_price=float(target_price),
            ceiling_price=float(ceiling_price),
            risk_tolerance=risk,
            currency=code,
        )
        store.append_chain(
            deal_id,
            "policy_confirmed",
            {
                "confirmed_by": "human",
                "product": product.strip(),
                "quantity": int(quantity),
                "target_price": float(target_price),
                "ceiling": float(ceiling_price),
                "risk_tolerance": risk,
                "currency": code,
                "previous": previous,
            },
        )
        return publish(store, deal_id)


def migrate_deal(store: DeskStore, deal_id: str, *, human_confirmed: bool) -> dict[str, Any]:
    """Explicit migration. A process restart does not call this."""
    if human_confirmed is not True:
        raise DeskRejected("A person has to migrate this deal.")
    with deal_lock(deal_id):
        store.migrate_deal(deal_id)
        return publish(store, deal_id)


def reanchor_deal(store: DeskStore, deal_id: str, *, human_confirmed: bool) -> dict[str, Any]:
    """Write the current chain head to the anchor after the links themselves verify."""
    if human_confirmed is not True:
        raise DeskRejected("A person has to re-anchor this deal.")
    if chain_key_configured() is False:
        raise DeskRejected("Re-anchor requires TRADEFLOW_CHAIN_KEY.")
    with deal_lock(deal_id):
        if store.get_deal(deal_id) is None:
            raise KeyError(deal_id)
        links = store.verify_links(deal_id)
        if not links["ok"]:
            raise DeskRejected("The audit chain does not verify. Re-anchor will not sign a broken chain.")
        refusal = reanchor_refusal(deal_id, list(links.get("entries") or []))
        if refusal:
            raise DeskRejected(refusal)
        written = store.append_chain(
            deal_id,
            "reanchor",
            {"confirmed_by": "human"},
        )
        if written.get("anchor_ok") is False:
            raise DeskRejected("The anchor did not record this re-anchor.")
        return publish(store, deal_id)


def recover_anchor_history(store: DeskStore, deal_id: str, *, human_confirmed: bool) -> dict[str, Any]:
    """Start a new signed export chain. This is the only repair for a missing or rewritten one.

    A chain that still verifies is left as it is, so recovery cannot drop an approval
    the anchor can still prove.
    """
    if human_confirmed is not True:
        raise DeskRejected("A person has to recover the anchor history.")
    if chain_key_configured() is False:
        raise DeskRejected("Anchor recovery requires TRADEFLOW_CHAIN_KEY.")
    with deal_lock(deal_id):
        if store.get_deal(deal_id) is None:
            raise KeyError(deal_id)
        links = store.verify_links(deal_id)
        if not links["ok"]:
            raise DeskRejected("The audit chain does not verify. Recovery will not sign a broken chain.")
        history = export_history(deal_id)
        if history["status"] in {"unset", "ok"}:
            raise DeskRejected("The anchor history still verifies.")
        if history["status"] == "down":
            raise DeskRejected("The anchor store was down.")
        written = store.append_chain(
            deal_id,
            "export_recovery",
            {"confirmed_by": "human"},
        )
        if written.get("anchor_ok") is False:
            raise DeskRejected("The anchor did not record this recovery.")
        return publish(store, deal_id)


def confirm_supplier_address(
    store: DeskStore,
    deal_id: str,
    supplier_id: str,
    *,
    address: str,
    name: str | None = None,
    human_confirmed: bool,
) -> dict[str, Any]:
    """A new email is sent only after a person confirms it on the chain."""
    if human_confirmed is not True:
        raise DeskRejected("A person has to confirm this address.")
    cleaned = require_address(address)
    if not cleaned:
        raise DeskRejected("Each supplier needs an address.")
    with deal_lock(deal_id):
        supplier = store.supplier(deal_id, supplier_id)
        if supplier is None:
            raise KeyError(supplier_id)
        new_name = (name or str(supplier["name"])).strip()
        store.update_supplier(supplier_id, name=new_name, address=cleaned)
        store.append_chain(
            deal_id,
            "supplier_address",
            {
                "supplier_id": supplier_id,
                "name": new_name,
                "address": cleaned,
                "confirmed_by": "human",
            },
        )
        return publish(store, deal_id)


def reopen_supplier(
    store: DeskStore,
    deal_id: str,
    supplier_id: str,
    *,
    human_confirmed: bool,
) -> dict[str, Any]:
    """A walked-away supplier quotes again only after this chained human action."""
    if human_confirmed is not True:
        raise DeskRejected("A person has to re-open this supplier.")
    with deal_lock(deal_id):
        if store.supplier(deal_id, supplier_id) is None:
            raise KeyError(supplier_id)
        store.insert_outcome(
            deal_id=deal_id,
            supplier_id=supplier_id,
            outcome="reopen_supplier",
            quote=None,
            offer=None,
            agreed_price=None,
        )
        store.append_chain(
            deal_id,
            "reopen_supplier",
            {"supplier_id": supplier_id, "confirmed_by": "human"},
        )
        return publish(store, deal_id)


def confirm_keep_supplier(
    store: DeskStore,
    deal_id: str,
    supplier_id: str,
    *,
    address: str,
    name: str | None = None,
    human_confirmed: bool,
) -> dict[str, Any]:
    """A flagged supplier on a migrated deal stays out until a person keeps an address.

    The address is the one in this request. The supplier row is not the source.
    """
    if human_confirmed is not True:
        raise DeskRejected("A person has to keep this supplier.")
    cleaned = require_address(address)
    if not cleaned:
        raise DeskRejected("Each supplier needs an address.")
    with deal_lock(deal_id):
        supplier = store.supplier(deal_id, supplier_id)
        if supplier is None:
            raise KeyError(supplier_id)
        entries = list(store.verify_links(deal_id).get("entries") or [])
        if not _deal_opened(entries).get("migrated"):
            raise DeskRejected("Keeping a supplier is for a migrated deal.")
        if not recompute.supplier_needs_keep(entries, supplier_id):
            raise DeskRejected("This supplier is not waiting to be kept.")
        given = (name or "").strip() or str(supplier.get("name") or "").strip()
        store.update_supplier(supplier_id, name=given, address=cleaned)
        store.append_chain(
            deal_id,
            "supplier_kept",
            {
                "supplier_id": supplier_id,
                "name": given,
                "address": cleaned,
                "confirmed_by": "human",
            },
        )
        return publish(store, deal_id)


def set_assumptions(store: DeskStore, deal_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    with deal_lock(deal_id):
        if store.get_deal(deal_id) is None:
            raise KeyError(deal_id)
        store.set_policy(deal_id, fields)
        store.append_chain(deal_id, "assumptions", {"fields": dict(fields)})
        store.touch(deal_id)
        return publish(store, deal_id)


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
    suppliers = {row["id"]: row for row in store.suppliers(deal_id)}
    for draft in snapshot.get("drafts") or []:
        if draft.get("status") != "draft":
            continue
        supplier = suppliers.get(draft.get("supplier_id"))
        try:
            raw = _draft_rfc822(
                product,
                draft["body"],
                to=None if supplier is None else supplier.get("address"),
                reply_to=draft.get("reply_to"),
                references=draft.get("references_header") or draft.get("reply_to"),
            )
            gmail_id = mailbox.create_gmail_draft(
                opener,
                token,
                raw,
                draft_id=draft.get("gmail_draft_id"),
            )
            store.set_gmail_draft(draft["id"], gmail_id=gmail_id or None, error=None)
        except Exception as exc:  # noqa: BLE001
            store.set_gmail_draft(draft["id"], gmail_id=None, error=type(exc).__name__)


def urllib_opener():
    import urllib.request

    return urllib.request.build_opener()


def _draft_rfc822(
    product: str,
    body: str,
    *,
    to: str | None = None,
    reply_to: str | None = None,
    references: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = counter_subject(product)
    if to:
        message["To"] = to
    if reply_to:
        message["In-Reply-To"] = reply_to
        message["References"] = references or reply_to
    message.set_content(body)
    return message.as_bytes()


def smtp_enabled() -> bool:
    if os.environ.get("DESK_SMTP_SEND", "").strip() != "1":
        return False
    return all(os.environ.get(key, "").strip() for key in ("SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"))


async def transmit(
    recipient: str,
    body: str,
    product: str,
    *,
    message_id: str | None = None,
    reply_to: str | None = None,
    references: str | None = None,
) -> bool:
    """Hand an already-approved draft to SMTP. Not used unless smtp_enabled()."""
    from src.transport.email_std import EmailStdTransport

    transport = EmailStdTransport()
    if transport.is_mock_mode:
        return False
    return bool(
        await transport.send_message(
            recipient,
            body,
            subject=counter_subject(product),
            metadata={
                "human_approved": True,
                "product": product,
                "message_id": message_id,
                "in_reply_to": reply_to,
                "references": references,
            },
        )
    )


def draft_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _decision_belongs(record: dict[str, Any], draft: dict[str, Any]) -> bool:
    same_supplier = str(record.get("supplier_id") or "") == str(draft.get("supplier_id") or "")
    same_deal = str(record.get("deal_id") or "") == str(draft.get("deal_id") or "")
    return same_supplier and same_deal


def _is_latest_decision(latest_id: str, decision_id: str) -> bool:
    return str(latest_id) == str(decision_id)


def _ask_matches_confirmed(stored: float, confirmed: float | None) -> bool:
    if confirmed is None:
        return False
    tolerance = float(OPT_TOL) * max(1.0, abs(float(stored)))
    return abs(float(stored) - float(confirmed)) <= tolerance


def _chain_held_ok(held: Any, previous_counter: float | None) -> bool:
    if held is None:
        return True
    if previous_counter is None:
        return False
    tolerance = float(OPT_TOL) * max(1.0, abs(float(previous_counter)))
    return abs(float(held) - float(previous_counter)) <= tolerance


def _body_is_rendered(body: str, rendered: str) -> bool:
    return draft_digest(body) == draft_digest(rendered)


def _confirmed_unit_ask(store: DeskStore, deal_id: str, supplier_id: str) -> float | None:
    from src.desk.headline import human_confirmed

    prices: list[float] = []
    for event in store.events(deal_id):
        if str(event.get("supplier_id") or "") != str(supplier_id):
            continue
        if not human_confirmed(event):
            continue
        provenance = event.get("provenance") or {}
        if provenance.get("origin") == "simulated" or provenance.get("simulated"):
            continue
        price = (event.get("parsed") or {}).get("unit_price")
        if price is None:
            continue
        prices.append(float(price))
    if not prices:
        return None
    return prices[-1]


def _supplier_decisions(entries: list[dict[str, Any]], supplier_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("type") != "decision":
            continue
        payload = entry.get("payload") or {}
        if str(payload.get("supplier_id") or "") != str(supplier_id):
            continue
        rows.append(entry)
    return rows


def _rendered_draft(
    store: DeskStore,
    draft: dict[str, Any],
    opened: dict[str, Any],
    counter: float,
    currency: str,
) -> str:
    from src.desk.project import draft_body
    from src.desk.project import _language

    texts = [
        str(event.get("raw_text") or "")
        for event in store.events(draft["deal_id"])
        if str(event.get("supplier_id") or "") == str(draft.get("supplier_id") or "")
    ]
    return draft_body(
        str(opened.get("product") or ""),
        float(counter),
        int(opened.get("quantity") or 0),
        _language(texts),
        currency,
    )


def _deal_opened(entries: list[dict[str, Any]]) -> dict[str, Any]:
    opened: dict[str, Any] = {}
    for entry in entries:
        if entry.get("type") == "deal_opened" and not opened:
            opened = dict(entry.get("payload") or {})
            opened["seq"] = int(entry.get("seq") or 0)
        if not opened or entry.get("type") != "policy_confirmed":
            continue
        payload = entry.get("payload") or {}
        if payload.get("confirmed_by") != "human":
            continue
        if int(entry.get("seq") or 0) <= int(opened.get("seq") or 0):
            continue
        for key in ("currency", "ceiling", "target_price", "quantity", "product", "risk_tolerance"):
            if key in payload:
                opened[key] = payload[key]
    return opened


def _migrated_limits_unconfirmed(entries: list[dict[str, Any]], opened: dict[str, Any]) -> bool:
    """A migrated deal stays unsendable until address and policy events follow the migration."""
    if not opened.get("migrated"):
        return False
    marker = int(opened.get("seq") or 0)
    confirmed_policy = False
    confirmed_addresses: set[str] = set()
    for entry in entries:
        if int(entry.get("seq") or 0) <= marker:
            continue
        payload = entry.get("payload") or {}
        if payload.get("confirmed_by") != "human":
            continue
        if entry.get("type") == "policy_confirmed" and payload.get("ceiling") is not None:
            confirmed_policy = True
        elif entry.get("type") == "supplier_address":
            confirmed_addresses.add(str(payload.get("supplier_id") or ""))
    needed = {str(item.get("id") or "") for item in (opened.get("suppliers") or []) if item.get("id")}
    return not confirmed_policy or not needed or not needed.issubset(confirmed_addresses)


def _body_currency(body: str) -> str:
    if "元" in body:
        return str(DEAL_CURRENCY)
    padded = f" {body} "
    for code in DEAL_CURRENCIES:
        if f" {code} " in padded:
            return str(code)
    return ""


def _currencies_agree(decision_currency: str, chained_currency: str, body_currency: str) -> bool:
    return decision_currency == chained_currency == body_currency


def _first_ingest_hash(entries: list[dict[str, Any]], event_id: str) -> str:
    for entry in entries:
        if entry.get("type") != "quote_ingested":
            continue
        payload = entry.get("payload") or {}
        if str(payload.get("event_id") or "") == str(event_id):
            return str(payload.get("raw_sha256") or "")
    return ""


def _latest_review(entries: list[dict[str, Any]], supplier_id: str) -> dict[str, Any] | None:
    chosen: dict[str, Any] | None = None
    for entry in entries:
        if entry.get("type") != "review":
            continue
        payload = entry.get("payload") or {}
        if payload.get("to") != "accepted":
            continue
        if str(payload.get("supplier_id") or "") != str(supplier_id):
            continue
        chosen = dict(payload)
        chosen["seq"] = int(entry.get("seq") or 0)
    return chosen


def _outcomes_from_chain(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        payload = dict(entry.get("payload") or {})
        supplier_id = str(payload.get("supplier_id") or "")
        if entry.get("type") == "reopen_supplier" and payload.get("confirmed_by") == "human" and supplier_id:
            payload = {"supplier_id": supplier_id, "outcome": "reopen_supplier"}
        elif entry.get("type") != "outcome":
            continue
        if not supplier_id:
            continue
        grouped.setdefault(supplier_id, []).append(payload)
    return grouped


def _migrated_unconfirmed(opened: dict[str, Any], review_seq: int | None) -> bool:
    """A legacy deal stays unsendable until a confirmation lands after the migration mark."""
    if not opened.get("migrated"):
        return False
    marker = int(opened.get("seq") or 0)
    if review_seq is None:
        return True
    return int(review_seq) <= marker


def _chained_piece_amount(raw_text: str, review: dict[str, Any], currency: str) -> float | None:
    """Re-read the confirmed deal-unit price from the message and the chain.

    A piece basis re-reads directly. An unknown or non-piece basis re-reads
    only when the chain recorded the human's basis choice or conversion.
    """
    token = str(review.get("token") or "")
    derived = price_from_token(raw_text, token, deal_currency=currency or None)
    if derived is not None:
        return float(derived)
    if str(review.get("confirmed_basis") or "") != "per_piece":
        return None
    found = confirm_amount(raw_text, token, deal_currency=currency or None)
    if found is None:
        return None
    amount, basis = found
    if str(review.get("stated_basis") or "") != str(basis):
        return None
    pieces = review.get("pieces_per_basis")
    if basis not in {None, "", "unknown", "per_piece"}:
        allowed = conversion_pieces(raw_text, basis)
        if pieces is None or allowed is None or not math.isclose(float(pieces), float(allowed)):
            return None
        return float(amount) / float(allowed)
    if pieces is not None:
        return None
    return float(amount)


def _review_binds_ok(raw_text: str, review: dict[str, Any], ask: float, ingested_hash: str) -> bool:
    """The raw message, the re-derived price, and the chained review are one confirmation."""
    digest = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    chained_raw = str(review.get("raw_sha256") or "")
    token = str(review.get("token") or "")
    currency = str(review.get("currency") or "")
    derived = _chained_piece_amount(raw_text, review, currency)
    span = _confirm_span(_visible_text(raw_text), token)
    chained_value = review.get("value")
    tolerance = float(OPT_TOL) * max(1.0, abs(float(chained_value or 0.0)), abs(float(ask)))
    price_ok = (
        derived is not None
        and chained_value is not None
        and abs(float(derived) - float(chained_value)) <= tolerance
    )
    ask_ok = chained_value is not None and abs(float(ask) - float(chained_value)) <= tolerance
    unit_ok = str(review.get("unit") or "") == "per_piece"
    span_ok = span == str(review.get("span") or "")
    return digest == ingested_hash and digest == chained_raw and price_ok and ask_ok and unit_ok and span_ok


def _close_amount(left: float, right: float) -> bool:
    tolerance = float(OPT_TOL) * max(1.0, abs(float(left)), abs(float(right)))
    return abs(float(left) - float(right)) <= tolerance


def _lists_close(left: Any, right: Any) -> bool:
    try:
        first = sorted(float(item) for item in (left or ()))
        second = sorted(float(item) for item in (right or ()))
    except (TypeError, ValueError):
        return False
    if len(first) != len(second):
        return False
    return all(_close_amount(item, other) for item, other in zip(first, second, strict=True))


def _constants_only_drift(record: dict[str, Any], recomputed: dict[str, Any]) -> bool:
    """The counter still matches. Only the deployed constants hash moved."""
    counter = record.get("counter")
    fresh = recomputed.get("counter")
    ceiling = record.get("ceiling")
    chained_ceiling = recomputed.get("ceiling")
    if counter is None or fresh is None or ceiling is None or chained_ceiling is None:
        return False
    same_counter = _close_amount(float(counter), float(fresh))
    same_ceiling = _close_amount(float(ceiling), float(chained_ceiling))
    same_unconfirmed = int(record.get("unconfirmed") or 0) == int(recomputed.get("unconfirmed") or 0)
    same_rivals = _lists_close(record.get("rival_asks"), recomputed.get("rival_asks"))
    same_constants = (record.get("constants") or {}) == (recomputed.get("constants") or {})
    return same_counter and same_ceiling and same_unconfirmed and same_rivals and not same_constants


def _chain_decision_equal(record: dict[str, Any], recomputed: dict[str, Any]) -> bool:
    counter = record.get("counter")
    fresh = recomputed.get("counter")
    ceiling = record.get("ceiling")
    chained_ceiling = recomputed.get("ceiling")
    if counter is None or fresh is None or ceiling is None or chained_ceiling is None:
        return False
    same_counter = _close_amount(float(counter), float(fresh))
    same_ceiling = _close_amount(float(ceiling), float(chained_ceiling))
    same_unconfirmed = int(record.get("unconfirmed") or 0) == int(recomputed.get("unconfirmed") or 0)
    same_rivals = _lists_close(record.get("rival_asks"), recomputed.get("rival_asks"))
    same_constants = (record.get("constants") or {}) == (recomputed.get("constants") or {})
    return same_counter and same_ceiling and same_unconfirmed and same_rivals and same_constants


def _price_in_message(raw_text: str, review: dict[str, Any], currency: str) -> float | None:
    """The amount the raw message states for this confirmation.

    The chained token is read first. A rewritten message no longer contains
    that token, so the amount now on the line is what a bypassed bind would
    price. The bind itself is what requires the amount to equal the chain.
    """
    derived = _chained_piece_amount(raw_text, review, currency)
    if derived is not None:
        return float(derived)
    reading = parse_quote(raw_text, deal_currency=currency or None)
    suggested = str((reading.get("provenance") or {}).get("suggested_token") or "")
    if not suggested:
        return None
    fallback = price_from_token(raw_text, suggested, deal_currency=currency or None)
    if fallback is None:
        return None
    return float(fallback)


def _ignored_quote(payload: dict[str, Any]) -> bool:
    if payload.get("simulated"):
        return True
    return str(payload.get("origin") or "") in {"simulated", "fixture"}


def _recompute_from_chain(
    store: DeskStore,
    draft: dict[str, Any],
    entries: list[dict[str, Any]],
    record: dict[str, Any],
) -> dict[str, Any]:
    """The counter the chained confirmations and the chained ceiling issue."""
    from src.desk.record import constants_snapshot
    from src.desk.recompute import _landed_consistent_rivals, counter_of, decision_for_supplier, quoting_supplier_ids
    from src.engine.landed import landed_unit_cost, ranking_basis

    opened = _deal_opened(entries)
    currency = str(opened.get("currency") or "")
    ceiling = opened.get("ceiling")
    events = {str(event["id"]): event for event in store.events(draft["deal_id"])}
    asks: dict[str, float] = {}
    histories: dict[str, list[float]] = {}
    landed_by_id: dict[str, dict[str, Any]] = {}
    unconfirmed = 0
    for entry in entries:
        if entry.get("type") != "review":
            continue
        payload = entry.get("payload") or {}
        if payload.get("to") != "accepted" or _ignored_quote(payload):
            continue
        event_id = str(payload.get("event_id") or "")
        event = events.get(event_id)
        raw_text = "" if event is None else str(event.get("raw_text") or "")
        ingested = _first_ingest_hash(entries, event_id)
        supplier_id = str(payload.get("supplier_id") or "")
        raw_value = payload.get("value")
        try:
            ask_hint = 0.0 if raw_value is None else float(raw_value)
        except (TypeError, ValueError):
            ask_hint = 0.0
        if not supplier_id or not _review_binds_ok(raw_text, payload, ask_hint, ingested):
            if event is not None and (event.get("parsed") or {}).get("unit_price") is not None:
                unconfirmed += 1
            continue
        derived = _price_in_message(raw_text, payload, currency)
        if derived is None:
            unconfirmed += 1
            continue
        asks[supplier_id] = float(derived)
        histories.setdefault(supplier_id, []).append(float(derived))
        landed_by_id[supplier_id] = landed_unit_cost(
            unit_price=float(derived),
            freight=payload.get("freight"),
            duty_rate=payload.get("duty_rate"),
            fees=payload.get("fees"),
            currency=currency or None,
            base_currency=currency or None,
        )
    outcomes_for = _outcomes_from_chain(entries)
    live_ids = quoting_supplier_ids(list(asks), outcomes_for)
    asks = {key: asks[key] for key in live_ids}
    landed_by_id = {key: landed_by_id[key] for key in live_ids if key in landed_by_id}
    supplier_id = str(record.get("supplier_id") or "")
    basis = ranking_basis([landed_by_id[key] for key in asks if key in landed_by_id])
    rivals = (
        _landed_consistent_rivals(supplier_id, asks, landed_by_id, basis)
        if supplier_id in asks
        else []
    )
    snapshot = constants_snapshot()
    if ceiling is None or supplier_id not in asks:
        return {
            "counter": None,
            "ceiling": None if ceiling is None else float(ceiling),
            "unconfirmed": unconfirmed,
            "rival_asks": [float(item) for item in rivals],
            "constants": snapshot,
        }
    built = decision_for_supplier(
        prices=list(histories.get(supplier_id) or []),
        outcomes=list(outcomes_for.get(supplier_id) or []),
        ask=float(asks[supplier_id]),
        rivals=[float(item) for item in rivals],
        ceiling=float(ceiling),
        risk_cap=float(opened.get("risk_tolerance") or 0.0),
    )
    choice = None if built is None else built[1]
    fresh = counter_of(choice, float(ceiling), float(asks[supplier_id]))
    held = record.get("held_previous_counter")
    if fresh is not None and held is not None and float(held) < fresh:
        fresh = float(held)
    return {
        "counter": fresh,
        "ceiling": float(ceiling),
        "unconfirmed": unconfirmed,
        "rival_asks": [float(item) for item in rivals],
        "constants": snapshot,
    }


def _send_is_new(entries: list[dict[str, Any]], draft_id: str, digest: str) -> bool:
    """One approval per draft body. A failed transmission may be tried again.

    An approval whose anchor write failed has no send result. A later re-anchor
    that did not shorten the chain opens exactly one retry of that same body.
    A retry is still refused when the anchor history already recorded the approval.
    """
    approved = False
    unfinished = False
    reanchored_after = False
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("type") == "reanchor" and unfinished:
            reanchored_after = True
        if str(payload.get("draft_id") or "") != str(draft_id):
            continue
        if entry.get("type") == "send_approved" and str(payload.get("body_sha256") or "") == digest:
            approved = True
            unfinished = True
            reanchored_after = False
        failed = entry.get("type") == "send_result" and payload.get("status") == "failed"
        finished = entry.get("type") == "send_result" and payload.get("status") in {"approved", "sent"}
        same_body = str(payload.get("body_sha256") or "") == digest
        legacy_draft = not payload.get("body_sha256") and str(payload.get("draft_id") or "") == str(draft_id)
        if failed and (same_body or legacy_draft):
            approved = False
            unfinished = False
        if finished and (same_body or legacy_draft):
            unfinished = False
    if approved and unfinished and reanchored_after:
        return True
    return not approved


def _fresh_counter(record: dict[str, Any]) -> float:
    """The counter ``decide_supplier`` issues from the inputs stored on the record."""
    from src.desk.decide import decide_supplier
    from src.engine.reserve import ReserveState

    needed = ("ask", "risk_cap", "prior_alpha", "prior_beta", "reserve_upper", "reference_ask", "ceiling")
    if any(record.get(key) is None for key in needed):
        raise SendRefused("The decision record does not store the inputs needed to recompute the counter.")
    state = ReserveState(
        alpha=float(record["prior_alpha"]),
        beta=float(record["prior_beta"]),
        upper=float(record["reserve_upper"]),
        reject_ratios=tuple(float(item) for item in (record.get("reject_ratios") or ())),
        n_responses=int(record.get("prior_n") or 0),
        reference_ask=float(record["reference_ask"]),
    )
    intercept = record.get("hazard_intercept")
    gap = record.get("hazard_gap")
    choice = decide_supplier(
        ask=float(record["ask"]),
        rivals=[float(item) for item in (record.get("rival_asks") or ())],
        ceiling=float(record["ceiling"]),
        state=state,
        risk_cap=float(record["risk_cap"]),
        intercept=None if intercept is None else float(intercept),
        gap_coef=None if gap is None else float(gap),
    )
    if choice.blocked or str(choice.counter_relation) == "withheld":
        raise SendRefused("The recomputed decision withholds the counter.")
    fresh = min(float(choice.default_counter), float(record["ceiling"]), float(record["ask"]))
    previous = record.get("held_previous_counter")
    if previous is not None and float(previous) < fresh:
        return float(previous)
    return fresh


def _transmission_differs(outgoing: dict[str, str], chained: dict[str, str]) -> bool:
    left = transmission_digest(
        recipient=outgoing["to"],
        subject=outgoing["subject"],
        in_reply_to=outgoing["in_reply_to"],
        references=outgoing["references"],
        body=outgoing["body"],
    )
    right = transmission_digest(
        recipient=chained["to"],
        subject=chained["subject"],
        in_reply_to=chained["in_reply_to"],
        references=chained["references"],
        body=chained["body"],
    )
    return left != right


def _envelope(
    store: DeskStore,
    entries: list[dict[str, Any]],
    draft: dict[str, Any],
    deal: dict[str, Any],
    supplier: dict[str, Any],
    counter: float,
) -> dict[str, str]:
    """The mail fields. They have to match the chain, then those chained fields are what is sent."""
    identity = chained_supplier(entries, str(draft.get("supplier_id") or "")) or {}
    opened = _deal_opened(entries)
    product = str(opened.get("product") or "")
    currency = str(opened.get("currency") or "")
    rendered = _rendered_draft(store, draft, opened, float(counter), currency)
    in_reply, references = reply_headers(entries, str(draft.get("supplier_id") or ""))
    chained = {
        "to": str(identity.get("address") or ""),
        "subject": counter_subject(product),
        "in_reply_to": in_reply,
        "references": references,
        "body": rendered,
        "product": product,
    }
    outgoing = {
        "to": str(supplier.get("address") or ""),
        "subject": counter_subject(str(deal.get("product") or "")),
        "in_reply_to": str(draft.get("reply_to") or ""),
        "references": str(draft.get("references_header") or ""),
        "body": str(draft.get("body") or ""),
        "product": str(deal.get("product") or ""),
    }
    if _transmission_differs(outgoing, chained):
        raise SendRefused("The recipient is not the chained supplier address.")
    outgoing["digest"] = transmission_digest(
        recipient=outgoing["to"],
        subject=outgoing["subject"],
        in_reply_to=outgoing["in_reply_to"],
        references=outgoing["references"],
        body=outgoing["body"],
    )
    return outgoing


def _guard_send(store: DeskStore, draft: dict[str, Any], deal: dict[str, Any]) -> dict[str, Any]:
    """Recompute the counter from the stored inputs. Refuse on any mismatch."""
    decision_id = draft.get("decision_id")
    if not decision_id:
        raise SendRefused("This draft has no decision record.")
    stored = store.get_decision(str(decision_id))
    if stored is None:
        raise SendRefused("This draft has no decision record.")
    record = stored["record"]
    loaded_digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    if loaded_digest != stored["record_sha256"]:
        raise SendRefused("The decision record hash does not match.")
    chain = store.verify_chain(draft["deal_id"])
    if not chain["ok"]:
        raise SendRefused("The audit chain does not verify.")
    if not _decision_belongs(record, draft):
        raise SendRefused("The decision does not belong to this draft.")
    opened_for_migration = _deal_opened(list(chain.get("entries") or []))
    prior_review = _latest_review(list(chain.get("entries") or []), str(draft.get("supplier_id") or ""))
    prior_seq = None if prior_review is None else int(prior_review.get("seq") or 0)
    if _migrated_unconfirmed(opened_for_migration, prior_seq):
        raise SendRefused("Re-confirm the quote. migrated, pre-chain inputs unverified.")
    if _migrated_limits_unconfirmed(list(chain.get("entries") or []), opened_for_migration):
        raise SendRefused("Confirm each supplier address and the policy limits on the chain before sending.")
    if recompute.supplier_needs_keep(list(chain.get("entries") or []), str(draft.get("supplier_id") or "")):
        raise SendRefused("Confirm this supplier was added after migration before sending.")
    supplier_rows = _supplier_decisions(list(chain.get("entries") or []), str(record.get("supplier_id") or ""))
    if not supplier_rows:
        raise SendRefused("The audit chain has no decision entry for this draft.")
    latest_payload = supplier_rows[-1].get("payload") or {}
    if not _is_latest_decision(str(latest_payload.get("decision_id") or ""), str(decision_id)):
        raise SendRefused("The draft is not this supplier's latest decision.")
    pinned = [
        entry for entry in supplier_rows
        if str((entry.get("payload") or {}).get("decision_id") or "") == str(decision_id)
    ]
    chain_digest = str((pinned[0].get("payload") or {}).get("record_sha256") or "")
    if chain_digest != loaded_digest:
        raise SendRefused("The decision record does not match the audit chain.")
    if int(record.get("unconfirmed") or 0) > 0:
        raise SendRefused("Unconfirmed numbers were used. The counter is withheld.")
    if not all(bool(item.get("passed")) for item in record.get("checks") or []):
        raise SendRefused("A decision check failed. The counter is withheld.")
    ask = record.get("ask")
    ceiling = deal.get("ceiling_price")
    counter = record.get("counter")
    if ask is None or counter is None or ceiling is None:
        raise SendRefused("The stored decision has no counter to re-derive.")
    confirmed_ask = _confirmed_unit_ask(store, str(draft["deal_id"]), str(draft.get("supplier_id") or ""))
    if not _ask_matches_confirmed(float(ask), confirmed_ask):
        raise SendRefused("The stored ask does not match the confirmed quote.")
    previous_counter = None
    if len(supplier_rows) >= 2:
        prev_payload = supplier_rows[-2].get("payload") or {}
        prev_stored = store.get_decision(str(prev_payload.get("decision_id") or ""))
        prev_digest = str(prev_payload.get("record_sha256") or "")
        if prev_stored is None or str(prev_stored.get("record_sha256") or "") != prev_digest:
            raise SendRefused("The held counter is not the previous chained decision.")
        prev_counter = (prev_stored.get("record") or {}).get("counter")
        if prev_counter is not None:
            previous_counter = float(prev_counter)
    if not _chain_held_ok(record.get("held_previous_counter"), previous_counter):
        raise SendRefused("The held counter is not the previous chained decision.")
    recomputed = _fresh_counter(record)
    tolerance = float(OPT_TOL) * max(1.0, abs(float(counter)))
    if abs(float(counter) - recomputed) > tolerance:
        raise SendRefused("The stored counter does not match a fresh decision.")
    entries = list(chain.get("entries") or [])
    opened = _deal_opened(entries)
    chained_currency = str(opened.get("currency") or "")
    rendered = _rendered_draft(store, draft, opened, float(counter), chained_currency)
    if not _currencies_agree(str(record.get("currency") or ""), chained_currency, _body_currency(rendered)):
        raise SendRefused("The decision currency is not the chained deal currency.")
    review = _latest_review(entries, str(draft.get("supplier_id") or ""))
    review_event = None if review is None else store.event(str(draft["deal_id"]), str(review.get("event_id") or ""))
    raw_text = "" if review_event is None else str(review_event.get("raw_text") or "")
    ingested_hash = "" if review is None else _first_ingest_hash(entries, str(review.get("event_id") or ""))
    if review is None or not _review_binds_ok(raw_text, review, float(ask), ingested_hash):
        raise SendRefused("The confirmed price is not the chained review.")
    issued = _recompute_from_chain(store, draft, entries, record)
    if not _chain_decision_equal(record, issued):
        if _constants_only_drift(record, issued):
            publish(store, str(draft["deal_id"]))
            raise DraftStale("Policy constants changed. Read the new draft and approve it again.")
        raise SendRefused("The stored counter is not the decision chained confirmations issue.")
    if not _body_is_rendered(str(draft.get("body") or ""), rendered):
        raise SendRefused("The draft text is not the rendered counter.")
    chained_ceiling = opened.get("ceiling")
    if chained_ceiling is None or float(counter) - float(chained_ceiling) > tolerance:
        raise SendRefused("The counter is above the current ceiling.")
    return record


async def send_draft(
    store: DeskStore,
    draft_id: str,
    *,
    human_approved: bool,
    body_sha256: str,
    sender: Sender | None = None,
) -> dict[str, Any]:
    if human_approved is not True:
        raise SendRefused("A person has to approve this draft before it can be sent.")
    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(draft_id)
    with deal_lock(draft["deal_id"]):
        draft = store.get_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft["status"] == "sent" and draft.get("transmitted"):
            return draft
        if draft["status"] in {"superseded", "sending"}:
            raise DraftStale("This draft changed. Read the new text and approve it again.")
        digest = draft_digest(draft["body"])
        if body_sha256 != digest:
            raise DraftStale("This draft changed. Read the new text and approve it again.")
        if draft["status"] not in {"draft", "failed", "approved"}:
            raise DraftStale("This draft changed. Read the new text and approve it again.")
        if not chain_key_configured():
            raise SendRefused("Desk sends require TRADEFLOW_CHAIN_KEY.")
        prior_chain = store.verify_chain(draft["deal_id"])
        if not prior_chain["ok"]:
            raise SendRefused("audit chain broken")
        if not _send_is_new(list(prior_chain.get("entries") or []), draft_id, digest):
            raise SendRefused("This draft was already approved.")
        deal = store.get_deal(draft["deal_id"])
        supplier = store.supplier(draft["deal_id"], draft["supplier_id"])
        if deal is None or supplier is None:
            raise KeyError(draft_id)
        record = _guard_send(store, draft, deal)
        envelope = _envelope(
            store,
            list(prior_chain.get("entries") or []),
            draft,
            deal,
            supplier,
            float(record["counter"]),
        )
        history = export_history(draft["deal_id"])
        if history["status"] in {"invalid", "down"}:
            raise SendRefused("The anchor history does not verify.")
        if approval_recorded(
            draft["deal_id"],
            list(prior_chain.get("entries") or []),
            draft_id=draft_id,
            body_sha256=digest,
            transmission_sha256=envelope["digest"],
        ):
            raise SendRefused("This draft was already approved.")
        approved = store.append_chain(
            draft["deal_id"],
            "send_approved",
            {
                "draft_id": draft_id,
                "decision_id": draft.get("decision_id"),
                "body_sha256": digest,
                "transmission_sha256": envelope["digest"],
            },
        )
        if approved.get("anchor_ok") is False:
            publish(store, draft["deal_id"])
            raise SendRefused("The anchor did not record this send.")
        message_id = f"<desk.{draft_id}.v{int(draft.get('version') or 1)}@tradeflow.local>"
        recipient = envelope["to"]
        body = envelope["body"]
        product = envelope["product"]
        reply_to = envelope["in_reply_to"] or None
        references = envelope["references"] or None
        deal_id = draft["deal_id"]
        if not smtp_enabled():
            store.mark_draft(
                draft_id,
                status="approved",
                transmitted=False,
                error=None,
                message_id=message_id,
            )
            store.append_chain(
                deal_id,
                "send_result",
                {
                    "draft_id": draft_id,
                    "status": "approved",
                    "transmitted": False,
                    "body_sha256": digest,
                },
            )
            publish(store, deal_id)
            updated = store.get_draft(draft_id)
            return updated or draft
        if not store.claim_draft_send(draft_id, message_id):
            raise DraftStale("This draft changed. Read the new text and approve it again.")
    fn = sender or transmit
    try:
        try:
            transmitted = bool(
                await fn(
                    recipient,
                    body,
                    product,
                    message_id=message_id,
                    reply_to=reply_to,
                    references=references,
                )
            )
        except TypeError:
            transmitted = bool(await fn(recipient, body, product))
    except Exception:
        store.mark_draft(
            draft_id,
            status="failed",
            transmitted=False,
            error="SMTP send failed",
            message_id=message_id,
        )
        store.append_chain(
            deal_id,
            "send_result",
            {
                "draft_id": draft_id,
                "status": "send_outcome_unknown",
                "transmitted": False,
                "body_sha256": digest,
            },
        )
        publish(store, deal_id)
        raise
    if not transmitted:
        store.mark_draft(
            draft_id,
            status="failed",
            transmitted=False,
            error="SMTP send failed",
            message_id=message_id,
        )
    else:
        store.mark_draft(
            draft_id,
            status="sent",
            transmitted=True,
            error=None,
            message_id=message_id,
        )
    store.append_chain(
        deal_id,
        "send_result",
        {
            "draft_id": draft_id,
            "status": "sent" if transmitted else "failed",
            "transmitted": bool(transmitted),
            "body_sha256": digest,
        },
    )
    publish(store, deal_id)
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
    return matched


def _seen_key(mailbox_key: str, deal_id: str) -> str:
    return f"{mailbox_key}:{deal_id}"


def _skip_for_poll(desk: DeskStore, mailbox_key: str, deal_id: str | None) -> set[str]:
    """Skip ids this poll has already stored.

    A deal-scoped poll only skips that deal's ingested ids. A poll of every
    deal skips an id only after every current deal has stored it, so one
    deal's poll cannot hide another deal's mail.
    """
    if deal_id:
        return desk.mailbox_ids(_seen_key(mailbox_key, deal_id))
    seen = [desk.mailbox_ids(_seen_key(mailbox_key, deal["id"])) for deal in desk.list_deals()]
    if not seen:
        return set()
    return set.intersection(*seen)


def ingest_mailbox_items(
    store: DeskStore,
    items: list[dict[str, Any]],
    *,
    deal_id: str | None,
    channel: str,
    mailbox_key: str | None = None,
) -> dict[str, Any]:
    counts: dict[str, Any] = {"ingested": 0, "review": 0, "duplicates": 0, "ignored": 0}
    for item in items:
        external = item.get("external_id") or _external_id(item)
        targets = _route_deals(store, item.get("sender") or "", deal_id)
        if not targets:
            counts["ignored"] += 1
            continue
        extra = {
            "channel": channel,
            "message_id": item.get("message_id"),
            "references": item.get("references"),
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
            else:
                counts["ingested"] += 1
                event = result.get("event") or {}
                if event.get("status") == "review":
                    counts["review"] += 1
            if mailbox_key:
                store.remember_mailbox(_seen_key(mailbox_key, target), external)
                uid_key = item.get("uid")
                validity = item.get("uidvalidity")
                if uid_key and validity:
                    store.remember_mailbox(
                        _seen_key(mailbox_key, target),
                        f"uid:{validity}:{uid_key}",
                    )
    counts["extractor_import"] = import_status()
    return counts


def poll_imap_once(
    store: DeskStore | None = None,
    *,
    deal_id: str | None = None,
) -> dict[str, Any]:
    desk = store or get_store()
    if deal_id and desk.get_deal(deal_id) is None:
        raise KeyError(deal_id)
    settings = mailbox.imap_settings_from_env()
    if settings is None:
        return {"status": "not_configured", "ingested": 0, "review": 0, "duplicates": 0, "ignored": 0}
    if deal_id is None and not desk.list_deals():
        return {"status": "no-deals", "ingested": 0, "review": 0, "duplicates": 0, "ignored": 0}
    mailbox_key = f"imap:{settings['host']}:{settings['username']}"
    items, _uidvalidity = mailbox.pull_window(
        host=settings["host"],
        port=int(settings["port"]),
        username=settings["username"],
        password=settings["password"],
        use_ssl=bool(settings["use_ssl"]),
        skip_ids=_skip_for_poll(desk, mailbox_key, deal_id),
    )
    counts = ingest_mailbox_items(
        desk,
        items,
        deal_id=deal_id,
        channel="imap",
        mailbox_key=mailbox_key,
    )
    counts["status"] = "ok"
    return counts


def poll_gmail_once(store: DeskStore | None = None, *, deal_id: str | None = None, opener=None) -> dict[str, Any]:
    desk = store or get_store()
    if not mailbox.gmail_configured():
        return {"status": "not_configured", "ingested": 0, "review": 0, "duplicates": 0}
    if deal_id is None and not desk.list_deals():
        return {"status": "no-deals", "ingested": 0, "review": 0, "duplicates": 0}
    client = opener or urllib_opener()
    mailbox_key = "gmail"
    items = mailbox.pull_gmail_window(client, skip_ids=_skip_for_poll(desk, mailbox_key, deal_id))
    counts = ingest_mailbox_items(
        desk,
        items,
        deal_id=deal_id,
        channel="gmail",
        mailbox_key=mailbox_key,
    )
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


BUS = DeskBus()
