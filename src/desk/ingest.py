"""Quote intake: parse, classify, persist, schedule extraction, poll mail.

[ARCHITECTURE 2026-10-24] Extracted verbatim from ``service.py`` (1,782
lines holding intake, polling, the extractor pool, the confirmation
endpoints and the send path in one module). This module owns the intake
pipeline — message → reading → provenance → chain event → extractor
scheduling — plus the IMAP/Gmail pollers and the per-deal lock table.
It imports ``store``, ``mailbox``, ``snapshot`` and the engine readers; it
does **not** import ``service``. ``service.py`` re-exports every moved
name, so all existing import paths keep working (the verify.py precedent).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import os
import threading
import time
from typing import Any, Awaitable, Callable

from src.config.heuristics import (
    DEAL_CURRENCY,
    EXTRACTOR_MAX_IN_FLIGHT,
    UNIT_SANITY_MULTIPLE,
)
from src.desk import mailbox
from src.desk.extract import time_left
from src.desk.input_checks import message_id_or_blank, references_or_blank, require_landed_total
from src.desk.parse import parse_quote
from src.desk.bus import BUS  # noqa: F401  (re-exported for existing callers)
from src.desk.bus import DeskBus  # noqa: F401  (re-exported for existing callers)
from src.desk.snapshot import publish
from src.desk.store import DeskRejected, DeskStore, get_store

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

logger = logging.getLogger("tradeflow.desk")


SOURCES = {"email", "chat-import", "paste"}

def classify_origin(raw_text: str, extra: dict | None, *, trusted_channel: bool = True) -> dict[str, Any]:
    """Fixture and simulated are decided here.

    A client ``simulated: true`` is a downgrade and is kept. The page URL
    or title can still upgrade a live flag to simulated. A client true is
    never stored as live.

    [SECURITY 2026-10-24] ``trusted_channel`` draws the fixture boundary.
    The fixture marker exists so a labelled test sample can never be stored
    as a live supplier quote. An inbound email is an untrusted channel: the
    sender controls the header *and* the body, so anyone could type
    ``X-TradeFlow-Fixture`` into a message and have a real supplier quote
    recorded as fixture origin in a tamper-evident chain. The marker is
    therefore honoured only from channels this process controls (paste,
    the API, the import harness); from the mailbox polls it is dropped and
    the message is classified live, where the human-confirm gate still
    applies before any number is believed.
    """
    fields = dict(extra or {})
    client_simulated = bool(fields.pop("simulated", False))
    header = str(fields.get("fixture") or "").strip() if trusted_channel else ""
    text = raw_text or ""
    fixture = bool(header) or ("X-TradeFlow-Fixture" in text and trusted_channel)
    if not trusted_channel:
        # Drop the untrusted marker entirely rather than storing a falsified one.
        fields.pop("fixture", None)
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
    trusted_channel: bool = True,
) -> dict[str, Any]:
    if source not in SOURCES:
        raise DeskRejected("Quote source must be email, chat-import, or paste.")
    deal = store.get_deal(deal_id)
    if deal is None:
        raise KeyError(deal_id)
    maximum_unit = max(float(deal["target_price"]), float(deal["ceiling_price"])) * float(
        deal.get("unit_sanity_multiple") or UNIT_SANITY_MULTIPLE
    )
    require_landed_total(maximum_unit, freight, fees, duty_rate)
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
            trusted_channel=trusted_channel,
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
    if reading.get("status") != "accepted":
        # A tier table: pre-select the row for the deal's quantity instead of
        # the first row. The quote stays in review; a person confirms.
        from src.desk.tiers import tier_for_quantity

        try:
            tier = tier_for_quantity(raw_text, deal.get("quantity"))
        except Exception:
            tier = None
        if tier is not None and tier["token"] in candidates:
            provenance["tier_pick"] = tier
            if suggested != tier["token"]:
                provenance["rules_suggested_token"] = suggested
                suggested = tier["token"]
                provenance["suggested_by"] = "tier"
    provenance["candidates"] = candidates
    provenance["suggested_token"] = suggested
    reading["provenance"] = provenance
    return {"reading": reading, "shadow": shadow, "low": low, "high": high}

def _mention_trace(raw_text: str, shadow: dict[str, Any]) -> list[dict[str, Any]]:
    """What the model labelled, for a person to read: class and verbatim span only.

    A span that is not in the message is dropped, so the desk never shows text
    the supplier did not write. No amount is kept; amounts come from the rules
    and from the person who confirms one.
    """
    from src.config.heuristics import DESK_TRACE_MAX_MENTIONS
    from src.desk.span_read import span_in_message

    rows: list[dict[str, Any]] = []
    for mention in shadow.get("mentions") or []:
        if len(rows) >= int(DESK_TRACE_MAX_MENTIONS):
            break
        span = str(mention.get("evidence_span") or "")
        if not span or not span_in_message(span, raw_text):
            continue
        rows.append({
            "class": str(mention.get("class") or "other"),
            "span": span,
            "speaker": str(mention.get("speaker") or "other"),
            "polarity": str(mention.get("polarity") or "affirmative"),
            "is_question": bool(mention.get("is_question")),
        })
    return rows


def _apply_model_offer(provenance: dict[str, Any], shadow: dict[str, Any]) -> None:
    """Record which candidate the model calls the supplier's own offer.

    The model is a second opinion. It never replaces the rule reader's
    pre-selection: when the rules pre-selected nothing, the model's candidate
    fills the gap; when they pre-selected another figure, the disagreement is
    shown. A person confirms either way, and the model can only point at one
    of the rule reader's own candidates, never add a number.
    """
    raw_offer = shadow.get("model_offer")
    offer: dict[str, Any] = raw_offer if isinstance(raw_offer, dict) else {}
    token = str(offer.get("token") or "")
    candidates = [str(item) for item in provenance.get("candidates") or []]
    if not token or token not in candidates:
        provenance["model_offer_token"] = None
        provenance["model_offer_span"] = None
        provenance["model_disagrees"] = False
        return
    provenance["model_offer_token"] = token
    provenance["model_offer_span"] = str(offer.get("span") or "")
    rules_pick = provenance.get("suggested_token")
    if not rules_pick:
        provenance["suggested_token"] = token
        provenance["suggested_by"] = "model"
        provenance["model_disagrees"] = False
        return
    provenance["model_disagrees"] = str(rules_pick) != token


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
    trusted_channel: bool = True,
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
    if shadow.get("mentions"):
        provenance["extractor_mentions"] = _mention_trace(raw_text, shadow)
        _apply_model_offer(provenance, shadow)
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
    provenance.update(classify_origin(raw_text, provenance_extra, trusted_channel=trusted_channel))
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
    # Lazy import: the Gmail drafting of replies belongs to the send path in
    # service.py; importing it lazily here keeps the module graph acyclic
    # (service imports ingest, never the other way round at import time).
    from src.desk.service import _maybe_gmail_drafts

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
        provenance["extractor_mentions"] = _mention_trace(str(event.get("raw_text") or ""), shadow)
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
    provenance["extractor_mentions"] = _mention_trace(str(event.get("raw_text") or ""), shadow)
    _apply_model_offer(provenance, shadow)
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

def urllib_opener():
    import urllib.request

    return urllib.request.build_opener()

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
    trusted_channel: bool = False,
) -> dict[str, Any]:
    counts: dict[str, Any] = {"ingested": 0, "review": 0, "duplicates": 0, "ignored": 0, "poison": 0}
    for item in items:
        external = item.get("external_id") or _external_id(item)
        # [SECURITY 2026-10-24] The mailbox layer marks an unparseable message
        # poison. It is recorded as seen for every current deal — so it can
        # never reappear at the head of the window and stall the pipeline —
        # but nothing of it is ingested, because there is nothing trustworthy
        # in it to store.
        if item.get("poison"):
            counts["poison"] += 1
            if mailbox_key:
                for deal in [deal_id] if deal_id else [row["id"] for row in store.list_deals()]:
                    store.remember_mailbox(_seen_key(mailbox_key, deal), external)
            continue
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
                trusted_channel=trusted_channel,
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
    # [SECURITY 2026-10-24] The fixture marker is honoured only from a
    # mailbox this process controls: the loopback, non-TLS fixture server
    # (IMAP_SSL=0 is refused for any other host, see _open_imap). A real
    # remote mailbox is sender-controlled, so a typed "X-TradeFlow-Fixture"
    # line there stays a live quote and the human-confirm gate applies.
    trusted_fixture_channel = (not bool(settings["use_ssl"])) and mailbox.loopback(
        str(settings["host"])
    )
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
        trusted_channel=trusted_fixture_channel,
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
