"""Replay each supplier's accepted quotes, then choose one offer per supplier."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src.config.heuristics import (
    BAYES_OBS_NOISE,
    DEAL_CURRENCY,
    DESK_N_GRID,
    DESK_N_SAMPLES,
    HAZARD_MIN_EVENTS,
    HAZARD_SUPPLIER_TAU,
    OPT_TOL,
    UNIT_SANITY_MULTIPLE,
)
from src.desk.store import DeskStore
from src.desk.headline import audit_line, confirmed_prices, saving_vs_best_first
from src.desk.shadow import progress as shadow_progress
from src.desk.decide import decide_supplier
from src.engine.decision import HEURISTIC_NOTE
from src.engine.hazard_fit import NOTE as HAZARD_TRANSFER_NOTE
from src.engine.hazard_fit import baseline_rates, load_hazard_fit
from src.engine.landed import landed_unit_cost, ranking_basis
from src.engine.reserve import ReserveState

HAZARD_NOTE = (
    "Walk-away hazard is transferred from the eBay seller-ended fit. The fitted "
    "curve has a small-gap hinge that peaks at a zero gap, so the risk cap uses "
    "the gap slope with that hinge held at zero and a tighter cap can withhold "
    "a push. The published coefficients are unchanged. The fit is the first buyer "
    "offer of a thread and is applied per round to the latest ask. A reserve-gap "
    "term is not added. Recorded continue and walk-away outcomes update only this "
    "supplier's effect. Quote text is not an outcome. This is not a measured "
    "Chinese-factory rate."
)
KAPPA_NOTE = (
    "Missing freight, duty, or fees are not treated as zero. "
    "Ranking uses the unit price."
)
LANDED_NOTE = "Ranking uses landed cost."


def solver_budget() -> tuple[int, int]:
    samples = _bounded_int(
        os.environ.get("DESK_N_SAMPLES", str(DESK_N_SAMPLES)),
        default=int(DESK_N_SAMPLES),
        low=1,
        high=20000,
    )
    grid = _bounded_int(
        os.environ.get("DESK_N_GRID", str(DESK_N_GRID)),
        default=int(DESK_N_GRID),
        low=2,
        high=40,
    )
    return samples, grid


def _bounded_int(raw: str | None, *, default: int, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value < low:
        return low
    if value > high:
        return high
    return value


def _std_or_tiny(value: float) -> float:
    number = float(value)
    if number == 0.0:
        return float(np.finfo(np.float64).tiny)
    return number


def _components(parsed: dict[str, Any], deal: dict[str, Any]) -> dict[str, Any]:
    """Landed cost in the deal currency. A USD deal does not drop USD freight."""
    base = str(deal.get("currency") or DEAL_CURRENCY)
    return landed_unit_cost(
        unit_price=parsed.get("unit_price"),
        freight=parsed.get("freight"),
        duty_rate=parsed.get("duty_rate"),
        fees=parsed.get("fees"),
        currency=parsed.get("currency") or base,
        base_currency=base,
    )


def _optional(deal: dict[str, Any], key: str) -> float | None:
    value = deal.get(key)
    if value is None:
        return None
    return float(value)


def _unverified(event: dict[str, Any]) -> bool:
    provenance = event.get("provenance") or {}
    return bool(provenance.get("simulated")) or provenance.get("origin") in {"simulated", "fixture"}


def _priced_live(event: dict[str, Any]) -> bool:
    parsed = event.get("parsed") or {}
    return event.get("status") == "accepted" and parsed.get("unit_price") is not None


def _sticky_origin(history: list[dict[str, Any]]) -> str:
    """Fixture and simulated stick until a later accepted live price."""
    origin = "live"
    for event in history:
        label = _origin(event)
        if label in {"fixture", "simulated"}:
            origin = label
        elif label == "live" and _priced_live(event):
            origin = "live"
    return origin


def _origin(event: dict[str, Any] | None) -> str:
    if event is None:
        return "live"
    provenance = event.get("provenance") or {}
    origin = provenance.get("origin")
    if origin in {"fixture", "simulated", "live"}:
        return str(origin)
    if provenance.get("simulated"):
        return "simulated"
    if provenance.get("fixture"):
        return "fixture"
    return "live"


def _hold_previous(row: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return row
    old_ids = list(previous.get("accepted_event_ids") or [])
    new_ids = list(row.get("accepted_event_ids") or [])
    old_counter = previous.get("counter")
    new_counter = row.get("counter")
    if old_ids != new_ids or old_counter is None or new_counter is None:
        return row
    if float(new_counter) > float(old_counter):
        row["counter"] = old_counter
        row["blocked"] = previous.get("blocked")
        row["hazard"] = previous.get("hazard")
        row["hazard_upper"] = previous.get("hazard_upper")
        row["hazard_figure"] = previous.get("hazard_figure")
        row["counter_held"] = True
    return row


def _headline(
    events: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    unconfirmed: int,
    deal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terms = deal or {}
    confirmed, _used = confirmed_prices(events)
    live = [event for event in confirmed if not _unverified(event)]
    first: dict[str, float] = {}
    basis: dict[str, str] = {}
    adder: dict[str, float] = {}
    for event in live:
        supplier_id = event.get("supplier_id")
        if not supplier_id or str(supplier_id) in first:
            continue
        parsed = event.get("parsed") or {}
        landed = _components(parsed, terms)
        unit = parsed.get("unit_price")
        if landed.get("status") == "complete" and landed.get("landed_cost") is not None and unit is not None:
            first[str(supplier_id)] = float(landed["landed_cost"])
            basis[str(supplier_id)] = "landed"
            adder[str(supplier_id)] = float(landed["landed_cost"]) - float(unit)
        elif unit is not None:
            first[str(supplier_id)] = float(unit)
            basis[str(supplier_id)] = "unit"
    agreed_rows = [
        item for item in outcomes
        if item.get("outcome") == "agreed" and item.get("agreed_price") is not None
    ]
    agreed_landed = None
    if agreed_rows and first:
        last = agreed_rows[-1]
        unit_agreed = float(last["agreed_price"])
        supplier_id = str(last.get("supplier_id") or "")
        uses_landed = any(kind == "landed" for kind in basis.values())
        if uses_landed:
            if supplier_id in adder:
                agreed_landed = unit_agreed + adder[supplier_id]
        elif basis.get(supplier_id) == "unit" or (not supplier_id and all(kind == "unit" for kind in basis.values())):
            agreed_landed = unit_agreed
    simulated = any(_unverified(event) for event in confirmed) or not live
    return saving_vs_best_first(
        list(first.values()),
        agreed_landed,
        simulated=simulated,
        unconfirmed=unconfirmed,
    )


def _reserve_state(
    prices: list[float],
    outcomes: list[dict[str, Any]],
    *,
    prior_mean: float | None = None,
    prior_std: float | None = None,
) -> ReserveState | None:
    """Own-ask Beta. D is a fraction of the first ask. Later prices are upper bounds."""
    if not prices:
        return None
    if prior_mean is None:
        state = ReserveState.policy()
    else:
        state = ReserveState.from_moments(float(prior_mean), prior_std)
    first = float(prices[0])
    if first <= 0.0:
        return None
    for later in prices[1:]:
        state = state.tighten(float(later) / first)
    for outcome in outcomes:
        kind = outcome.get("outcome")
        if kind == "agreed" and outcome.get("agreed_price") is not None:
            state = state.tighten(float(outcome["agreed_price"]) / first)
        elif kind == "walk_away" and outcome.get("offer") is not None:
            state = state.add_rejection(float(outcome["offer"]) / first)
        elif kind == "continue":
            state = state.record()
    return state


def _walked_away(outcomes: list[dict[str, Any]]) -> bool:
    return any(item.get("outcome") == "walk_away" for item in outcomes)


def recompute(store: DeskStore, deal_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    deal = store.get_deal(deal_id)
    if deal is None:
        raise KeyError(deal_id)
    previous = store.get_snapshot(deal_id) or {}
    previous_rows = {row["id"]: row for row in previous.get("suppliers") or []}
    suppliers = store.suppliers(deal_id)
    events = store.events(deal_id)
    by_supplier: dict[str, list[dict[str, Any]]] = {row["id"]: [] for row in suppliers}
    for event in events:
        if event.get("supplier_id"):
            by_supplier.setdefault(event["supplier_id"], []).append(event)

    target = float(deal["target_price"])
    ceiling = float(deal["ceiling_price"])
    include_unverified = bool(deal.get("include_unverified"))
    markup = _optional(deal, "expected_markup")
    user_prior = _optional(deal, "reserve_prior_mean")
    user_std = _optional(deal, "reserve_prior_std")
    noise = _optional(deal, "bayes_obs_noise")
    noise_floor = float(BAYES_OBS_NOISE if noise is None else noise)

    def decision_events(supplier_id: str) -> list[dict[str, Any]]:
        rows = [
            event for event in (by_supplier.get(supplier_id) or [])
            if event.get("status") == "accepted"
        ]
        live = [event for event in rows if (event.get("provenance") or {}).get("origin") != "simulated"]
        live = [event for event in live if not (event.get("provenance") or {}).get("simulated")]
        if include_unverified:
            return live
        return [event for event in live if not _unverified(event)]

    consumed: list[dict[str, Any]] = []
    for row in suppliers:
        consumed.extend(decision_events(row["id"]))
    _kept, unconfirmed = confirmed_prices(consumed)
    beliefs_state: dict[str, dict[str, Any]] = {}
    for row in suppliers:
        history = decision_events(row["id"])
        outcomes = store.outcomes(deal_id, row["id"])
        prices = [
            float(event["parsed"]["unit_price"])
            for event in history
            if event.get("parsed", {}).get("unit_price") is not None
        ]
        state = _reserve_state(
            prices,
            outcomes,
            prior_mean=user_prior,
            prior_std=user_std,
        )
        if state is None or not state.consistent() or _walked_away(outcomes):
            note = "No accepted quote yet."
            if state is not None and not state.consistent():
                note = "The rejection lower bound meets or passes the upper bound, so no price is issued."
            elif _walked_away(outcomes):
                note = "This supplier walked away, so no new counter is issued."
            beliefs_state[row["id"]] = {
                "mean": None,
                "std": None,
                "observations": 0,
                "label": "withheld" if state is not None else "no data",
                "state": None,
                "note": note,
                "prior_mean": None if state is None else state.alpha / (state.alpha + state.beta),
                "prior_strength": None if state is None else state.alpha + state.beta,
                "reference_ask": None if not prices else float(prices[0]),
            }
            continue
        reference = float(prices[0]) / target
        state = state.with_reference(reference)
        mean = reference * float(state.mean())
        std = _std_or_tiny(reference * float(state.std()))
        beliefs_state[row["id"]] = {
            "mean": mean,
            "std": std,
            "observations": len(prices),
            "label": state.source,
            "state": state,
            "note": state.note(),
            "prior_mean": state.alpha / (state.alpha + state.beta),
            "prior_strength": state.alpha + state.beta,
            "reference_ask": float(prices[0]),
        }
        store.set_belief(row["id"], mean, std)

    active = []
    if unconfirmed == 0:
        active = [
            row for row in suppliers
            if beliefs_state[row["id"]]["state"] is not None and decision_events(row["id"])
        ]
    # Reported for the desk, and not an input to the counter.
    samples, grid = solver_budget()
    choices: dict[str, Any] = {}
    landed_rows: list[dict[str, Any]] = []
    asks: dict[str, float] = {}
    if active:
        fit = load_hazard_fit()
        intercept = _optional(deal, "hazard_intercept")
        quote_gap = _optional(deal, "hazard_quote_gap")
        decision_intercept = None if intercept is None else float(intercept)
        decision_gap = None if quote_gap is None else float(quote_gap)
        for row in active:
            latest = decision_events(row["id"])[-1]
            asks[row["id"]] = float(latest["parsed"]["unit_price"])
            landed_rows.append(_components(latest["parsed"], deal))
            outcomes = store.outcomes(deal_id, row["id"])
            store.set_hazard_counts(
                row["id"],
                len(outcomes),
                sum(1 for item in outcomes if item.get("outcome") == "walk_away"),
            )
        basis = ranking_basis(landed_rows)
        for row in active:
            rivals = [price for other, price in asks.items() if other != row["id"]]
            slot = beliefs_state[row["id"]]
            state = slot["state"].with_reference(float(slot["reference_ask"]))
            choices[row["id"]] = decide_supplier(
                ask=asks[row["id"]],
                rivals=rivals,
                ceiling=ceiling,
                state=state,
                risk_cap=float(deal["risk_tolerance"]),
                fit=fit,
                intercept=decision_intercept,
                gap_coef=decision_gap,
            )
            beliefs_state[row["id"]]["decision_inputs"] = {
                "rival_asks": [float(item) for item in rivals],
                "risk_cap": float(deal["risk_tolerance"]),
                "reserve_upper": float(state.upper),
                "reject_ratios": [float(item) for item in state.reject_ratios],
                "reference_ask": float(slot["reference_ask"]),
                "hazard_intercept": decision_intercept,
                "hazard_gap": decision_gap,
                "prior_alpha": float(state.alpha),
                "prior_beta": float(state.beta),
                "prior_n": int(state.n_responses),
            }
    else:
        basis = "unit_price"

    cards = []
    for row in suppliers:
        history = by_supplier.get(row["id"]) or []
        usable = decision_events(row["id"])
        slot = beliefs_state[row["id"]]
        mean, std = slot["mean"], slot["std"]
        observations, label, supplier_note = slot["observations"], slot["label"], slot["note"]
        latest_any = history[-1] if history else None
        card: dict[str, Any] = {
            "id": row["id"],
            "name": row["name"],
            "address": row["address"],
            "channel": row["channel"],
            "latest_quote": None,
            "currency": None,
            "landed_cost": None,
            "landed_status": "no data",
            "belief_mean": mean,
            "belief_std": std,
            "belief_observations": observations,
            "belief_label": label,
            "reserve_note": supplier_note,
            "d_prior_mean": slot.get("prior_mean"),
            "d_prior_strength": slot.get("prior_strength"),
            "d_prior_label": "user policy",
            "reference_ask": slot.get("reference_ask"),
            "markup_used": None,
            "sensitivity_half": None,
            "sensitivity_double": None,
            "markup_identified": markup is not None,
            "origin": _sticky_origin(history),
            "simulated": _sticky_origin(history) == "simulated",
            "fixture": _sticky_origin(history) == "fixture",
            "excluded_from_decision": bool(history) and not usable and not include_unverified,
            "prior_driven": True,
            "counter": None,
            "push_counter": None,
            "push_note": "",
            "counter_relation": None,
            "blocked": False,
            "hazard": None,
            "hazard_upper": None,
            "hazard_figure": None,
            "rank": None,
            "accepted_event_ids": [
                event["id"] for event in history if event.get("status") == "accepted"
            ],
            "counter_held": False,
        }
        display = usable[-1] if usable else latest_any
        if display is not None and display.get("parsed", {}).get("unit_price") is not None:
            parsed = display["parsed"]
            card["latest_quote"] = parsed.get("unit_price")
            card["currency"] = str(deal.get("currency") or parsed.get("currency") or DEAL_CURRENCY)
            landed = _components(parsed, deal)
            card["landed_cost"] = landed.get("landed_cost")
            card["landed_status"] = landed.get("status") or "no data"
        if card["excluded_from_decision"]:
            card["rank"] = None
        elif row["id"] in choices:
            choice = choices[row["id"]]
            blocked = bool(choice.blocked)
            own_quote = float(card["latest_quote"])
            relation = str(choice.counter_relation)
            if blocked or relation == "withheld":
                counter = None
                figure = "upper"
                relation = "withheld"
            else:
                counter = min(float(choice.default_counter), ceiling, own_quote)
                figure = "mean"
                if abs(float(counter) - own_quote) <= float(OPT_TOL) * max(1.0, own_quote):
                    relation = "at_ask"
            withheld_push = bool(choice.push_withheld)
            card["counter"] = counter
            card["push_counter"] = None if blocked or withheld_push else min(
                float(choice.push_counter), ceiling, own_quote
            )
            card["push_note"] = choice.push_note or ""
            card["counter_relation"] = relation
            card["sensitivity_half"] = None if blocked else min(float(choice.push_half), ceiling, own_quote)
            card["sensitivity_double"] = None if blocked else min(float(choice.push_double), ceiling, own_quote)
            card["blocked"] = blocked
            card["hazard"] = float(choice.incremental_hazard)
            card["hazard_upper"] = float(choice.incremental_hazard)
            card["hazard_figure"] = figure
            card["prior_driven"] = True
            card["decision_inputs"] = beliefs_state[row["id"]].get("decision_inputs") or {}
            card = _hold_previous(card, previous_rows.get(row["id"]))
        cards.append(card)

    basis = ranking_basis(
        [
            {"status": card["landed_status"], "landed_cost": card["landed_cost"]}
            for card in cards
            if card["latest_quote"] is not None
        ]
    ) if any(card["latest_quote"] is not None for card in cards) else "unit_price"

    def sort_key(card: dict[str, Any]) -> tuple:
        if card["latest_quote"] is None or card.get("excluded_from_decision"):
            return (1, 0.0, card["name"], card["id"])
        if basis == "landed_cost" and card["landed_cost"] is not None:
            cost = float(card["landed_cost"])
        else:
            cost = float(card["latest_quote"])
        return (0, cost, card["name"], card["id"])

    ordered = sorted(cards, key=sort_key)
    rank = 0
    for card in ordered:
        if card["latest_quote"] is None or card.get("excluded_from_decision"):
            card["rank"] = None
            continue
        rank += 1
        card["rank"] = rank
    by_id = {card["id"]: card for card in cards}
    for card in cards:
        card["rank"] = by_id[card["id"]]["rank"]

    from src.desk.project import project_open_drafts

    project_open_drafts(
        store,
        deal,
        cards,
        by_supplier=by_supplier,
        beliefs_state=beliefs_state,
        decision_events=decision_events,
        unconfirmed=unconfirmed,
    )
    for card in cards:
        card.pop("decision_inputs", None)

    mc = None
    stop = False
    stop_reason = "no accepted quotes yet" if not choices else "one offer per supplier; no Monte Carlo"

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))

    def _public(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event["id"],
            "supplier_id": event.get("supplier_id"),
            "source": event["source"],
            "raw_text": event["raw_text"],
            "parsed": event["parsed"],
            "confidence": event["confidence"],
            "status": event["status"],
            "provenance": event["provenance"],
            "external_id": event.get("external_id"),
            "received_at": event["received_at"],
        }

    public_events = [_public(event) for event in reversed(events[-100:])]
    review = [_public(event) for event in reversed(events) if event["status"] == "review"]
    draft_rows = []
    for draft in store.drafts(deal_id)[:20]:
        draft_rows.append(
            {
                "id": draft["id"],
                "supplier_id": draft["supplier_id"],
                "body": draft["body"],
                "status": draft["status"],
                "human_approved": bool(draft["human_approved"]),
                "transmitted": bool(draft["transmitted"]),
                "created_at": draft["created_at"],
                "sent_at": draft["sent_at"],
                "gmail_draft_id": draft["gmail_draft_id"],
                "gmail_error": draft["gmail_error"],
                "version": int(draft.get("version") or 1),
                "body_sha256": draft.get("body_sha256"),
                "decision_id": draft.get("decision_id"),
                "send_error": draft.get("send_error"),
                "reply_to": draft.get("reply_to"),
                "message_id": draft.get("message_id"),
            }
        )
    fit = load_hazard_fit()
    user_intercept = _optional(deal, "hazard_intercept")
    user_gap = _optional(deal, "hazard_quote_gap")
    intercept = float(fit.intercept if user_intercept is None else user_intercept)
    tau = float(HAZARD_SUPPLIER_TAU if _optional(deal, "hazard_tau") is None else deal["hazard_tau"])
    quote_gap = float(fit.gap_coef if user_gap is None else user_gap)
    reserve_gap = _optional(deal, "hazard_reserve_gap")
    sanity = float(
        UNIT_SANITY_MULTIPLE if _optional(deal, "unit_sanity_multiple") is None else deal["unit_sanity_multiple"]
    )
    rates = baseline_rates(intercept, tau)
    recorded = store.outcomes(deal_id)
    payload = {
        "deal": {
            "id": deal["id"],
            "product": deal["product"],
            "currency": str(deal.get("currency") or DEAL_CURRENCY),
            "quantity": deal["quantity"],
            "target_price": deal["target_price"],
            "ceiling_price": deal["ceiling_price"],
            "risk_tolerance": deal["risk_tolerance"],
            "seed": deal["seed"],
            "include_unverified": include_unverified,
            "expected_markup": markup,
            "reserve_prior_mean": user_prior,
            "reserve_prior_std": user_std,
            "search_cost": _optional(deal, "search_cost"),
            "bayes_obs_noise": noise_floor,
            "unit_sanity_multiple": sanity,
            "hazard_reserve_gap": reserve_gap,
        },
        "hazard_assumptions": {
            "intercept": intercept,
            "tau": tau,
            "quote_gap": quote_gap,
            "reserve_gap": None,
            "reserve_gap_in_decision": False,
            "baseline_rate": rates["baseline"],
            "baseline_low": rates["low"],
            "baseline_high": rates["high"],
            "source": "user" if user_intercept is not None or user_gap is not None else "ebay_seller_ended",
            "coefficients": "seller-ended eBay fit" if user_intercept is None and user_gap is None else "user override",
            "min_events": int(HAZARD_MIN_EVENTS),
            "recorded_outcomes": len(recorded),
            "prior_driven": len(recorded) < int(HAZARD_MIN_EVENTS),
            "note": (HAZARD_TRANSFER_NOTE + " " + str(fit.provenance.get("transfer_caveat") or "")).strip(),
        },
        "audit": audit_line(unconfirmed),
        "unconfirmed_numbers_used": unconfirmed,
        "shadow_gate": shadow_progress(
            [
                {
                    "would_accept": bool((event.get("provenance") or {}).get("shadow_would_accept")),
                    "resolved": bool((event.get("provenance") or {}).get("shadow_resolved")),
                    "matched": bool((event.get("provenance") or {}).get("shadow_matched")),
                }
                for event in events
            ]
        ),
        "headline": _headline(consumed, recorded, unconfirmed=unconfirmed, deal=deal),
        "ranking_basis": basis,
        "kappa_note": LANDED_NOTE if basis == "landed_cost" else KAPPA_NOTE,
        "hazard_note": HAZARD_NOTE,
        "heuristic": HEURISTIC_NOTE,
        "suppliers": cards,
        "events": public_events,
        "review": review,
        "drafts": draft_rows,
        "elapsed_ms": elapsed_ms,
        "n_samples": samples,
        "n_grid": grid,
        "stop": stop,
        "stop_reason": stop_reason,
        "mc": mc,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    store.save_snapshot(deal_id, payload, elapsed_ms)
    stored = store.get_snapshot(deal_id) or payload
    return stored
