"""Replay each supplier's accepted quotes, then run one coupled solve."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src.config.heuristics import DESK_N_GRID, DESK_N_SAMPLES, PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
from src.desk.store import DeskStore
from src.engine.bayesian_v2 import robust_bayes_update
from src.engine.coupled import solve_coupled
from src.engine.hazard import HazardBelief
from src.engine.landed import landed_unit_cost, ranking_basis

HAZARD_NOTE = (
    "Walk-away hazard stays prior-driven until a person records a continue "
    "or a walk-away. Quote text is not turned into one of those events."
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


def recompute(store: DeskStore, deal_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    deal = store.get_deal(deal_id)
    if deal is None:
        raise KeyError(deal_id)
    previous = store.get_snapshot(deal_id) or {}
    previous_rows = {row["id"]: row for row in previous.get("suppliers") or []}
    suppliers = store.suppliers(deal_id)
    events = store.events(deal_id)
    accepted = [event for event in events if event["status"] == "accepted" and event.get("supplier_id")]
    by_supplier: dict[str, list[dict[str, Any]]] = {row["id"]: [] for row in suppliers}
    for event in accepted:
        by_supplier.setdefault(event["supplier_id"], []).append(event)

    target = float(deal["target_price"])
    ceiling = float(deal["ceiling_price"])
    beliefs_state: dict[str, tuple[float, float, int]] = {}
    for row in suppliers:
        mean = float(PRIOR_RESERVE_MEAN)
        std = float(PRIOR_RESERVE_STD)
        history = by_supplier.get(row["id"]) or []
        for event in history:
            observed = float(event["parsed"]["unit_price"]) / target
            updated = robust_bayes_update(mean, std, observed)
            mean = float(updated.posterior_mean)
            std = _std_or_tiny(float(updated.posterior_std))
        beliefs_state[row["id"]] = (mean, std, len(history))
        store.set_belief(row["id"], mean, std)

    active = [row for row in suppliers if by_supplier.get(row["id"])]
    samples, grid = solver_budget()
    solved: dict[str, Any] | None = None
    index_of: dict[str, int] = {}
    landed_rows: list[dict[str, Any]] = []
    if active:
        quotes = []
        means = []
        stds = []
        kappas = []
        hazard_beliefs = []
        for index, row in enumerate(active):
            index_of[row["id"]] = index
            latest = by_supplier[row["id"]][-1]
            unit = float(latest["parsed"]["unit_price"])
            quotes.append(unit / target)
            mean, std, _count = beliefs_state[row["id"]]
            means.append(mean)
            stds.append(std)
            landed = _components(latest["parsed"])
            landed_rows.append(landed)
            belief = HazardBelief()
            belief.n_events = int(row.get("hazard_events") or 0)
            belief.n_walks = int(row.get("hazard_walks") or 0)
            hazard_beliefs.append(belief)
        basis = ranking_basis(landed_rows)
        if basis == "landed_cost":
            for index, landed in enumerate(landed_rows):
                adder = landed.get("adder")
                kappas.append(0.0 if adder is None else float(adder) / target)
        else:
            kappas = [0.0 for _row in active]
        rounds = [len(by_supplier[row["id"]]) for row in active]
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
        )
    else:
        basis = "unit_price"

    cards = []
    for row in suppliers:
        history = by_supplier.get(row["id"]) or []
        mean, std, observations = beliefs_state[row["id"]]
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
            "belief_label": "prior" if observations == 0 else "posterior",
            "prior_driven": True,
            "counter": None,
            "blocked": False,
            "hazard": None,
            "hazard_upper": None,
            "hazard_figure": None,
            "rank": None,
            "accepted_event_ids": [event["id"] for event in history],
            "counter_held": False,
        }
        if history:
            latest = history[-1]
            parsed = latest["parsed"]
            card["latest_quote"] = parsed.get("unit_price")
            card["currency"] = parsed.get("currency") or "CNY"
            landed = _components(parsed)
            card["landed_cost"] = landed.get("landed_cost")
            card["landed_status"] = landed.get("status") or "no data"
        if solved is not None and row["id"] in index_of:
            index = index_of[row["id"]]
            blocked = bool(solved["blocked"][index])
            offer = float(solved["offers"][index]) * target
            own_quote = float(card["latest_quote"])
            if blocked:
                counter = None
                figure = "upper"
            else:
                counter = min(offer, ceiling, own_quote)
                figure = "mean"
            card["counter"] = counter
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
        if card["latest_quote"] is None:
            return (1, 0.0, card["name"], card["id"])
        if basis == "landed_cost" and card["landed_cost"] is not None:
            cost = float(card["landed_cost"])
        else:
            cost = float(card["latest_quote"])
        return (0, cost, card["name"], card["id"])

    ordered = sorted(cards, key=sort_key)
    rank = 0
    for card in ordered:
        if card["latest_quote"] is None:
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
    payload = {
        "deal": {
            "id": deal["id"],
            "product": deal["product"],
            "quantity": deal["quantity"],
            "target_price": deal["target_price"],
            "ceiling_price": deal["ceiling_price"],
            "risk_tolerance": deal["risk_tolerance"],
            "seed": deal["seed"],
        },
        "ranking_basis": basis,
        "kappa_note": LANDED_NOTE if basis == "landed_cost" else KAPPA_NOTE,
        "hazard_note": HAZARD_NOTE,
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
