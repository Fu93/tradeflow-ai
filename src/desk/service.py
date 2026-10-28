"""Desk actions. Ingest never sends. Sending is a separate, explicit call."""
from __future__ import annotations

import hashlib
import logging
import os
from email.message import EmailMessage
from typing import Any

from src.config.heuristics import (
    BREAKDOWN_TRIP_THRESHOLD,
    DEAL_CURRENCIES,
    DEAL_CURRENCY,
    DEAL_MAX_SUPPLIERS,
    DEAL_MIN_SUPPLIERS,
    OPT_TOL,
    RISK_TOLERANCE_FLOOR,
    RISK_TOLERANCE_MAX,
)
from src.desk import mailbox, recompute
from src.desk.approval_signing import (
    address_change_payload,
    address_change_policy,
    approval_is_pinned,
    approval_policy,
    required_approval_pubkey,
    signed_payload,
    verify_address_change,
    verify_signed_approval,
)
from src.desk.audit_chain import approval_recorded, chain_key_configured, export_history, reanchor_refusal
from src.desk.input_checks import EMAIL_CHANNEL, require_address, require_contact, require_plain
from src.desk.names import supplier_name_key
from src.desk.parse import _confirm_span, _visible_text, parse_quote, price_from_token
from src.desk.bus import BUS  # noqa: F401  (re-exported for existing callers)
from src.desk.bus import DeskBus  # noqa: F401  (re-exported for existing callers)
from src.desk.errors import DraftStale, SendRefused
from src.desk.snapshot import publish
from src.desk.typed import amounts_in_words, words_state
from src.desk.store import DeskRejected, DeskStore, DeskUnavailable
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

# ---- [ARCHITECTURE 2026-10-24] Intake + polling moved to src/desk/ingest.py ----
# Re-exported so every historical import path (tests, scripts, main's
# lifespan) keeps resolving; the intake pipeline lives in one module now.
from src.desk.ingest import (  # noqa: F401
    SOURCES,
    Sender,
    _DEAL_LOCKS,
    _DEAL_LOCKS_GUARD,
    _EXTRACTOR_POOL,
    _EXTRACTOR_POOL_GUARD,
    _EXTRACTOR_THREADS,
    _EXTRACTOR_THREADS_GUARD,
    _ExtractorJob,
    _address_key,
    _apply_extractor_result,
    _external_id,
    _extractor_pool,
    _failed_shadow,
    _ingest_locked,
    _match_supplier,
    _outside_sanity,
    _prepare_reading,
    _quantity_token,
    _route_deals,
    _schedule_extractor,
    _seen_key,
    _skip_for_poll,
    classify_origin,
    deal_lock,
    ingest_mailbox_items,
    ingest_message,
    poll_configured,
    poll_forever,
    poll_gmail_once,
    poll_imap_once,
    poll_seconds,
    polling_enabled,
    settle_extractor,
    shutdown_extractors,
    urllib_opener,
)

logger = logging.getLogger("tradeflow.desk")

OUTCOMES = {"continue", "walk_away", "agreed"}
EVENT_TRANSITIONS = {
    "review": {"accepted", "rejected"},
    "accepted": set(),
    "rejected": set(),
}


















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
    channels = [str(item.get("channel") or EMAIL_CHANNEL).strip().lower() or EMAIL_CHANNEL for item in suppliers]
    addresses = [require_contact(item["address"], channel) for item, channel in zip(suppliers, channels, strict=True)]
    if not int(DEAL_MIN_SUPPLIERS) <= len(suppliers) <= int(DEAL_MAX_SUPPLIERS):
        raise DeskRejected(
            f"A deal needs {DEAL_MIN_SUPPLIERS} or {DEAL_MAX_SUPPLIERS} suppliers, one per line; "
            f"this has {len(suppliers)}."
        )
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
                "channel": (item.get("channel") or EMAIL_CHANNEL).strip().lower() or EMAIL_CHANNEL,
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
























def accept_review(
    store: DeskStore,
    deal_id: str,
    event_id: str,
    supplier_id: str | None,
    *,
    chosen_token: str | None = None,
    reject: bool = False,
    typed_amount: float | None = None,
    typed_words: str | None = None,
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
        typed = typed_amount is not None
        words = ""
        if typed and chosen_token:
            raise DeskRejected("Pick a figure from the message or type one, not both.")
        if typed:
            # A person typed the price (for example 28塊5 -> 28.50) and names
            # the words they read it from. Code cannot re-derive it, so it is
            # recorded as typed and those words must stand in the message.
            words = str(typed_words or "").strip()
            if not words or words not in _visible_text(str(event["raw_text"] or "")):
                raise DeskRejected("Name the words in the message the typed price comes from.")
            try:
                price = float(typed_amount)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise DeskRejected("The typed price is not a number.") from exc
            if not price > 0 or price != price:
                raise DeskRejected("The typed price must be above zero.")
            stated = sorted(amounts_in_words(words))
            if not words_state(words, price, float(OPT_TOL) * max(1.0, abs(price))):
                said = ", ".join(f"{value:.2f}" for value in stated) or "no amount"
                raise DeskRejected(f"The typed price is not the amount in those words (they read as {said}).")
        else:
            if not chosen_token:
                raise DeskRejected("Accept confirms only the amount shown from the message.")
            if _quantity_token(event["raw_text"], chosen_token):
                raise DeskRejected("That amount is a quantity, not a unit price.")
            read = price_from_token(event["raw_text"], chosen_token, deal_currency=currency)
            if read is None:
                raise DeskRejected("That amount is not a unit price in the deal currency.")
            price = float(read)
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
        if typed:
            provenance["typed_by_person"] = True
            provenance["typed_words"] = words
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
                "token": None if typed else chosen_token,
                "typed": typed,
                "value": float(price),
                "currency": currency,
                "unit": "per_piece",
                "span": words if typed else _confirm_span(_visible_text(raw_text), str(chosen_token)),
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
    address_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A new email is sent only after a person confirms it on the chain.

    [SECURITY 2026-10-23] The boolean is not the control any more. A supplier
    address change redirects where the next counter is sent, and this desk has
    one shared API key, so ``human_confirmed: true`` was reachable by anyone
    holding that key — the business-email-compromise path. When the policy
    requires it (a pinned approval key, or production), a valid signature over
    ``{deal_id, supplier_id, address}`` from the pinned key must accompany the
    change, and the signature is recorded inside the chain entry so it is
    tamper-evident rather than merely checked once at the door.
    """
    if human_confirmed is not True:
        raise DeskRejected("A person has to confirm this address.")
    cleaned = require_address(address)
    if not cleaned:
        raise DeskRejected("Each supplier needs an address.")
    payload = address_change_payload(deal_id, supplier_id, cleaned)
    recorded_change = None
    if address_change_policy() == "required":
        if not verify_address_change(address_change, payload):
            raise DeskRejected(
                "A supplier address change needs a signed confirmation from the "
                "pinned approval key."
            )
        # verify_address_change has already established this is a dict with the
        # three fields present and the right shape.
        assert isinstance(address_change, dict)
        recorded_change = {
            "alg": str(address_change.get("alg") or ""),
            "pubkey": str(address_change.get("pubkey") or ""),
            "sig": str(address_change.get("sig") or ""),
        }
    elif address_change is not None and not verify_address_change(address_change, payload):
        # Optional policy, but a bad signature is still a refusal: a caller who
        # offered a signature meant to be held to it.
        raise DeskRejected("The supplied address-change signature is not valid.")
    with deal_lock(deal_id):
        supplier = store.supplier(deal_id, supplier_id)
        if supplier is None:
            raise KeyError(supplier_id)
        new_name = (name or str(supplier["name"])).strip()
        store.update_supplier(supplier_id, name=new_name, address=cleaned)
        entry: dict[str, Any] = {
            "supplier_id": supplier_id,
            "name": new_name,
            "address": cleaned,
            "confirmed_by": "human",
        }
        if recorded_change is not None:
            entry["address_change"] = recorded_change
        store.append_chain(deal_id, "supplier_address", entry)
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


def set_offer_mode(
    store: DeskStore,
    deal_id: str,
    supplier_id: str,
    mode: str,
    *,
    human_confirmed: bool,
) -> dict[str, Any]:
    """A person chooses which figure this supplier's draft carries.

    "default" is the best price already on the table; "push" is the solver's
    push-lower counter. The choice is a chained human event, so the send guard
    re-derives the same figure from the chain before anything is sent.
    """
    from src.desk.decide import OFFER_MODES

    if human_confirmed is not True:
        raise DeskRejected("A person has to choose the figure for a draft.")
    if mode not in OFFER_MODES:
        raise DeskRejected("The offer mode is default or push.")
    with deal_lock(deal_id):
        if store.get_deal(deal_id) is None or store.supplier(deal_id, supplier_id) is None:
            raise KeyError(supplier_id)
        store.append_chain(
            deal_id,
            "offer_mode",
            {"supplier_id": supplier_id, "mode": mode, "confirmed_by": "human"},
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
        # A supplier reached in a chat app is never mailed: the approved text
        # is copied and sent by a person, so the draft stays "approved".
        by_hand = str(supplier.get("channel") or EMAIL_CHANNEL) != EMAIL_CHANNEL
        if by_hand or not smtp_enabled():
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


























