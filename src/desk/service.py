"""Desk actions. Ingest never sends. Sending is a separate, explicit call."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import os
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
    UNIT_SANITY_MULTIPLE,
)
from src.desk import mailbox, recompute
from src.desk.approval_signing import (
    approval_is_pinned,
    approval_policy,
    required_approval_pubkey,
    signed_payload,
    verify_signed_approval,
)
from src.desk.audit_chain import approval_recorded, chain_key_configured, export_history, reanchor_refusal
from src.desk.extract import time_left
from src.desk.input_checks import message_id_or_blank, references_or_blank, require_address, require_plain
from src.desk.names import supplier_name_key
from src.desk.parse import _confirm_span, _visible_text, parse_quote, price_from_token
from src.desk.bus import BUS  # noqa: F401  (re-exported for existing callers)
from src.desk.bus import DeskBus  # noqa: F401  (re-exported for existing callers)
from src.desk.errors import DraftStale, SendRefused
from src.desk.snapshot import publish
from src.desk.store import DeskRejected, DeskStore, DeskUnavailable, get_store
from src.desk.verify import _envelope, _guard_send, _send_is_new, draft_digest

# Compatibility surface. The send gate moved to ``src/desk/verify.py`` on
# 2026-10-23; these names are re-exported so every existing import path —
# including the step tests, which import individual predicates to assert on them
# directly — keeps working. Importing a private name across modules is not the
# ideal way to test a predicate, but breaking 116 call sites to make a point
# about underscores would be a worse trade.
from src.desk.verify import (  # noqa: F401
    _ask_matches_confirmed,
    _body_currency,
    _body_is_rendered,
    _chain_decision_equal,
    _chain_held_ok,
    _close_amount,
    _confirmed_unit_ask,
    _constants_only_drift,
    _currencies_agree,
    _deal_opened,
    _decision_belongs,
    _first_ingest_hash,
    _fresh_counter,
    _ignored_quote,
    _is_latest_decision,
    _latest_review,
    _lists_close,
    _migrated_limits_unconfirmed,
    _migrated_unconfirmed,
    _outcomes_from_chain,
    _price_in_message,
    _recompute_from_chain,
    _rendered_draft,
    _review_binds_ok,
    _supplier_decisions,
    _transmission_differs,
)
from src.desk.transmission import counter_subject

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
        if re.match(r"(?i)\s*(?:个|件|只|pcs|pc|pieces|k)(?:\b|(?=[\u4e00-\u9fff])|$)", tail):
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


def _extractor_pool() -> concurrent.futures.ThreadPoolExecutor:
    global _EXTRACTOR_POOL
    with _EXTRACTOR_POOL_GUARD:
        if _EXTRACTOR_POOL is None:
            _EXTRACTOR_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=int(EXTRACTOR_MAX_IN_FLIGHT),
                thread_name_prefix="desk-extractor-pool",
            )
        return _EXTRACTOR_POOL


def shutdown_extractors(wait: bool = False) -> None:
    """Close the extractor pool. Called from the app lifespan on shutdown.

    Without this the pool outlives the event loop: interpreter exit joins the
    worker threads abruptly, and any queued extraction is dropped without
    recording that it never ran. ``wait=False`` keeps shutdown bounded — a
    stuck provider call must not hold the process open — and the fail-closed
    rule already treats an unsettled extraction as "not accepted".
    """
    global _EXTRACTOR_POOL
    with _EXTRACTOR_POOL_GUARD:
        pool, _EXTRACTOR_POOL = _EXTRACTOR_POOL, None
    if pool is not None:
        pool.shutdown(wait=wait, cancel_futures=True)


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

    queued = time.monotonic()
    budget = _hop_timeout(
        "TRADEFLOW_EXTRACTOR_TIMEOUT_BUDGET", float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS)
    )
    job = _ExtractorJob()

    def _run() -> None:
        try:
            from src.desk.shadow import shadow_from_message

            remaining = time_left(budget, queued)
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
            job.finish()
            with _EXTRACTOR_THREADS_GUARD:
                if _EXTRACTOR_THREADS.get(event_id) is job:
                    _EXTRACTOR_THREADS.pop(event_id, None)

    with _EXTRACTOR_THREADS_GUARD:
        _EXTRACTOR_THREADS[event_id] = job
    _extractor_pool().submit(_run)


def accept_review(
    store: DeskStore,
    deal_id: str,
    event_id: str,
    supplier_id: str | None,
    *,
    chosen_token: str | None = None,
    reject: bool = False,
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
        price = price_from_token(event["raw_text"], chosen_token, deal_currency=currency)
        if price is None:
            raise DeskRejected("That amount is not a unit price in the deal currency.")
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






























































async def send_draft(
    store: DeskStore,
    draft_id: str,
    *,
    human_approved: bool,
    body_sha256: str,
    approval: dict[str, Any] | None = None,
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
        # Human-level accountability gate. The signed payload is exactly what
        # the approving person reviewed: the deal, the draft, and the body
        # digest. The transmission digest is server-computed and recorded, but
        # the human never saw it, so it is not signed.
        #
        # [SECURITY 2026-10-23] Fail-closed policy, not opt-in-by-env-var:
        #   * policy "required" + no pinned key  -> refuse the send. A desk that
        #     claims signed approvals while holding no key to verify against is
        #     worse than one that admits it has none, because the boolean path
        #     would still be taken.
        #   * policy "required" + no approval    -> refuse.
        #   * a supplied approval from an unpinned key is not an approval.
        policy = approval_policy()
        if policy == "required":
            if required_approval_pubkey() is None:
                raise SendRefused(
                    "This desk requires signed approvals but TRADEFLOW_APPROVAL_PUBKEY "
                    "is not configured. Refusing to send (fail-closed)."
                )
            if approval is None:
                raise SendRefused("This desk requires a signed approval for sends.")
        if approval is not None:
            if not verify_signed_approval(approval, signed_payload(draft["deal_id"], draft_id, digest)):
                raise SendRefused("The approval signature does not verify.")
            if required_approval_pubkey() is not None and not approval_is_pinned(approval):
                raise SendRefused("The approval was signed by a key this desk does not pin.")
        chain_payload: dict[str, Any] = {
            "draft_id": draft_id,
            "decision_id": draft.get("decision_id"),
            "body_sha256": digest,
            "transmission_sha256": envelope["digest"],
        }
        if approval is not None:
            chain_payload["approval"] = {
                "alg": approval.get("alg"),
                "pubkey": approval.get("pubkey"),
                "sig": approval.get("sig"),
            }
        approved = store.append_chain(
            draft["deal_id"],
            "send_approved",
            chain_payload,
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




