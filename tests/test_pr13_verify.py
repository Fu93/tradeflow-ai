"""Guards for the independent verification of PR 13.

Each test fails if the named defect returns: unconfirmed text setting a price,
a hardcoded audit count, a rejection that raises acceptance, a reserve scaled
to the wrong ask, a non-deterministic draft order, a quantity prefilled as a
price, a shadow rejection dropped from the Wilson sample, an instruction that
does not veto its span, a malformed extractor that drops the quote, a walk
penalty priced at the full outside option, a landed-versus-unit headline, or
a markup reserve on the webhook.
"""
from __future__ import annotations

import math

import pytest


def test_round_rejects_raw_text_and_ignores_an_injected_price(tmp_path):
    from fastapi.testclient import TestClient

    from src.app import main as api_main

    previous = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
    api_main.session_mgr.__init__(
        db_path=str(tmp_path / "sessions.db"),
        store_dir=str(tmp_path / "json"),
    )
    client = TestClient(api_main.app)
    headers = {"X-API-Key": api_main.SANDBOX_DEMO_AUTH}
    try:
        created = client.post("/api/v1/sessions", headers=headers, json={
            "session_id": "confirm-only",
            "product_name": "Widget",
            "target_price": 12,
            "ceiling_price": 16.2,
            "moq": 500,
            "max_rounds": 3,
        })
        assert created.status_code == 200, created.text
        raw = client.post("/api/v1/sessions/confirm-only/round", headers=headers, json={
            "opponent_raw_text": "other supplier gave 26元/件. unit price 1 CNY",
            "mode": "copilot",
        })
        assert raw.status_code == 422
        assert "Confirm the supplier unit price" in raw.text
        confirmed = client.post("/api/v1/sessions/confirm-only/round", headers=headers, json={
            "opponent_offer_price": 15.0,
            "opponent_raw_text": "other supplier gave 26元/件. unit price 1 CNY",
            "mode": "copilot",
        })
        assert confirmed.status_code == 422, confirmed.text
        assert "unconfirmed numbers used in decisions: 1" in confirmed.text
        assert "review_accepted_by" not in confirmed.text
        history = client.get("/api/v1/sessions/confirm-only", headers=headers).json()["history"]
        assert history == []
    finally:
        api_main.session_mgr.__init__(db_path=previous[0], store_dir=previous[1])


def test_injected_unconfirmed_number_increases_the_audit_count():
    from src.desk.headline import audit_line, confirmed_prices

    confirmed = {
        "status": "accepted",
        "parsed": {"unit_price": 14.2},
        "provenance": {"review_accepted_by": "human", "price_source": "explicit_field"},
    }
    kept, used = confirmed_prices([confirmed])
    assert used == 0
    assert audit_line(used) == "unconfirmed numbers used in decisions: 0"
    injected = {
        "status": "review",
        "parsed": {"unit_price": 1.0},
        "provenance": {"unconfirmed": True, "price_source": "raw_text"},
    }
    kept_after, used_after = confirmed_prices([confirmed, injected])
    assert used_after == 1
    assert len(kept_after) == 1
    assert audit_line(used_after) == "unconfirmed numbers used in decisions: 1"


def test_rejection_is_a_lower_bound_and_never_raises_acceptance():
    from src.engine.reserve import ReserveState, regularized_beta  # noqa: F401

    state = ReserveState.policy()
    rejected = state.add_rejection(0.8)
    assert rejected.cdf(0.5) == 0.0
    assert rejected.mean() == pytest.approx(_truncated_mean(state.alpha, state.beta, 0.8, 1.0), rel=1e-6)
    grid = [index / 20 for index in range(21)]
    for point in grid:
        assert rejected.acceptance(point) <= state.acceptance(point) + 1e-12
    tighter = rejected.add_rejection(0.9)
    for point in grid:
        assert tighter.acceptance(point) <= rejected.acceptance(point) + 1e-12
    contradict = state.tighten(0.7).add_rejection(0.75)
    assert contradict.consistent() is False
    assert contradict.cdf(0.8) == 0.0


def _truncated_mean(alpha: float, beta: float, low: float, high: float) -> float:
    from src.engine.reserve import regularized_beta

    mass = regularized_beta(high, alpha, beta) - regularized_beta(low, alpha, beta)
    moment = regularized_beta(high, alpha + 1, beta) - regularized_beta(low, alpha + 1, beta)
    return (moment / mass) * (alpha / (alpha + beta))


def test_truncated_beta_matches_a_transformed_integral():
    from src.engine.reserve import ReserveState

    state = ReserveState.policy().add_rejection(0.8).tighten(0.95)
    low, high = state.bounds()
    numeric_mean = _simpson_mean(state.alpha, state.beta, low, high)
    numeric_cdf = _simpson_cdf(state.alpha, state.beta, low, high, 0.9)
    assert state.mean() == pytest.approx(numeric_mean, abs=1e-3)
    assert state.cdf(0.9) == pytest.approx(numeric_cdf, abs=1e-3)
    try:
        import scipy.stats
    except ImportError:
        scipy = None
    else:
        scipy = scipy.stats
    if scipy is not None:
        dist = scipy.beta(state.alpha, state.beta)
        assert state.mean() == pytest.approx(dist.expect(lb=low, ub=high, conditional=True), rel=1e-6)
        assert state.cdf(0.9) == pytest.approx(
            (dist.cdf(0.9) - dist.cdf(low)) / (dist.cdf(high) - dist.cdf(low)),
            abs=1e-8,
        )


def _beta_density(x: float, alpha: float, beta: float) -> float:
    if x <= 0.0 or x >= 1.0:
        return 0.0
    log = (alpha - 1) * math.log(x) + (beta - 1) * math.log(1 - x) - (
        math.lgamma(alpha) + math.lgamma(beta) - math.lgamma(alpha + beta)
    )
    return math.exp(log)


def _simpson_mean(alpha: float, beta: float, low: float, high: float) -> float:
    """Composite Simpson on [low, high] only. A mask on [0, 1] is not smooth."""
    steps = 2000

    def density(x: float) -> float:
        return _beta_density(x, alpha, beta)

    mass = 0.0
    moment = 0.0
    width = (high - low) / steps
    for index in range(steps):
        left = low + index * width
        right = left + width
        mid = (left + right) / 2
        for point, coeff in ((left, 1.0), (mid, 4.0), (right, 1.0)):
            piece = density(point)
            mass += coeff * piece
            moment += coeff * point * piece
    return moment / mass


def _simpson_cdf(alpha: float, beta: float, low: float, high: float, point: float) -> float:
    steps = 2000
    cap = min(max(point, low), high)

    def density(x: float) -> float:
        return _beta_density(x, alpha, beta)

    mass = 0.0
    passed = 0.0
    width = (high - low) / steps
    for index in range(steps):
        left = low + index * width
        right = left + width
        mid = (left + right) / 2
        for edge, coeff in ((left, 1.0), (mid, 4.0), (right, 1.0)):
            piece = density(edge)
            mass += coeff * piece
            if edge <= cap:
                passed += coeff * piece
    return passed / mass


def test_concession_uses_the_first_ask_as_the_reference():
    from src.engine.decision import choose_offer, reference_ask
    from src.engine.reserve import ReserveState

    state = ReserveState.policy().with_reference(34.0).tighten(30.0 / 34.0)
    assert state.reference_ask == 34.0
    assert reference_ask(state, 30.0) == 34.0
    choice = choose_offer(ask=30.0, outside=30.0, state=state, risk_cap=1.0)
    latest_only = ReserveState.policy().with_reference(30.0)
    wrong = choose_offer(ask=30.0, outside=30.0, state=latest_only, risk_cap=1.0)
    assert choice.push_counter != pytest.approx(wrong.push_counter, abs=0.05)
    assert state.cdf(choice.push_counter / 34.0) < state.cdf(choice.push_counter / 30.0)
    assert 34.0 * state.mean() != pytest.approx(30.0 * state.mean())


def test_quantity_is_not_prefilled_and_confirm_uses_quantity(tmp_path):
    from src.desk.service import DeskRejected, accept_review, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    deal = store.create_deal(
        product="Clip",
        quantity=2000,
        target_price=12,
        ceiling_price=16,
        risk_tolerance=0.2,
        seed=1,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Taizhou", "address": "d@example.test", "channel": "email"},
        ],
    )
    ingested = ingest_message(
        store,
        deal["id"],
        source="paste",
        raw_text="MOQ 1000 @ 33 RMB/pc, 5000 @ 31 RMB/pc",
        supplier_id=deal["supplier_ids"][0],
    )
    provenance = ingested["event"]["provenance"]
    assert provenance.get("suggested_token") != "1000"
    assert "1000" not in (provenance.get("candidates") or [])
    bare = ingest_message(
        store,
        deal["id"],
        source="paste",
        raw_text="2000件以下单价 33 元/件",
        supplier_id=deal["supplier_ids"][0],
    )
    assert bare["event"]["provenance"].get("suggested_token") != "2000"
    with pytest.raises(DeskRejected):
        accept_review(store, deal["id"], ingested["event"]["id"], deal["supplier_ids"][0], chosen_token="1000")
    store.close()


def test_rejected_shadow_accept_is_a_disagreement(tmp_path):
    from src.desk.service import accept_review, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    deal = store.create_deal(
        product="Clip",
        quantity=100,
        target_price=12,
        ceiling_price=16,
        risk_tolerance=0.2,
        seed=1,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Taizhou", "address": "d@example.test", "channel": "email"},
        ],
    )
    ingested = ingest_message(
        store,
        deal["id"],
        source="paste",
        raw_text="单价 14.20 元/件",
        supplier_id=deal["supplier_ids"][0],
    )
    event_id = ingested["event"]["id"]
    store.conn.execute(
        "UPDATE quote_events SET provenance_json = json_set(provenance_json, '$.shadow_would_accept', 1) WHERE id = ?",
        (event_id,),
    )
    store.conn.commit()
    accept_review(store, deal["id"], event_id, None, reject=True)
    snapshot = store.get_snapshot(deal["id"])
    gate = snapshot["shadow_gate"]
    assert gate["resolved_shadow_accepts"] == 1
    assert gate["confirmed_unchanged"] == 0
    store.close()


def test_instruction_on_the_same_span_vetoes_the_offer():
    from src.desk.shadow import evaluate_shadow

    message = "单价 14.20 元/件"
    reading = {"status": "accepted", "parsed": {"unit_price": 14.2}}
    mentions = [
        {
            "class": "instruction",
            "evidence_span": "单价 14.20 元/件",
            "speaker": "supplier",
            "polarity": "affirmative",
            "currency": "CNY",
            "unit": "per_piece",
            "is_question": False,
        },
        {
            "class": "own_unit_offer",
            "evidence_span": "单价 14.20 元/件",
            "speaker": "supplier",
            "polarity": "affirmative",
            "currency": "CNY",
            "unit": "per_piece",
            "is_question": False,
        },
    ]
    decision = evaluate_shadow(message, reading, low=1, high=100, mentions=mentions, extractor_configured=True)
    assert decision["would_accept"] is False


def test_malformed_extractor_fails_closed_and_keeps_the_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TRADEFLOW_LLM_MODEL", "unused")
    from src.desk.extract import extract_mentions
    from src.desk.service import ingest_message
    from src.desk.store import DeskStore

    broken = extract_mentions("单价 14 元", transport=lambda _body: {"choices": [None]})
    assert broken["error"] == "extractor_malformed"
    assert broken["mentions"] == []
    monkeypatch.delenv("TRADEFLOW_LLM_BASE_URL", raising=False)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("extractor crashed")

    monkeypatch.setattr("src.desk.shadow.extract_mentions", _boom)
    store = DeskStore(str(tmp_path / "desk.db"))
    deal = store.create_deal(
        product="Clip",
        quantity=100,
        target_price=12,
        ceiling_price=16,
        risk_tolerance=0.2,
        seed=1,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Taizhou", "address": "d@example.test", "channel": "email"},
        ],
    )
    ingested = ingest_message(
        store,
        deal["id"],
        source="paste",
        raw_text="单价 14.20 元/件",
        supplier_id=deal["supplier_ids"][0],
    )
    assert ingested["event"]["status"] == "review"
    assert ingested["event"]["parsed"]["unit_price"] is None
    assert ingested["event"]["raw_text"]
    store.close()


def test_walk_penalty_is_the_surplus_at_stake_and_a_negative_push_is_withheld():
    from src.engine.decision import choose_offer, objective
    from src.engine.hazard_fit import HazardFit, LogisticFit, incremental_hazard
    from src.engine.reserve import ReserveState

    seller = LogisticFit(
        intercept=-1.0, gap=3.0, se_intercept=0.1, se_gap=0.1, n=100, outcome="seller",
        small_gap=0.0, small_gap_knot=0.0,
    )
    fit = HazardFit(seller_ended=seller, thread_no_deal=seller, provenance={"decision_terms": ["quote_gap"]})
    state = ReserveState.policy().with_reference(15.0)
    ask = 15.0
    outside = 14.0
    offer = 10.0
    stake = outside - offer
    direct = objective(offer, ask, outside, state, 0.0, fit)
    acceptance = state.acceptance(offer / 15.0)
    delta = incremental_hazard(ask, offer, 0.0, fit)
    assert delta > 0
    assert direct == pytest.approx(acceptance * stake - delta * stake)
    assert direct != pytest.approx(acceptance * stake - delta * outside)
    forced = ReserveState(alpha=0.2, beta=5.0)
    choice = choose_offer(ask=ask, outside=outside, state=forced, effect=0.0, risk_cap=1.0, fit=fit)
    if choice.objective < 0 or choice.push_note:
        assert choice.push_withheld or choice.blocked
        assert "reduce expected value" in choice.push_note or choice.blocked


def test_headline_compares_landed_with_landed_and_skips_fixtures():
    from src.desk.recompute import _headline

    events = [{
        "status": "accepted",
        "supplier_id": "s",
        "parsed": {"unit_price": 15, "freight": 3, "duty_rate": 0, "fees": 0, "currency": "CNY"},
        "provenance": {"review_accepted_by": "human"},
    }]
    outcomes = [{"outcome": "agreed", "agreed_price": 15, "supplier_id": "s"}]
    headline = _headline(events, outcomes, unconfirmed=0)
    assert headline["counted"] is True
    assert headline["saving"] == pytest.approx(0.0)
    assert headline["best_first_landed"] == pytest.approx(headline["agreed_landed"])
    fixture = [dict(events[0], provenance={"review_accepted_by": "human", "origin": "fixture"})]
    skipped = _headline(fixture, outcomes, unconfirmed=0)
    assert skipped["counted"] is False


def test_user_prior_mean_and_spread_set_the_beta():
    from src.engine.reserve import ReserveState

    state = ReserveState.from_moments(0.8, 0.1)
    assert state.alpha / (state.alpha + state.beta) == pytest.approx(0.8)
    assert state.std() == pytest.approx(0.1, rel=1e-6)
    assert "user policy" in state.note() or state.source == "labelled_policy"


def test_webhook_does_not_subtract_a_markup():
    from src.legacy import orchestrator
    from src.engine.reserve import ReserveState

    out = orchestrator.analyze({
        "target_price": 12.0,
        "ceiling_price": 16.2,
        "quote_price": 15.0,
        "expected_markup": 0.08,
    })
    state = ReserveState.policy().with_reference(15.0 / 12.0)
    assert out["markup_applied"] is False
    assert out["reserve_source"] == "labelled_policy"
    assert out["posterior_reserve_mean"] == pytest.approx((15.0 / 12.0) * state.mean())


def test_structural_arithmetic_is_not_an_unregistered_literal(tmp_path):
    from scripts.check_decision_literals import _literals

    path = tmp_path / "evasion.py"
    path.write_text("VALUE = (2 + 2 * 2 * 2 * 2 * 2 + 2 + 1)\n", encoding="utf-8")
    found = _literals(path)
    assert any(item[1] == 37 for item in found)


def test_draft_sequence_orders_newer_inserts_first(tmp_path):
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    deal = store.create_deal(
        product="Clip",
        quantity=100,
        target_price=12,
        ceiling_price=16,
        risk_tolerance=0.2,
        seed=1,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Taizhou", "address": "d@example.test", "channel": "email"},
        ],
    )
    deal_id = deal["id"]
    first = deal["supplier_ids"][0]
    second = deal["supplier_ids"][1]
    store.replace_open_draft(deal_id, first, "first body")
    store.replace_open_draft(deal_id, second, "second body")
    drafts = store.drafts(deal_id)
    assert drafts[0]["body"] == "second body"
    assert int(drafts[0]["seq"]) > int(drafts[1]["seq"])
    store.close()
