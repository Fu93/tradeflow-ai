"""Persist a decision record and the draft that quotes it.

The counter is already chosen. This module does not search for a price.
"""
from __future__ import annotations

from typing import Any, Callable

from src.config.heuristics import DEAL_CURRENCY
from src.desk.store import DeskStore


def _language(texts: list[str]) -> str:
    cjk = 0
    latin = 0
    for text in texts:
        for char in text:
            if "\u4e00" <= char <= "\u9fff":
                cjk += 1
            elif ("A" <= char <= "Z") or ("a" <= char <= "z"):
                latin += 1
    return "zh" if cjk > latin else "en"


def _references(prior: str | None, message_id: str) -> str:
    parts: list[str] = []
    for token in str(prior or "").split():
        if token and token not in parts:
            parts.append(token)
    if message_id and message_id not in parts:
        parts.append(message_id)
    return " ".join(parts)


def draft_body(product: str, counter: float, quantity: int, language: str, currency: str) -> str:
    amount = f"{counter:.2f}"
    if language == "zh" and currency == DEAL_CURRENCY:
        return (
            f"您好，关于{product}，我们可以按每件 {amount} 元、"
            f"数量 {quantity} 继续。请确认是否可以安排。"
        )
    return (
        f"Hello, for {product} we can proceed at {amount} {currency} per unit "
        f"for {quantity} units. Please confirm if that works."
    )


def _walked_away(outcomes: list[dict[str, Any]]) -> bool:
    return any(item.get("outcome") == "walk_away" for item in outcomes)


def project_open_drafts(
    store: DeskStore,
    deal: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    by_supplier: dict[str, list[dict[str, Any]]],
    beliefs_state: dict[str, Any],
    decision_events: Callable[[str], list[dict[str, Any]]],
    unconfirmed: int,
) -> None:
    """Append one decision row and replace the open draft for each issued counter."""
    from src.desk.record import build_record

    for card in cards:
        walked = _walked_away(store.outcomes(deal["id"], card["id"]))
        if card["latest_quote"] is None or card["counter"] is None or walked:
            store.supersede_open_drafts(deal["id"], card["id"])
            if walked:
                card["counter"] = None
                card["push_counter"] = None
                card["blocked"] = True
                card["counter_relation"] = "withheld"
            continue
        history = by_supplier.get(card["id"]) or []
        language = _language([event.get("raw_text") or "" for event in history])
        currency = str(deal.get("currency") or DEAL_CURRENCY)
        slot = beliefs_state[card["id"]]
        state = slot.get("state")
        inputs = dict(card.get("decision_inputs") or {})
        usable_events = decision_events(card["id"])
        stored_inputs = (
            inputs.get("risk_cap") is not None and inputs.get("reference_ask") is not None
        )
        record = build_record(
            deal_id=deal["id"],
            supplier_id=card["id"],
            currency=currency,
            events=usable_events,
            ask=float(card["latest_quote"]),
            ceiling=float(deal["ceiling_price"]),
            offer=float(card["counter"]),
            counter=float(card["counter"]),
            push_counter=card.get("push_counter"),
            half=card.get("sensitivity_half"),
            double=card.get("sensitivity_double"),
            prior_alpha=float(inputs["prior_alpha"]) if stored_inputs else (
                None if state is None else float(state.alpha)
            ),
            prior_beta=float(inputs["prior_beta"]) if stored_inputs else (
                None if state is None else float(state.beta)
            ),
            prior_n=int(inputs.get("prior_n") or 0) if stored_inputs else (
                0 if state is None else int(state.n_responses)
            ),
            prior_source="" if state is None else state.source,
            unconfirmed=unconfirmed,
            hazard_within_cap=not bool(card.get("blocked")),
            rival_asks=tuple(float(item) for item in (inputs.get("rival_asks") or ())),
            risk_cap=None if inputs.get("risk_cap") is None else float(inputs["risk_cap"]),
            reserve_upper=None if inputs.get("reserve_upper") is None else float(inputs["reserve_upper"]),
            reject_ratios=tuple(float(item) for item in (inputs.get("reject_ratios") or ())),
            reference_ask=None if inputs.get("reference_ask") is None else float(inputs["reference_ask"]),
            hazard_intercept=inputs.get("hazard_intercept"),
            hazard_gap=inputs.get("hazard_gap"),
            held_previous_counter=float(card["counter"]) if card.get("counter_held") else None,
            inputs_stored=stored_inputs,
        )
        if record.counter is None:
            card["counter"] = None
            card["push_counter"] = None
            card["counter_relation"] = "withheld"
            store.supersede_open_drafts(deal["id"], card["id"])
            continue
        payload = record.as_dict()
        decision_id = store.append_decision(payload, record.sha256())
        store.append_chain(
            deal["id"],
            "decision",
            {"decision_id": decision_id, "record_sha256": record.sha256(), "supplier_id": card["id"]},
        )
        card["decision_id"] = decision_id
        body = draft_body(
            str(deal["product"]),
            float(record.counter),
            int(deal["quantity"]),
            language,
            currency,
        )
        reply_to = None
        references = None
        for event in reversed(history):
            message_id = (event.get("provenance") or {}).get("message_id")
            if message_id:
                reply_to = str(message_id)
                references = _references(
                    (event.get("provenance") or {}).get("references"),
                    str(message_id),
                )
                break
        draft_row = store.replace_open_draft(
            deal["id"],
            card["id"],
            body,
            reply_to=reply_to,
            references_header=references,
            decision_id=decision_id,
        )
        store.append_chain(
            deal["id"],
            "draft_created",
            {
                "decision_id": decision_id,
                "draft_id": draft_row["id"],
                "body_sha256": draft_row.get("body_sha256"),
            },
        )
