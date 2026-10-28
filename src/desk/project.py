"""Persist a decision record and the draft that quotes it.

The counter is already chosen. This module does not search for a price.
"""
from __future__ import annotations

from typing import Any, Callable

from src.config.heuristics import DEAL_CURRENCY
from src.desk.store import DeskStore
from src.desk.transmission import join_references, reply_headers


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
    return join_references(prior, message_id)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def product_for_language(product: str, language: str) -> str:
    """The product name the draft uses, in the draft's language.

    A product typed as "折叠伞 / folding umbrella" carries both names. A Chinese
    draft takes the part with Chinese characters, an English draft the other.
    A product with one name is used as typed.
    """
    text = str(product or "").strip()
    for separator in (" / ", "／"):
        if separator in text:
            parts = [part.strip() for part in text.split(separator) if part.strip()]
            chinese = [part for part in parts if _has_cjk(part)]
            other = [part for part in parts if not _has_cjk(part)]
            if language == "zh" and chinese:
                return chinese[0]
            if language != "zh" and other:
                return other[0]
            return parts[0] if parts else text
    return text


def draft_body(product: str, counter: float, quantity: int, language: str, currency: str) -> str:
    product = product_for_language(product, language)
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
    """A walk-away drops the supplier until a later human re-open."""
    from src.desk.recompute import _walked_away as ordered

    return ordered(outcomes)


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
            # The effective figure: a chosen push that the solver withholds is
            # issued, and recorded, as the default.
            offer_mode="push" if card.get("counter_relation") == "push_lower" else "default",
        )
        if record.counter is None:
            card["counter"] = None
            card["push_counter"] = None
            card["counter_relation"] = "withheld"
            store.supersede_open_drafts(deal["id"], card["id"])
            continue
        digest = record.sha256()
        chain = store.verify_chain(deal["id"])
        prior = [
            entry for entry in (chain.get("entries") or [])
            if entry.get("type") == "decision"
            and str((entry.get("payload") or {}).get("supplier_id") or "") == str(card["id"])
        ]
        previous = (prior[-1].get("payload") or {}) if prior else {}
        previous_constants = None
        if previous.get("decision_id"):
            stored_decision = store.get_decision(str(previous["decision_id"]))
            if stored_decision is not None:
                previous_constants = (stored_decision.get("record") or {}).get("constants")
        constants_moved = previous_constants is not None and previous_constants != record.constants
        if str(previous.get("record_sha256") or "") == digest and previous.get("decision_id") and not constants_moved:
            decision_id = str(previous["decision_id"])
        else:
            decision_id = store.append_decision(record.as_dict(), digest)
            store.append_chain(
                deal["id"],
                "decision",
                {"decision_id": decision_id, "record_sha256": digest, "supplier_id": card["id"]},
            )
        card["decision_id"] = decision_id
        body = draft_body(
            str(deal["product"]),
            float(record.counter),
            int(deal["quantity"]),
            language,
            currency,
        )
        chain = store.verify_chain(deal["id"])
        reply_to, references = reply_headers(list(chain.get("entries") or []), str(card["id"]))
        draft_row = store.replace_open_draft(
            deal["id"],
            card["id"],
            body,
            reply_to=reply_to or None,
            references_header=references or None,
            decision_id=decision_id,
            require_new=constants_moved,
        )
        store.append_chain(
            deal["id"],
            "draft_created",
            {
                "decision_id": decision_id,
                "draft_id": draft_row["id"],
                "body_sha256": draft_row.get("body_sha256"),
                "reply_to": reply_to,
                "references": references,
            },
        )
