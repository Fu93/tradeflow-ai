"""Guards for the independent re-verification of bf9ef6f.

Each test names the defect it keeps closed: an unconfirmed extractor number
must not become a counter, a posted round price is not a confirmation, glued
currency words are amounts, landed cost uses the deal currency, the send
guard recomputes from stored inputs and the audit chain, the risk cap can
bind, and a one-click suggestion is a supplier goods unit price or blank.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _deal(tmp_path: Path, monkeypatch, *, currency: str = "CNY", ceiling: float = 30.0):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(tmp_path / "desk.db")
    snapshot = create_deal(
        store,
        product="valve",
        quantity=100,
        target_price=4.0,
        ceiling_price=ceiling,
        risk_tolerance=0.5,
        seed=1,
        currency=currency,
        suppliers=[
            {"name": "North", "address": "north@example.test"},
            {"name": "South", "address": "south@example.test"},
            {"name": "East", "address": "east@example.test"},
            {"name": "West", "address": "west@example.test"},
        ],
    )
    return store, snapshot


def test_glued_currency_words_are_amounts_and_model_codes_are_not():
    from src.desk.span_read import read_amounts

    assert read_amounts("2.55usd") == [2.55]
    assert read_amounts("1.88usd/pc") == [1.88]
    assert read_amounts("2.55USD") == [2.55]
    assert read_amounts("30RMB/pc") == [30.0]
    assert read_amounts("EUR4.20") == [4.20]
    assert read_amounts("3X") == []
    assert read_amounts("XT660") == []


def test_landed_cost_uses_the_deal_currency(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch, currency="USD", ceiling=10.0)
    deal_id = snapshot["deal"]["id"]
    north, south = snapshot["suppliers"][0]["id"], snapshot["suppliers"][1]["id"]
    first = ingest_message(
        store, deal_id, source="paste", supplier_id=north,
        raw_text="unit price 4.50 USD per piece",
        freight=0.30, duty_rate=0.0, fees=0.0,
    )
    second = ingest_message(
        store, deal_id, source="paste", supplier_id=south,
        raw_text="unit price 4.40 USD per piece",
        freight=0.20, duty_rate=0.0, fees=0.0,
    )
    accept_review(store, deal_id, first["event"]["id"], north, chosen_token="4.50")
    fresh = accept_review(store, deal_id, second["event"]["id"], south, chosen_token="4.40")
    by_id = {row["id"]: row for row in fresh["suppliers"]}
    assert by_id[north]["landed_cost"] == pytest.approx(4.80)
    assert by_id[south]["landed_cost"] == pytest.approx(4.60)
    assert fresh["headline"]["best_first_landed"] == pytest.approx(4.60)


def test_prefill_is_the_supplier_goods_price_or_blank():
    from src.desk.parse import parse_quote

    rival = parse_quote("Supplier X offers 26 CNY per piece.\nWe can do 30 CNY per piece.")
    assert rival["provenance"]["suggested_token"] != "26"
    assert rival["provenance"]["suggested_token"] in {None, "30"}
    fees = parse_quote("Freight is 12 CNY. Your target is 18 CNY per piece.")
    assert fees["provenance"]["suggested_token"] is None
    own = parse_quote("Unit price 15.60 CNY per piece.")
    assert own["provenance"]["suggested_token"] in {None, "15.60"}


def test_risk_cap_binds_because_the_increment_uses_the_slope():
    from src.engine.decision import choose_offer, hazard_floor
    from src.engine.hazard_fit import incremental_hazard, load_hazard_fit
    from src.engine.reserve import ReserveState

    fit = load_hazard_fit()
    ask = 30.0
    assert incremental_hazard(ask, ask, 0.0, fit) == 0.0
    assert incremental_hazard(ask, ask * 0.95, 0.0, fit) > 0.0
    tight_floor = hazard_floor(ask, 0.0, 0.0, fit)
    loose_floor = hazard_floor(ask, 0.15, 0.0, fit)
    assert tight_floor == pytest.approx(ask)
    assert loose_floor < tight_floor
    state = ReserveState.policy().with_reference(ask)
    tight = choose_offer(ask=ask, outside=ask, state=state, risk_cap=0.0, fit=fit)
    loose = choose_offer(ask=ask, outside=ask, state=state, risk_cap=0.15, fit=fit)
    assert tight.push_counter >= loose.push_counter


def test_record_without_decision_inputs_withholds_the_counter():
    from src.desk.record import build_record

    record = build_record(
        deal_id="d", supplier_id="s", currency="CNY", events=[],
        ask=15.0, ceiling=20.0, offer=14.0, counter=14.0,
        push_counter=14.0, half=14.0, double=14.0,
        prior_alpha=2.0, prior_beta=2.0, prior_n=0, prior_source="labelled_policy",
        unconfirmed=0, hazard_within_cap=True,
    )
    assert record.counter is None
    assert any(item.name == "decision_inputs_stored" and not item.passed for item in record.checks)


def test_headline_saving_uses_the_lower_landed_price():
    from src.desk.headline import saving_vs_best_first

    saved = saving_vs_best_first([4.8, 4.6], 4.0, simulated=False, unconfirmed=0)
    assert saved["best_first_landed"] == pytest.approx(4.6)
    assert saved["saving"] == pytest.approx(0.6)


def test_outcome_and_assumption_changes_are_on_the_chain(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message, record_outcome, set_assumptions

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60")
    record_outcome(store, deal_id, supplier, outcome="continue", quote=15.60, offer=15.60)
    set_assumptions(store, deal_id, {"risk_tolerance": 0.2})
    chain = store.verify_chain(deal_id)
    kinds = [entry["type"] for entry in chain["entries"]]
    assert "outcome" in kinds
    assert "assumptions" in kinds
    assert chain["ok"] is True


def test_send_refuses_a_rewritten_counter_whose_row_hash_matches(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message
    from src.engine.reserve import ReserveState

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=30.0)
    deal_id = snapshot["deal"]["id"]
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    decision_id = draft["decision_id"]
    belief = ReserveState.policy()
    forged = {
        "deal_id": deal_id,
        "supplier_id": supplier,
        "currency": "CNY",
        "event_ids": [],
        "prices_minor": [],
        "ask": 20.0,
        "ceiling": 30.0,
        "offer": 20.0,
        "counter": 20.0,
        "push_counter": 20.0,
        "half": 20.0,
        "double": 20.0,
        "prior_alpha": belief.alpha,
        "prior_beta": belief.beta,
        "prior_n": 0,
        "prior_source": "labelled_policy",
        "optimiser": "decision.choose_offer",
        "hazard_fit_sha256": "pinned",
        "constants": {},
        "unconfirmed": 0,
        "checks": [
            {"name": "only_accepted_inputs", "passed": True, "detail": "ok"},
            {"name": "currency_matches_deal", "passed": True, "detail": "ok"},
            {"name": "counter_le_ask", "passed": True, "detail": "ok"},
            {"name": "counter_le_ceiling", "passed": True, "detail": "ok"},
            {"name": "hazard_within_cap", "passed": True, "detail": "ok"},
            {"name": "fit_hash_pinned", "passed": True, "detail": "ok"},
            {"name": "unconfirmed_clear", "passed": True, "detail": "ok"},
            {"name": "decision_inputs_stored", "passed": True, "detail": "ok"},
        ],
        "eligible": True,
        "extractor_response_sha256": "",
        "extractor_attempts": [],
        "rival_asks": [],
        "risk_cap": 0.5,
        "reserve_upper": belief.upper,
        "reject_ratios": [],
        "reference_ask": 20.0,
        "hazard_intercept": None,
        "hazard_gap": None,
        "held_previous_counter": None,
    }
    digest = hashlib.sha256(
        json.dumps(forged, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    store.conn.execute("DROP TRIGGER IF EXISTS decisions_no_update")
    store.conn.execute(
        "UPDATE decisions SET record_json = ?, record_sha256 = ? WHERE id = ?",
        (json.dumps(forged, sort_keys=True), digest, decision_id),
    )
    store.conn.commit()
    guarded = dict(draft, body="Hello, we can proceed at 20.00 CNY per unit.")
    with pytest.raises(SendRefused, match="audit chain"):
        _guard_send(store, guarded, deal)


def test_send_refuses_when_the_recomputed_counter_differs(tmp_path, monkeypatch):
    from src.desk.record import build_record
    from src.desk.service import SendRefused, _guard_send
    from src.engine.reserve import ReserveState

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=30.0)
    deal = snapshot["deal"]
    belief = ReserveState.policy()
    record = build_record(
        deal_id=deal["id"], supplier_id=snapshot["suppliers"][0]["id"], currency="CNY",
        events=[], ask=20.0, ceiling=30.0, offer=20.0, counter=20.0,
        push_counter=20.0, half=20.0, double=20.0,
        prior_alpha=belief.alpha, prior_beta=belief.beta, prior_n=0,
        prior_source="labelled_policy", unconfirmed=0, hazard_within_cap=True,
        rival_asks=(14.0,), risk_cap=0.5, reserve_upper=belief.upper, reject_ratios=(),
        reference_ask=20.0, inputs_stored=True,
    )
    assert record.counter == pytest.approx(20.0)
    decision_id = store.append_decision(record.as_dict(), record.sha256())
    store.append_chain(
        deal["id"], "decision",
        {"decision_id": decision_id, "record_sha256": record.sha256()},
    )
    with pytest.raises(SendRefused, match="fresh decision"):
        _guard_send(
            store,
            {"decision_id": decision_id, "deal_id": deal["id"], "body": "offer 20.00"},
            deal,
        )


def test_extension_copilot_uses_the_desk_confirm_flow():
    text = Path("extension/background.js").read_text(encoding="utf-8")
    submit = text[text.index("async function handleOfferSubmission"):text.index("async function confirmDeskPrice")]
    confirm = text[text.index("async function confirmDeskPrice"):text.index("// Bidirectional Port Connection Router")]
    assert "/round" not in submit
    assert "supplier_offer" not in submit
    assert "/quotes" in submit
    assert "/review/" in confirm
    assert "chosen_token" in confirm
    hud = Path("extension/content.js").read_text(encoding="utf-8")
    offer_at = hud.index('portClient.send("SUBMIT_OFFER"')
    offer = hud[offer_at:hud.index("});", offer_at)]
    assert "offer:" not in offer
    panel = Path("src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert "tokens.length === 1" not in panel
