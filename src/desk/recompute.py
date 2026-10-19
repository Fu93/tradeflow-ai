"""Replay each supplier's accepted quotes, then run one coupled solve."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src.config.heuristics import (
    BAYES_OBS_NOISE,
    DESK_N_GRID,
    DESK_N_SAMPLES,
    HAZARD_MIN_EVENTS,
    HAZARD_SUPPLIER_TAU,
    SEARCH_STEP,
    UNIT_SANITY_MULTIPLE,
    WEITZMAN_SEARCH_COST,
)
from src.desk.store import DeskStore
from src.desk.headline import AUDIT_LINE, confirmed_prices, saving_vs_best_first
from src.desk.shadow import progress as shadow_progress
from src.engine.coupled import solve_coupled
from src.engine.decision import HEURISTIC_NOTE
from src.engine.hazard import HazardBelief, baseline_rates
from src.engine.hazard_fit import NOTE as HAZARD_TRANSFER_NOTE
from src.engine.hazard_fit import load_hazard_fit
from src.engine.landed import landed_unit_cost, ranking_basis
from src.engine.reserve import ReserveState

HAZARD_NOTE = (
    "Walk-away hazard uses one gap, transferred from the eBay seller-ended fit. "
    "A reserve-gap term is not added. Recorded continue and walk-away outcomes "
    "update only this supplier's effect. Quote text is not an outcome."
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


def _components(parsed: dict[str, Any]) -> dict[str, Any]:
    return landed_unit_cost(
        unit_price=parsed.get("unit_price"),
        freight=parsed.get("freight"),
        duty_rate=parsed.get("duty_rate"),
        fees=parsed.get("fees"),
        currency=parsed.get("currency") or "CNY",
        base_currency="CNY",
    )


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


def _draft_body(product: str, counter: float, quantity: int, language: str) -> str:
    if language == "zh":
        return (
            f"您好，关于{product}，我们可以按每件 {counter:.2f} 元、"
            f"数量 {quantity} 继续。请确认是否可以安排。"
        )
    return (
        f"Hello, for {product} we can proceed at {counter:.2f} CNY per unit "
        f"for {quantity} units. Please confirm if that works."
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


def _headline(events: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed, _used = confirmed_prices(events)
    first: dict[str, float] = {}
    for event in confirmed:
        supplier_id = event.get("supplier_id")
        if not supplier_id or supplier_id in first:
            continue
        landed = _components(event.get("parsed") or {})
        price = landed.get("landed_cost")
        if price is None:
            price = (event.get("parsed") or {}).get("unit_price")
        if price is not None:
            first[str(supplier_id)] = float(price)
    agreed = [
        float(item["agreed_price"])
        for item in outcomes
        if item.get("outcome") == "agreed" and item.get("agreed_price") is not None
    ]
    simulated = any((event.get("provenance") or {}).get("origin") == "simulated" for event in confirmed)
    return saving_vs_best_first(
        list(first.values()),
        None if not agreed else agreed[-1],
        simulated=simulated,
    )


def _reserve_state(prices: list[float], outcomes: list[dict[str, Any]]) -> ReserveState | None:
    """Own-ask Beta. Later prices and closed deals are upper bounds on D."""
    if not prices:
        return None
    state = ReserveState.policy()
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
        if include_unverified:
            return rows
        return [event for event in rows if not _unverified(event)]

    beliefs_state: dict[str, tuple] = {}
    for row in suppliers:
        history = decision_events(row["id"])
        prices = [
            float(event["parsed"]["unit_price"])
            for event in history
            if event.get("parsed", {}).get("unit_price") is not None
        ]
        state = _reserve_state(prices, store.outcomes(deal_id, row["id"]))
        if state is None:
            beliefs_state[row["id"]] = {
                "mean": None,
                "std": None,
                "observations": 0,
                "label": "no data",
                "state": None,
                "note": "No accepted quote yet.",
            }
            continue
        mean = float(prices[-1]) * float(state.mean()) / target
        std = _std_or_tiny(float(prices[-1]) * float(state.std()) / target)
        beliefs_state[row["id"]] = {
            "mean": mean,
            "std": std,
            "observations": len(prices),
            "label": state.source,
            "state": state,
            "note": state.note(),
        }
        store.set_belief(row["id"], mean, std)
    reserve_note = "Each supplier has their own Beta prior. A markup is not applied."

    active = [
        row for row in suppliers
        if beliefs_state[row["id"]]["state"] is not None and decision_events(row["id"])
    ]
    samples, grid = solver_budget()
    solved: dict[str, Any] | None = None
    half_solved: dict[str, Any] | None = None
    double_solved: dict[str, Any] | None = None
    index_of: dict[str, int] = {}
    landed_rows: list[dict[str, Any]] = []
    if active:
        quotes = []
        means = []
        stds = []
        kappas = []
        hazard_beliefs = []
        fit = load_hazard_fit()
        intercept = _optional(deal, "hazard_intercept")
        tau = _optional(deal, "hazard_tau")
        quote_gap = _optional(deal, "hazard_quote_gap")
        decision_intercept = float(fit.intercept if intercept is None else intercept)
        decision_gap = float(fit.gap_coef if quote_gap is None else quote_gap)
        for index, row in enumerate(active):
            index_of[row["id"]] = index
            latest = decision_events(row["id"])[-1]
            unit = float(latest["parsed"]["unit_price"])
            quotes.append(unit / target)
            slot = beliefs_state[row["id"]]
            mean, std = slot["mean"], slot["std"]
            means.append(float(mean))
            stds.append(float(std))
            landed = _components(latest["parsed"])
            landed_rows.append(landed)
            belief = HazardBelief(
                intercept=decision_intercept,
                tau=tau,
                quote_gap_coef=decision_gap,
                reserve_gap_coef=0.0,
            )
            for outcome in store.outcomes(deal_id, row["id"]):
                if outcome.get("quote") is None or outcome.get("offer") is None:
                    continue
                belief.update(
                    walked=outcome["outcome"] == "walk_away",
                    quote=float(outcome["quote"]) / target,
                    offer=float(outcome["offer"]) / target,
                    reserve=float(mean),
                )
            store.set_hazard_counts(row["id"], belief.n_events, belief.n_walks)
            hazard_beliefs.append(belief)
        basis = ranking_basis(landed_rows)
        if basis == "landed_cost":
            for index, landed in enumerate(landed_rows):
                adder = landed.get("adder")
                kappas.append(0.0 if adder is None else float(adder) / target)
        else:
            kappas = [0.0 for _row in active]
        rounds = [len(by_supplier[row["id"]]) for row in active]
        search_cost = (
            float(WEITZMAN_SEARCH_COST)
            if _optional(deal, "search_cost") is None
            else float(deal["search_cost"])
        )

        solved = solve_coupled(
            np.asarray(quotes, dtype=np.float64),
            np.asarray(means, dtype=np.float64),
            np.asarray(stds, dtype=np.float64),
            ceiling=ceiling / target,
            kappas=np.asarray(kappas, dtype=np.float64),
            n_samples=samples,
            n_grid=grid,
            risk_tolerance=float(deal["risk_tolerance"]),
            beliefs=hazard_beliefs,
            rng=np.random.default_rng(int(deal["seed"])),
            round_index=rounds,
            snap_to_grid=True,
            search_cost=search_cost,
            reserves=[beliefs_state[row["id"]]["state"] for row in active],
        )
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
            card["currency"] = parsed.get("currency") or "CNY"
            landed = _components(parsed)
            card["landed_cost"] = landed.get("landed_cost")
            card["landed_status"] = landed.get("status") or "no data"
        if card["excluded_from_decision"]:
            card["rank"] = None
        elif solved is not None and row["id"] in index_of:
            index = index_of[row["id"]]
            blocked = bool(solved["blocked"][index])
            offer = float(solved["offers"][index]) * target
            own_quote = float(card["latest_quote"])
            relation = str(solved["counter_relation"][index])
            if blocked or relation == "withheld":
                counter = None
                figure = "upper"
                relation = "withheld"
            else:
                counter = min(offer, ceiling, own_quote)
                figure = "mean"
                if abs(float(counter) - own_quote) <= target * float(SEARCH_STEP):
                    relation = "at_ask"
            card["counter"] = counter
            card["push_counter"] = None if blocked else min(float(solved["push_offers"][index]) * target, ceiling, own_quote)
            card["counter_relation"] = relation
            card["sensitivity_half"] = None if blocked else min(float(solved["push_half"][index]) * target, ceiling, own_quote)
            card["sensitivity_double"] = None if blocked else min(float(solved["push_double"][index]) * target, ceiling, own_quote)
            card["blocked"] = blocked
            card["hazard"] = float(solved["hazards"][index])
            card["hazard_upper"] = float(solved["hazard_upper"][index])
            card["hazard_figure"] = figure
            card["prior_driven"] = bool(solved.get("prior_driven", True))
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

    for card in cards:
        if card["latest_quote"] is None or card["counter"] is None:
            store.supersede_open_drafts(deal["id"], card["id"])
            continue
        history = by_supplier.get(card["id"]) or []
        language = _language([event.get("raw_text") or "" for event in history])
        body = _draft_body(
            str(deal["product"]),
            float(card["counter"]),
            int(deal["quantity"]),
            language,
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
        store.replace_open_draft(
            deal["id"],
            card["id"],
            body,
            reply_to=reply_to,
            references_header=references,
        )

    mc = None
    stop = False
    stop_reason = "no accepted quotes yet"
    if solved is not None:
        metrics = solved.get("mc") or {}
        cvar = metrics.get("cvar_cost")
        mc = {
            "cvar_cost": None if cvar is None else float(cvar) * target,
            "cvar_se": None if metrics.get("cvar_se") is None else float(metrics["cvar_se"]) * target,
            "tail_saturated_at_ceiling": bool(metrics.get("tail_saturated_at_ceiling")),
            "tail_fraction": metrics.get("tail_fraction"),
        }
        stop = bool(solved.get("stop"))
        stop_reason = str(solved.get("stop_reason") or "")

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
            "note": HAZARD_TRANSFER_NOTE,
        },
        "audit": AUDIT_LINE,
        "unconfirmed_numbers_used": confirmed_prices(events)[1],
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
        "headline": _headline(events, recorded),
        "ranking_basis": basis,
        "kappa_note": LANDED_NOTE if basis == "landed_cost" else KAPPA_NOTE,
        "hazard_note": HAZARD_NOTE,
        "heuristic": None if solved is None else solved.get("heuristic") or HEURISTIC_NOTE,
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
