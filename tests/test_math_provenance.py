"""Provenance: prices come from formulas, and constants stay registered."""
from __future__ import annotations

import numpy as np
import pytest

from scripts.check_decision_literals import scan
from src.desk.parse import parse_quote
from src.engine.bayesian_v2 import robust_bayes_update
from src.engine.coupled import sample_joint_scenarios
from src.engine.hazard import HazardBelief


def test_registry_covers_decision_literals():
    assert scan() == []


def test_european_thousands_stay_one_amount():
    reading = parse_quote("单价 5.254,60元")
    assert reading["status"] == "review"
    assert reading["parsed"]["unit_price"] is None
    assert reading["provenance"]["review_reason"] == "unit mismatch"
    assert reading["provenance"]["candidates"] == ["5.254,60"]
    assert reading["parsed"]["quoted_amount"] == pytest.approx(5254.60)


def test_currency_less_unit_price_is_review():
    reading = parse_quote("Unit price 14.80")
    assert reading["status"] == "review"
    assert reading["parsed"]["unit_price"] is None


def test_shared_factor_correlation_matches_rho():
    rng = np.random.default_rng(0)
    drawn = sample_joint_scenarios(
        rng,
        np.array([2.0, 2.0]),
        np.array([0.1, 0.1]),
        20000,
        rho=0.3,
    )
    reserves = drawn["reserves"]
    corr = np.corrcoef(reserves[0], reserves[1])[0, 1]
    assert corr == pytest.approx(0.3, abs=0.02)
    assert float(np.std(reserves[0])) == pytest.approx(0.1, abs=0.01)


def test_heavy_tail_quote_still_moves_the_mean():
    updated = robust_bayes_update(1.16, 0.10, 2.50)
    assert updated.posterior_mean > 1.16
    assert updated.interval_model == "student_t"
    assert updated.robust_weight < 0.2


def test_clopper_pearson_upper_is_not_one_for_a_single_success():
    from scripts.benchmark_stats import clopper_pearson_upper

    bound = clopper_pearson_upper(1, 20, alpha=0.05)
    assert 0.05 < bound < 0.5


def test_hazard_outcome_moves_the_effect():
    belief = HazardBelief()
    before = belief.mean_effect()
    belief.update(walked=True, quote=1.4, offer=1.0, reserve=1.1)
    assert belief.n_events == 1
    assert belief.mean_effect() != pytest.approx(before)


def test_negotiate_and_auction_require_a_ceiling(monkeypatch):
    monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from src.app.main import app

    client = TestClient(app)
    missing = client.post("/negotiate", json={"target_price": 12, "observed_offer": 1.25})
    assert missing.status_code == 422
    auction = client.post("/api/v1/multi-auction/run", json={"target_price": 12})
    assert auction.status_code == 422


def test_unknown_round_does_not_create_a_session(monkeypatch):
    monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from src.app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/sessions/no-such-session/round",
        json={"opponent_offer_price": 15.0, "opponent_raw_text": "单价 15.00 元"},
    )
    assert response.status_code == 404


def test_fixture_and_simulated_are_server_side(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_API_KEY", "math-provenance-key")
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="umbrella",
        quantity=100,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=1,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test"},
            {"name": "Wenzhou", "address": "b@example.test"},
            {"name": "Yiwu", "address": "c@example.test"},
            {"name": "Shaoxing", "address": "d@example.test"},
        ],
    )
    deal_id = snapshot["deal"]["id"]
    live = snapshot["suppliers"][0]["id"]
    simulated_id = snapshot["suppliers"][1]["id"]
    fixture_id = snapshot["suppliers"][2]["id"]
    lied = ingest_message(
        store,
        deal_id,
        source="chat-import",
        raw_text="单价 15.00 元",
        supplier_id=live,
        provenance_extra={"simulated": True, "page_url": "https://shop.example/item"},
    )
    assert lied["event"]["status"] == "review"
    assert lied["event"]["provenance"]["origin"] == "simulated"
    simulated = ingest_message(
        store,
        deal_id,
        source="chat-import",
        raw_text="单价 16.00 元",
        supplier_id=simulated_id,
        provenance_extra={"page_url": "http://127.0.0.1/mock_chat.html", "simulated": False},
        freight=2.5,
    )
    assert simulated["event"]["status"] == "review"
    assert simulated["event"]["provenance"]["simulated"] is True
    accepted = accept_review(store, deal_id, simulated["event"]["id"], simulated_id, chosen_token="16.00", human_confirmed=True)
    card = next(row for row in accepted["suppliers"] if row["id"] == simulated_id)
    assert card["origin"] == "simulated"
    assert card["excluded_from_decision"] is True
    kept = store.event(deal_id, simulated["event"]["id"])
    assert kept["provenance"]["simulated"] is True
    assert kept["parsed"]["freight"] == pytest.approx(2.5)
    fixture = ingest_message(
        store,
        deal_id,
        source="email",
        raw_text="X-TradeFlow-Fixture: sample\n单价 17.00 元",
        supplier_id=fixture_id,
    )
    assert fixture["event"]["status"] == "review"
    assert fixture["event"]["provenance"]["origin"] == "fixture"
