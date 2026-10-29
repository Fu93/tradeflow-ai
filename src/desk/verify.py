"""The send gate: every predicate a draft must pass before mail leaves.

Why this module exists
----------------------
The desk's central safety property is that no number reaches a supplier unless a
person approved *that* number. Enforcing it took roughly 600 lines of
predicates — re-derive the decision from the chain, check the currency agrees
across three sources, check the body is the rendered draft, check the ask still
matches what a human confirmed, check the transmission envelope differs from
anything already sent — and all of it lived in the middle of ``service.py``,
between the extractor pool and the mailbox poller.

That is the wrong place for the one property the product sells. A module of
2,200 lines that also polls IMAP is a module where a merge conflict in the
poller can silently regress the gate, and where a reviewer cannot see the gate
as a single object. It now has its own file, its own dependency surface, and its
own tests.

Dependency direction: ``verify`` imports ``errors``, ``snapshot``, ``store``,
``record``, ``recompute``, ``decide`` and the engine's landed-cost and reserve
types. It does **not** import ``service``; ``service`` imports it. The gate
cannot be bypassed by a caller that does not load the service layer, because the
service layer is what calls it.

The functions are kept at module level with their original names. They are
private by convention (leading underscore) and ``service.py`` re-exports the two
that its own callers use, so every existing import path is unchanged.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from src.config.heuristics import DEAL_CURRENCIES, DEAL_CURRENCY, OPT_TOL
from src.desk import recompute
from src.desk.errors import DraftStale, SendRefused
from src.desk.parse import _confirm_span, _visible_text, parse_quote, price_from_token
from src.desk.typed import words_state
from src.desk.snapshot import publish
from src.desk.store import DeskStore
from src.engine.decision import within_risk_cap
from src.desk.transmission import (
    chained_supplier,
    counter_subject,
    reply_headers,
    transmission_digest,
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
    # 美元 and 欧元 end in 元, so they are read before the yuan.
    if "美元" in body:
        return "USD"
    if "欧元" in body or "歐元" in body:
        return "EUR"
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


def typed_review(review: dict[str, Any]) -> bool:
    """A price a person typed (for example from 28塊5), not a digit string read from the message."""
    return review.get("typed") is True and not review.get("token")


def _typed_binds_ok(raw_text: str, review: dict[str, Any], ask: float, ingested_hash: str) -> bool:
    """A typed price binds to the message by the words it was read from.

    The raw message is the one ingested and chained, the chained words still
    stand in it verbatim, those words state the typed amount (28塊5 states
    28.50, so 5 is refused), the amount is positive, and it is the ask being
    priced. It is labelled as typed everywhere.
    """
    raw_digest = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    words = str(review.get("span") or "").strip()
    value = review.get("value")
    if value is None or not words or float(value) <= 0:
        return False
    within = float(OPT_TOL) * max(1.0, abs(float(value)), abs(float(ask)))
    stands = words in _visible_text(raw_text)
    priced = abs(float(ask) - float(value)) <= within
    per_piece = str(review.get("unit") or "") == "per_piece"
    chained = str(review.get("raw_sha256") or "")
    stated = words_state(words, float(value), within)
    return raw_digest == ingested_hash and raw_digest == chained and stands and stated and priced and per_piece


def _review_binds_ok(raw_text: str, review: dict[str, Any], ask: float, ingested_hash: str) -> bool:
    """The raw message, the re-derived price, and the chained review are one confirmation."""
    if typed_review(review):
        return _typed_binds_ok(raw_text, review, ask, ingested_hash)
    digest = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    chained_raw = str(review.get("raw_sha256") or "")
    token = str(review.get("token") or "")
    currency = str(review.get("currency") or "")
    derived = price_from_token(raw_text, token, deal_currency=currency or None)
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
    if typed_review(review):
        # A typed price stands while its words are still in the message; the
        # bind above already required that, and the amount is the chained one.
        words = str(review.get("span") or "").strip()
        value = review.get("value")
        if not words or value is None or words not in _visible_text(raw_text):
            return None
        return float(value)
    token = str(review.get("token") or "")
    derived = price_from_token(raw_text, token, deal_currency=currency or None)
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
    from src.desk.recompute import _landed_consistent_rivals, decision_for_supplier, quoting_supplier_ids
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
    from src.desk.decide import issued_offer, offer_modes

    mode = offer_modes(entries).get(supplier_id, "default")
    fresh, _how = issued_offer(choice, float(ceiling), float(asks[supplier_id]), mode)
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
    if str(record.get("offer_mode") or "default") == "push":
        from src.desk.decide import issued_offer

        pushed, how = issued_offer(choice, float(record["ceiling"]), float(record["ask"]), "push")
        if how != "push_lower" or pushed is None:
            raise SendRefused("The record carries the push-lower figure, but a fresh decision withholds the push.")
        fresh = float(pushed)
    else:
        from src.desk.decide import issued_offer

        default, _relation = issued_offer(choice, float(record["ceiling"]), float(record["ask"]), "default")
        if default is None:
            raise SendRefused("No whole-cent counter fits the price and risk limits.")
        fresh = float(default)
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
    if record.get("risk_cap") is None or not within_risk_cap(
        float(ask), float(counter), float(record["risk_cap"]),
        intercept=record.get("hazard_intercept"), gap_coef=record.get("hazard_gap"),
    ):
        raise SendRefused("The issued counter exceeds the walk-away risk limit.")
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
