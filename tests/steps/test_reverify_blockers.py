"""Guards for the independent re-verification of bf9ef6f.

Each test names the defect it keeps closed: an unconfirmed extractor number
must not become a counter, a posted round price is not a confirmation, glued
currency words are amounts, landed cost uses the deal currency, the send
guard recomputes from stored inputs and the audit chain, the risk cap can
bind, and a one-click suggestion is a supplier goods unit price or blank.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
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


def _reissue(store, draft, **changes):
    stored = store.get_decision(draft["decision_id"])
    record = json.loads(json.dumps(stored["record"]))
    record.update(changes)
    digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    decision_id = store.append_decision(record, digest)
    store.append_chain(
        draft["deal_id"],
        "decision",
        {
            "decision_id": decision_id,
            "record_sha256": digest,
            "supplier_id": draft["supplier_id"],
        },
    )
    return decision_id


def _tree_text(relative: str) -> str:
    """The copy on PYTHONPATH. The mutation runner stages that copy first."""
    roots = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    roots.append(str(Path(__file__).resolve().parents[2]))
    for root in roots:
        candidate = Path(root) / relative
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError(relative)


def test_send_refuses_a_decision_in_another_currency(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, _body_currency, _currencies_agree, _deal_opened, _guard_send
    from src.desk.service import accept_review, ingest_message

    assert _deal_opened([]) == {}
    assert _body_currency("Hello, for valve we can proceed at 31.00 USD per unit for 100 units.") == "USD"
    assert _body_currency("no amount here") == ""
    assert _currencies_agree("CNY", "CNY", "CNY") is True
    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=40.0)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 31.00 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="31.00")
    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    decision_id = _reissue(store, draft, currency="USD")
    with pytest.raises(SendRefused, match="chained deal currency"):
        _guard_send(store, dict(draft, decision_id=decision_id), deal)


def test_send_refuses_an_edited_confirmed_price(tmp_path, monkeypatch):
    from src.desk.project import draft_body
    from src.desk.service import SendRefused, _guard_send, _price_in_message, accept_review, ingest_message

    assert _price_in_message("单价 20.00 元", {"token": "31.00"}, "CNY") == pytest.approx(20.0)
    assert _price_in_message("单价 31.00 元", {"token": "31.00"}, "CNY") == pytest.approx(31.0)
    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=40.0)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 31.00 元",
    )
    event_id = opened["event"]["id"]
    accept_review(store, deal["id"], event_id, supplier, chosen_token="31.00")
    parsed = dict(store.event(deal["id"], event_id)["parsed"])
    parsed["unit_price"] = 20.0
    parsed["currency"] = "CNY"
    store.conn.execute(
        "UPDATE quote_events SET raw_text = ?, parsed_json = ? WHERE id = ?",
        ("单价 20.00 元", json.dumps(parsed, sort_keys=True), event_id),
    )
    store.conn.commit()
    draft = next(item for item in store.drafts(deal["id"]) if item["supplier_id"] == supplier)
    decision_id = _reissue(
        store, draft, ask=20.0, offer=20.0, counter=20.0, reference_ask=20.0,
    )
    body = draft_body("valve", 20.0, 100, "zh", "CNY")
    with pytest.raises(SendRefused, match="chained review"):
        _guard_send(store, dict(draft, decision_id=decision_id, body=body), deal)


def test_send_refuses_a_decision_with_forged_rivals(tmp_path, monkeypatch):
    from src.desk.project import draft_body
    from src.desk.service import SendRefused, _guard_send, accept_review, ingest_message

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=40.0)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 31.00 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="31.00")
    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    decision_id = _reissue(
        store, draft, rival_asks=[10.0], counter=10.0, offer=10.0,
    )
    body = draft_body("valve", 10.0, 100, "zh", "CNY")
    with pytest.raises(SendRefused, match="chained confirmations"):
        _guard_send(store, dict(draft, decision_id=decision_id, body=body), deal)


def test_resetting_an_approved_draft_does_not_send_again(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=40.0)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal["id"], source="paste", supplier_id=supplier, raw_text="单价 31.00 元",
    )
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="31.00")
    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    digest = draft_digest(draft["body"])
    approved = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest))
    assert approved["status"] == "approved"
    store.conn.execute(
        "UPDATE drafts SET status = 'draft', human_approved = 0 WHERE id = ?",
        (draft["id"],),
    )
    store.conn.commit()
    with pytest.raises(SendRefused, match="already approved"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest))


def test_a_non_suggested_token_shows_its_line():
    from src.desk.parse import parse_quote

    parsed = parse_quote("We can do 14.80 CNY per piece.\nFinal offer 12.50 CNY per piece.")
    spans = parsed["provenance"]["confirm_spans"]
    assert parsed["provenance"]["suggested_token"] == "14.80"
    assert "Final offer" in spans["12.50"]
    assert spans["12.50"] != "12.50"
    panel = _tree_text("src/web/static/js/desk.js")
    assert "const span = mapped || lineContaining(item.raw_text, token);" in panel


def test_mutation_runner_leaves_the_worktree_untouched(tmp_path):
    roots = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    roots.append(str(Path(__file__).resolve().parents[2]))
    script = None
    for root in roots:
        candidate = Path(root) / "scripts" / "mutation_sanity.py"
        if candidate.is_file():
            script = candidate
            break
    assert script is not None
    spec = importlib.util.spec_from_file_location("mutation_sanity_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    live = Path(__file__).resolve().parents[2] / "src" / "desk" / "service.py"
    original = live.read_bytes()
    needle = "return draft_digest(body) == draft_digest(rendered)"
    mutated = original.decode("utf-8").replace(needle, "return rendered in body", 1)
    stage = module.stage_mutant(live, mutated)
    try:
        assert live.read_bytes() == original
        staged = stage / "src" / "desk" / "service.py"
        assert staged.is_file()
        assert not staged.is_symlink()
        assert "return rendered in body" in staged.read_text(encoding="utf-8")
        assert live.read_bytes() == original
    finally:
        live.write_bytes(original)
        shutil.rmtree(stage, ignore_errors=True)


def test_send_after_a_rival_walks_away(tmp_path, monkeypatch):
    from src.desk.service import accept_review, draft_digest, ingest_message, record_outcome, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    north, south = snapshot["suppliers"][0]["id"], snapshot["suppliers"][1]["id"]
    first = ingest_message(
        store, deal_id, source="paste", supplier_id=north, raw_text="unit price 15.60 CNY per piece",
    )
    second = ingest_message(
        store, deal_id, source="paste", supplier_id=south, raw_text="unit price 14.00 CNY per piece",
    )
    accept_review(store, deal_id, first["event"]["id"], north, chosen_token="15.60")
    accept_review(store, deal_id, second["event"]["id"], south, chosen_token="14.00")
    fresh = record_outcome(store, deal_id, south, outcome="walk_away", quote=14.0, offer=14.0)
    card = next(row for row in fresh["suppliers"] if row["id"] == north)
    stored = store.get_decision(card["decision_id"])
    rivals = [float(item) for item in (stored["record"].get("rival_asks") or ())]
    assert all(abs(item - 14.0) > 0.01 for item in rivals)
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == north and item["status"] == "draft")
    sent = asyncio.run(
        send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
    )
    assert sent["status"] == "approved"
    store.close()


def test_another_currency_in_the_sentence_blocks_a_bare_number():
    from src.desk.parse import parse_quote, price_from_token

    text = "31.00 per piece, payment in USD"
    reading = parse_quote(text, deal_currency="CNY")
    assert reading["provenance"].get("suggested_token") != "31.00"
    assert "31.00" not in (reading["provenance"].get("candidates") or [])
    assert price_from_token(text, "31.00", deal_currency="CNY") is None
    assert price_from_token("31.00 per piece, payment in GBP", "31.00", deal_currency="CNY") is None
    assert price_from_token("31.00 per piece", "31.00", deal_currency="CNY") == pytest.approx(31.0)
    assert price_from_token("31.00 per piece, payment in CNY", "31.00", deal_currency="CNY") == pytest.approx(31.0)


def test_legacy_deal_needs_a_fresh_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "legacy-chain-key")
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    path = tmp_path / "desk.db"
    store = DeskStore(path)
    deal = store.create_deal(
        product="valve",
        quantity=100,
        target_price=4.0,
        ceiling_price=20.0,
        risk_tolerance=0.5,
        seed=1,
        currency="CNY",
        suppliers=[
            {"name": "North", "address": "north@example.test"},
            {"name": "South", "address": "south@example.test"},
            {"name": "East", "address": "east@example.test"},
            {"name": "West", "address": "west@example.test"},
        ],
    )
    deal_id = deal["id"]
    supplier = deal["supplier_ids"][0]
    ingested = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="15.60")
    store.close()
    reopened = DeskStore(path)
    untouched = reopened.verify_chain(deal_id)
    assert all(not (entry.get("payload") or {}).get("migrated") for entry in untouched["entries"])
    reopened.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    reopened.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    reopened.conn.execute("DELETE FROM desk_chain WHERE deal_id = ?", (deal_id,))
    reopened.conn.commit()
    reopened.migrate_deal(deal_id)
    chain = reopened.verify_chain(deal_id)
    opened = next(entry for entry in chain["entries"] if entry["type"] == "deal_opened")
    assert opened["payload"]["migrated"] is True
    assert opened["payload"]["note"] == "migrated, pre-chain inputs unverified"
    assert reopened.event(deal_id, ingested["event"]["id"])["status"] == "review"
    from src.desk.service import confirm_policy, confirm_supplier_address

    draft = next(item for item in reopened.drafts(deal_id) if item["status"] == "draft")
    with pytest.raises(SendRefused, match="pre-chain inputs unverified"):
        asyncio.run(
            send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
    fresh = ingest_message(
        reopened, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(reopened, deal_id, fresh["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in reopened.drafts(deal_id) if item["supplier_id"] == supplier and item["status"] == "draft")
    with pytest.raises(SendRefused, match="policy limits"):
        asyncio.run(
            send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
    limits = reopened.get_deal(deal_id)
    confirm_policy(
        reopened,
        deal_id,
        human_confirmed=True,
        product=limits["product"],
        quantity=limits["quantity"],
        target_price=limits["target_price"],
        ceiling_price=limits["ceiling_price"],
        risk_tolerance=limits["risk_tolerance"],
        currency=limits["currency"],
    )
    for row in reopened.suppliers(deal_id):
        confirm_supplier_address(
            reopened, deal_id, row["id"], address=row["address"], human_confirmed=True,
        )
    draft = next(item for item in reopened.drafts(deal_id) if item["supplier_id"] == supplier and item["status"] == "draft")
    sent = asyncio.run(
        send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
    )
    assert sent["status"] == "approved"
    reopened.close()


def _sha256_rewrite(store, deal_id: str, extra: dict | None = None) -> None:
    from src.desk.audit_chain import GENESIS, canonical

    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    rows = store.conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    for row in rows:
        payload = json.loads(row["payload_json"])
        key_id = "primary"
        if "key_id" in row.keys():
            key_id = str(row["key_id"] or "") or "primary"
        body = {
            "deal_id": row["deal_id"],
            "seq": int(row["seq"]),
            "type": row["type"],
            "ts": row["ts"],
            "payload": payload,
            "prev_hash": prev,
            "key_id": key_id,
        }
        digest = hashlib.sha256(canonical(body)).hexdigest()
        store.conn.execute(
            "UPDATE desk_chain SET prev_hash = ?, entry_hash = ? WHERE id = ?",
            (prev, digest, row["id"]),
        )
        prev = digest
    if extra is not None:
        seq = int(rows[-1]["seq"]) + 1
        payload = extra
        body = {
            "deal_id": deal_id,
            "seq": seq,
            "type": "send_result",
            "ts": "2026-01-01T00:00:00.000+00:00",
            "payload": payload,
            "prev_hash": prev,
            "key_id": "primary",
        }
        digest = hashlib.sha256(canonical(body)).hexdigest()
        store.conn.execute(
            """
            INSERT INTO desk_chain (
                deal_id, seq, type, ts, payload_json, prev_hash, entry_hash, key_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deal_id,
                seq,
                "send_result",
                body["ts"],
                json.dumps(payload, sort_keys=True),
                prev,
                digest,
                "primary",
            ),
        )
    store.conn.commit()


def test_rehashed_chain_and_forged_failure_do_not_verify(tmp_path, monkeypatch):
    key = "chain-key-not-in-the-database-08c5"
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", key)
    from src.desk.audit_chain import ChainKeyMissing
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    ingested = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    digest = draft_digest(draft["body"])
    assert key.encode() not in Path(store.path).read_bytes()
    approved = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest))
    assert approved["status"] == "approved"
    monkeypatch.delenv("TRADEFLOW_CHAIN_KEY", raising=False)
    with pytest.raises(SendRefused, match="TRADEFLOW_CHAIN_KEY"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest))
    assert store.verify_chain(deal_id)["ok"] is False
    with pytest.raises(ChainKeyMissing):
        store.append_chain(deal_id, "note", {"text": "no key"})
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", key)
    _sha256_rewrite(
        store,
        deal_id,
        {"draft_id": draft["id"], "status": "failed", "body_sha256": digest, "transmitted": False},
    )
    assert store.verify_chain(deal_id)["ok"] is False
    with pytest.raises(SendRefused, match="audit chain broken"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest))
    store.close()


def test_anchor_detects_a_truncated_tail(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    readme = Path(__file__).resolve().parents[2].joinpath("README.md").read_text(encoding="utf-8")
    assert "tail truncation is detectable only with an anchor" in readme

    class _Anchor(BaseHTTPRequestHandler):
        heads: dict[str, dict] = {}

        def do_POST(self):
            count = int(self.headers.get("Content-Length") or 0)
            item = json.loads(self.rfile.read(count))
            _Anchor.heads[item["deal_id"]] = item
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse

            deal = parse_qs(urlparse(self.path).query).get("deal_id", [""])[0]
            item = _Anchor.heads.get(deal)
            if item is None:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(item).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            return

    _Anchor.heads = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Anchor)
    port = server.server_address[1]
    threading_started = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    threading_started.start()
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", f"http://127.0.0.1:{port}/anchor")
    reset_stores()
    try:
        store = DeskStore(tmp_path / "desk.db")
        snapshot = create_deal(
            store,
            product="valve",
            quantity=100,
            target_price=4.0,
            ceiling_price=20.0,
            risk_tolerance=0.5,
            seed=1,
            currency="CNY",
            suppliers=[
                {"name": "North", "address": "north@example.test"},
                {"name": "South", "address": "south@example.test"},
                {"name": "East", "address": "east@example.test"},
                {"name": "West", "address": "west@example.test"},
            ],
        )
        deal_id = snapshot["deal"]["id"]
        remote = store.verify_chain(deal_id)
        assert remote["ok"] is True
        assert _Anchor.heads[deal_id]["seq"] == remote["count"]
        assert _Anchor.heads[deal_id]["entry_hash"] == remote["head"]
        anchor = tmp_path / "chain-anchor.jsonl"
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
        store.append_chain(deal_id, "note", {"text": "tail"})
        anchored = store.verify_chain(deal_id)
        assert anchored["ok"] is True
        store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
        store.conn.execute(
            "DELETE FROM desk_chain WHERE deal_id = ? AND seq = (SELECT MAX(seq) FROM desk_chain WHERE deal_id = ?)",
            (deal_id, deal_id),
        )
        store.conn.commit()
        truncated = store.verify_chain(deal_id)
        assert truncated["ok"] is False
        monkeypatch.delenv("TRADEFLOW_CHAIN_ANCHOR", raising=False)
        assert store.verify_chain(deal_id)["ok"] is True
        assert store.verify_chain(deal_id)["anchor"] == "anchor not configured"
        store.close()
    finally:
        server.shutdown()
        server.server_close()


def test_missing_anchor_fails_closed_and_the_index_is_the_latest_head(tmp_path, monkeypatch):
    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    anchor = tmp_path / "chain-anchor.jsonl"
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    reset_stores()
    store = DeskStore(tmp_path / "desk.db")
    snapshot = create_deal(
        store,
        product="valve",
        quantity=100,
        target_price=4.0,
        ceiling_price=20.0,
        risk_tolerance=0.5,
        seed=1,
        currency="CNY",
        suppliers=[
            {"name": "North", "address": "north@example.test"},
            {"name": "South", "address": "south@example.test"},
            {"name": "East", "address": "east@example.test"},
            {"name": "West", "address": "west@example.test"},
        ],
    )
    deal_id = snapshot["deal"]["id"]
    verified = store.verify_chain(deal_id)
    assert verified["ok"] is True
    assert verified["count"] > 1
    anchor.unlink()
    Path(str(anchor) + ".index").unlink()
    assert store.verify_chain(deal_id)["ok"] is False
    monkeypatch.delenv("TRADEFLOW_CHAIN_ANCHOR", raising=False)
    bare = store.verify_chain(deal_id)
    assert bare["ok"] is True
    assert bare["anchor"] == "anchor not configured"
    store.close()


def test_anchor_failure_stays_pending_even_if_a_later_head_matches(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from src.desk.audit_chain import _export_anchor
    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    class _Down(BaseHTTPRequestHandler):
        def do_POST(self):
            count = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(count)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Down)
    port = server.server_address[1]
    threading_started = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    threading_started.start()
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", f"http://127.0.0.1:{port}/anchor")
    reset_stores()
    try:
        store = DeskStore(tmp_path / "desk.db")
        snapshot = create_deal(
            store,
            product="valve",
            quantity=100,
            target_price=4.0,
            ceiling_price=20.0,
            risk_tolerance=0.5,
            seed=1,
            currency="CNY",
            suppliers=[
                {"name": "North", "address": "north@example.test"},
                {"name": "South", "address": "south@example.test"},
                {"name": "East", "address": "east@example.test"},
                {"name": "West", "address": "west@example.test"},
            ],
        )
        deal_id = snapshot["deal"]["id"]
        assert store.verify_chain(deal_id)["ok"] is False
        row = store.conn.execute(
            "SELECT seq, entry_hash FROM desk_chain WHERE deal_id = ? ORDER BY seq DESC LIMIT 1",
            (deal_id,),
        ).fetchone()
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "later.jsonl"))
        _export_anchor(deal_id, int(row["seq"]), str(row["entry_hash"]))
        assert store.verify_chain(deal_id)["ok"] is False
        store.close()
    finally:
        server.shutdown()
        server.server_close()


def test_pre_key_chain_is_archived_and_reconfirmed(tmp_path, monkeypatch):
    from src.desk.audit_chain import GENESIS, legacy_digest
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    ingested = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="15.60")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    rows = store.conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    old_rows = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        digest = legacy_digest(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, prev)
        old_rows.append((int(row["seq"]), digest))
        store.conn.execute(
            "UPDATE desk_chain SET prev_hash = ?, entry_hash = ?, key_id = NULL WHERE id = ?",
            (prev, digest, row["id"]),
        )
        prev = digest
    store.conn.commit()
    old_head = old_rows[-1][1]
    path = store.path
    store.close()
    from src.desk.store import DeskStore

    reopened = DeskStore(path)
    assert reopened.conn.execute("SELECT COUNT(*) FROM desk_chain_legacy").fetchone()[0] == 0
    reopened.migrate_deal(deal_id)
    chain = reopened.verify_chain(deal_id)
    assert chain["ok"] is True
    opened = next(entry for entry in chain["entries"] if entry["type"] == "deal_opened")
    assert opened["prev_hash"] == GENESIS
    assert opened["payload"]["legacy_head"] == old_head
    assert opened["payload"]["legacy"] == "legacy unverified"
    assert opened["payload"]["note"] == "migrated, pre-chain inputs unverified"
    archived = reopened.conn.execute(
        "SELECT note, entry_hash FROM desk_chain_legacy WHERE deal_id = ? ORDER BY seq DESC LIMIT 1",
        (deal_id,),
    ).fetchone()
    assert archived["note"] == "legacy unverified"
    assert archived["entry_hash"] == old_head
    assert reopened.event(deal_id, ingested["event"]["id"])["status"] == "review"
    draft = next(item for item in reopened.drafts(deal_id) if item["status"] == "draft")
    with pytest.raises(SendRefused, match="pre-chain inputs unverified"):
        asyncio.run(
            send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
    accept_review(reopened, deal_id, ingested["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in reopened.drafts(deal_id) if item["supplier_id"] == supplier and item["status"] == "draft")
    with pytest.raises(SendRefused, match="policy limits"):
        asyncio.run(
            send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
    from src.desk.service import confirm_policy, confirm_supplier_address

    limits = reopened.get_deal(deal_id)
    confirm_policy(
        reopened,
        deal_id,
        human_confirmed=True,
        product=limits["product"],
        quantity=limits["quantity"],
        target_price=limits["target_price"],
        ceiling_price=limits["ceiling_price"],
        risk_tolerance=limits["risk_tolerance"],
        currency=limits["currency"],
    )
    for row in reopened.suppliers(deal_id):
        confirm_supplier_address(
            reopened, deal_id, row["id"], address=row["address"], human_confirmed=True,
        )
    draft = next(item for item in reopened.drafts(deal_id) if item["supplier_id"] == supplier and item["status"] == "draft")
    sent = asyncio.run(
        send_draft(reopened, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
    )
    assert sent["status"] == "approved"
    reopened.close()


def test_send_refuses_a_rewritten_address_subject_or_reply_header(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_USERNAME", "desk@example.test")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    north, south = by_name["North"], by_name["South"]
    first = ingest_message(
        store, deal_id, source="paste", supplier_id=north, raw_text="unit price 15.60 CNY per piece",
    )
    second = ingest_message(
        store, deal_id, source="paste", supplier_id=south, raw_text="unit price 14.00 CNY per piece",
    )
    accept_review(store, deal_id, first["event"]["id"], north, chosen_token="15.60")
    accept_review(store, deal_id, second["event"]["id"], south, chosen_token="14.00")
    captured: list[dict] = []

    async def _grab(recipient, body, product, **kwargs):
        captured.append(
            {
                "to": recipient,
                "product": product,
                "reply": kwargs.get("reply_to"),
                "references": kwargs.get("references"),
                "body": body,
            }
        )
        return True

    north_draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == north and item["status"] == "draft")
    sent = asyncio.run(
        send_draft(store, north_draft["id"], human_approved=True, body_sha256=draft_digest(north_draft["body"]), sender=_grab)
    )
    assert sent["status"] == "sent"
    assert captured[0]["to"] == "north@example.test"
    assert captured[0]["product"] == "valve"
    assert captured[0]["reply"] in {None, ""}
    south_draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == south and item["status"] == "draft")
    store.conn.execute(
        "UPDATE suppliers SET address = ? WHERE id = ?",
        ("attacker@evil.example", south),
    )
    store.conn.execute("UPDATE deals SET product = ? WHERE id = ?", ("Stolen valve", deal_id))
    store.conn.execute(
        "UPDATE drafts SET reply_to = ?, references_header = ? WHERE id = ?",
        ("<attacker@evil.example>", "<attacker@evil.example>", south_draft["id"]),
    )
    store.conn.commit()
    with pytest.raises(SendRefused, match="chained supplier"):
        asyncio.run(
            send_draft(
                store,
                south_draft["id"],
                human_approved=True,
                body_sha256=draft_digest(south_draft["body"]),
                sender=_grab,
            )
        )
    assert len(captured) == 1
    store.close()


def test_a_walked_supplier_returns_only_after_reopen(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message, record_outcome, reopen_supplier

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    north, south = by_name["North"], by_name["South"]
    first = ingest_message(
        store, deal_id, source="paste", supplier_id=north, raw_text="unit price 15.60 CNY per piece",
    )
    second = ingest_message(
        store, deal_id, source="paste", supplier_id=south, raw_text="unit price 14.00 CNY per piece",
    )
    accept_review(store, deal_id, first["event"]["id"], north, chosen_token="15.60")
    accept_review(store, deal_id, second["event"]["id"], south, chosen_token="14.00")
    record_outcome(store, deal_id, south, outcome="walk_away", quote=14.0, offer=10.0)
    again = ingest_message(
        store, deal_id, source="paste", supplier_id=south, raw_text="unit price 13.00 CNY per piece",
    )
    fresh = accept_review(store, deal_id, again["event"]["id"], south, chosen_token="13.00")
    south_card = next(row for row in fresh["suppliers"] if row["id"] == south)
    assert "walked away" in str(south_card.get("reserve_note") or "")
    north_card = next(row for row in fresh["suppliers"] if row["id"] == north)
    rivals = [float(item) for item in ((north_card.get("decision_inputs") or {}).get("rival_asks") or [])]
    assert all(abs(item - 13.0) > 0.01 for item in rivals)
    opened = reopen_supplier(store, deal_id, south, human_confirmed=True)
    south_card = next(row for row in opened["suppliers"] if row["id"] == south)
    assert "walked away" not in str(south_card.get("reserve_note") or "")
    assert south_card.get("counter") is not None
    store.close()


def test_spelled_currency_on_another_line_blocks_a_bare_number():
    from src.desk.parse import parse_quote, price_from_token

    samples = (
        "All prices in USD\n31.00 per piece",
        "All prices in US dollars\n31.00 per piece",
        "价格以美元计价\n31.00 per piece",
        "All prices in euros\n31.00 per piece",
    )
    for text in samples:
        reading = parse_quote(text, deal_currency="CNY")
        assert reading["provenance"].get("suggested_token") != "31.00"
        assert price_from_token(text, "31.00", deal_currency="CNY") is None
    assert price_from_token("All prices in CNY\n31.00 per piece", "31.00", deal_currency="CNY") == pytest.approx(31.0)
    assert price_from_token("31.00 per piece, payment in US dollars", "31.00", deal_currency="CNY") is None


def test_health_and_create_report_a_missing_chain_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "desk-key-for-health")
    monkeypatch.delenv("TRADEFLOW_CHAIN_KEY", raising=False)
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["desk"] == "desk sends disabled: chain key missing"
    response = client.post(
        "/api/v1/desk/deals",
        headers={"X-API-Key": "desk-key-for-health"},
        json={
            "product": "valve",
            "quantity": 100,
            "target_price": 4,
            "ceiling_price": 20,
            "risk_tolerance": 0.5,
            "seed": 1,
            "currency": "CNY",
            "suppliers": [
                {"name": "North", "address": "north@example.test"},
                {"name": "South", "address": "south@example.test"},
                {"name": "East", "address": "east@example.test"},
                {"name": "West", "address": "west@example.test"},
            ],
        },
    )
    assert response.status_code == 503
    assert "chain key missing" in response.text
    store = DeskStore(tmp_path / "desk.db")
    assert store.list_deals() == []
    store.close()


def test_create_rolls_back_when_the_chain_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "rollback-key")
    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    reset_stores()

    def _boom(self, *_args, **_kwargs):
        raise RuntimeError("chain down")

    monkeypatch.setattr(DeskStore, "append_chain", _boom)
    store = DeskStore(tmp_path / "desk.db")
    with pytest.raises(RuntimeError, match="chain down"):
        create_deal(
            store,
            product="valve",
            quantity=100,
            target_price=4.0,
            ceiling_price=20.0,
            risk_tolerance=0.5,
            seed=1,
            currency="CNY",
            suppliers=[
                {"name": "North", "address": "north@example.test"},
                {"name": "South", "address": "south@example.test"},
                {"name": "East", "address": "east@example.test"},
                {"name": "West", "address": "west@example.test"},
            ],
        )
    import sqlite3

    other = sqlite3.connect(tmp_path / "desk.db")
    assert other.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 0
    other.close()
    store.close()


def test_a_retired_chain_key_still_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "secret-a")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY_ID", "a")
    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "secret-b")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY_ID", "b")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEYS", "a:secret-a")
    assert store.verify_chain(deal_id)["ok"] is True
    store.append_chain(deal_id, "note", {"text": "after rotation"})
    verified = store.verify_chain(deal_id)
    assert verified["ok"] is True
    assert verified["entries"][0]["key_id"] == "a"
    assert verified["entries"][-1]["key_id"] == "b"
    store.close()
