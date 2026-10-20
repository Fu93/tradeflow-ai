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
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message
    from src.engine.reserve import ReserveState

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=30.0)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="unit price 20.00 CNY per piece",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="20.00")
    belief = ReserveState.policy()
    record = build_record(
        deal_id=deal["id"], supplier_id=supplier, currency="CNY",
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
        {
            "decision_id": decision_id,
            "record_sha256": record.sha256(),
            "supplier_id": supplier,
        },
    )
    with pytest.raises(SendRefused, match="fresh decision"):
        _guard_send(
            store,
            {
                "decision_id": decision_id,
                "deal_id": deal["id"],
                "supplier_id": supplier,
                "body": "offer 20.00",
            },
            deal,
        )


def _issued(store, deal, supplier, **overrides):
    from src.desk.record import build_record
    from src.engine.reserve import ReserveState

    belief = ReserveState.policy()
    fields = dict(
        deal_id=deal["id"], supplier_id=supplier, currency="CNY", events=[],
        ask=15.60, ceiling=float(deal["ceiling_price"]), offer=15.60, counter=15.60,
        push_counter=15.60, half=15.60, double=15.60,
        prior_alpha=belief.alpha, prior_beta=belief.beta, prior_n=0,
        prior_source="labelled_policy", unconfirmed=0, hazard_within_cap=True,
        rival_asks=(), risk_cap=0.5, reserve_upper=belief.upper, reject_ratios=(),
        reference_ask=15.60, inputs_stored=True,
    )
    fields.update(overrides)
    record = build_record(**fields)
    decision_id = store.append_decision(record.as_dict(), record.sha256())
    store.append_chain(
        deal["id"], "decision",
        {"decision_id": decision_id, "record_sha256": record.sha256(), "supplier_id": supplier},
    )
    return decision_id, record


def test_send_refuses_another_suppliers_decision(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    north, south = snapshot["suppliers"][0]["id"], snapshot["suppliers"][1]["id"]
    for supplier in (north, south):
        opened = ingest_message(
            store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 15.60 元",
        )
        accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    drafts = {item["supplier_id"]: item for item in store.drafts(deal["id"]) if item["status"] == "draft"}
    stolen = dict(drafts[north], decision_id=drafts[south]["decision_id"], body=drafts[south]["body"])
    with pytest.raises(SendRefused, match="does not belong"):
        _guard_send(store, stolen, deal)


def test_send_refuses_an_older_decision(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 15.60 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    _issued(store, deal, supplier)
    with pytest.raises(SendRefused, match="latest decision"):
        _guard_send(store, draft, deal)


def test_send_refuses_an_ask_the_confirmed_quote_does_not_have(tmp_path, monkeypatch):
    from src.desk.project import draft_body
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 15.60 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    decision_id, _record = _issued(store, deal, supplier, ask=20.0, offer=20.0, counter=20.0, reference_ask=20.0)
    body = draft_body("valve", 20.0, 100, "zh", "CNY")
    draft = next(item for item in store.drafts(deal["id"]) if item["supplier_id"] == supplier)
    with pytest.raises(SendRefused, match="confirmed quote"):
        _guard_send(store, dict(draft, decision_id=decision_id, body=body), deal)


def test_send_refuses_a_held_counter_the_chain_did_not_issue(tmp_path, monkeypatch):
    from src.desk.project import draft_body
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 15.60 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    decision_id, _record = _issued(
        store, deal, supplier, offer=12.0, counter=12.0, held_previous_counter=12.0,
    )
    body = draft_body("valve", 12.0, 100, "zh", "CNY")
    draft = next(item for item in store.drafts(deal["id"]) if item["supplier_id"] == supplier)
    with pytest.raises(SendRefused, match="held counter"):
        _guard_send(store, dict(draft, decision_id=decision_id, body=body), deal)


def test_send_refuses_a_draft_that_adds_a_second_price(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 15.60 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    tainted = dict(draft, body=draft["body"] + "\nfinal offer 20.00")
    with pytest.raises(SendRefused, match="rendered counter"):
        _guard_send(store, tainted, deal)


def test_landed_rank_does_not_cut_to_a_lower_unit_price(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    north, south = snapshot["suppliers"][0]["id"], snapshot["suppliers"][1]["id"]
    first = ingest_message(
        store, deal_id, source="paste", supplier_id=north,
        raw_text="单价 5.00 元", freight=0.10, duty_rate=0.0, fees=0.0,
    )
    second = ingest_message(
        store, deal_id, source="paste", supplier_id=south,
        raw_text="单价 4.40 元", freight=0.90, duty_rate=0.0, fees=0.0,
    )
    accept_review(store, deal_id, first["event"]["id"], north, chosen_token="5.00")
    fresh = accept_review(store, deal_id, second["event"]["id"], south, chosen_token="4.40")
    by_id = {row["id"]: row for row in fresh["suppliers"]}
    assert by_id[north]["landed_cost"] == pytest.approx(5.10)
    assert by_id[south]["landed_cost"] == pytest.approx(5.30)
    assert by_id[north]["rank"] == 1
    assert by_id[north]["counter"] == pytest.approx(5.00)
    assert by_id[north]["counter_relation"] == "at_ask"
    assert by_id[south]["counter"] == pytest.approx(4.20)


def test_accept_confirms_only_the_visible_token(tmp_path, monkeypatch):
    from src.desk.parse import parse_quote
    from src.desk.service import DeskRejected, accept_review, ingest_message

    shown = parse_quote("单价 14.80 元")
    assert shown["provenance"]["suggested_token"] == "14.80"
    assert "14.80" in shown["provenance"]["confirm_span"]
    assert shown["provenance"]["confirm_value"] == pytest.approx(14.80)
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="单价 14.80 元",
    )
    with pytest.raises(DeskRejected, match="shown"):
        accept_review(store, deal_id, opened["event"]["id"], supplier)
    panel = Path("src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert "confirmValue" in panel
    assert "deskSeq" in panel
    source = Path("tests/test_desk_ui.py").read_text(encoding="utf-8")
    assert "wait_for_timeout" not in source
    assert "data-desk-seq" in source or "deskSeq" in source


def test_negotiate_rejects_a_posted_price(monkeypatch):
    monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from src.app.main import app

    client = TestClient(app)
    response = client.post("/negotiate", json={
        "target_price": 12.0,
        "ceiling_price": 16.2,
        "observed_offer": 1.25,
    })
    assert response.status_code == 422
    assert "unconfirmed numbers used in decisions: 1" in response.text
    assert "opening_offer_price" not in response.text


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
