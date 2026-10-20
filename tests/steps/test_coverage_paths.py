"""Branches the step tests left dark: decision polish, send guard, mailbox, stream."""
from __future__ import annotations

import asyncio
import os
from email.message import EmailMessage
from pathlib import Path

import pytest

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def _open_deal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    from src.desk.service import create_deal
    from src.desk.store import DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        suppliers=SUPPLIERS,
    )
    return store, snapshot


def test_decision_polish_and_withheld_push():
    from src.engine.decision import (
        _newton_polish,
        _push,
        bounded_scalar_max,
        choose_offer,
        hazard_floor,
        objective,
        responses_label,
    )
    from src.engine.hazard_fit import HazardFit, LogisticFit, quote_gap
    from src.engine.reserve import ReserveState, regularized_beta

    def hill(offer: float) -> float:
        return -((offer - 3.0) ** 2)

    assert _newton_polish(hill, 3.0, 0.0, 6.0) == pytest.approx(3.0, abs=0.2)
    assert _newton_polish(lambda offer: offer, 1.0, 0.0, 2.0) == pytest.approx(1.0, abs=0.5)
    assert bounded_scalar_max(hill, 5.0, 1.0) == pytest.approx(3.0, abs=0.05)
    assert bounded_scalar_max(hill, 1.0, 1.0) == pytest.approx(1.0)

    seller = LogisticFit(
        intercept=-1.0, gap=3.0, se_intercept=0.1, se_gap=0.1, n=100, outcome="seller",
        small_gap=0.0, small_gap_knot=0.0,
    )
    fit = HazardFit(seller_ended=seller, thread_no_deal=seller, provenance={})
    assert hazard_floor(10.0, -1.0, 0.0, fit) == 10.0
    assert hazard_floor(10.0, 1.0, 0.0, fit) == 0.0
    assert quote_gap(0.0, 5.0) == 0.0
    state = ReserveState.policy().with_reference(15.0)
    assert objective(20.0, 15.0, 14.0, state, 0.0, fit) < 0
    broken = state.add_rejection(0.99).tighten(0.2)
    assert objective(10.0, 15.0, 14.0, broken, 0.0, fit) < 0
    pushed, kind = _push(0.0, 0.0, state, 0.0, 0.5, fit)
    assert pushed == 0.0 and kind == "golden_section"
    blocked = choose_offer(ask=15.0, outside=14.0, state=broken, risk_cap=0.5, fit=fit)
    assert blocked.blocked is True
    assert blocked.counter_relation == "withheld"
    forced = ReserveState(alpha=0.05, beta=8.0).with_reference(15.0)
    withheld = choose_offer(ask=15.0, outside=14.0, state=forced, effect=0.0, risk_cap=0.0, fit=fit)
    assert withheld.push_withheld or withheld.push_counter <= withheld.default_counter
    assert responses_label(0) == "labelled_policy"
    assert responses_label(30) == "supplier_responses"
    assert regularized_beta(0.0, 2.0, 3.0) == 0.0
    assert regularized_beta(1.0, 2.0, 3.0) == 1.0
    assert 0.0 < regularized_beta(0.3, 1.0, 3.0) < 1.0
    assert 0.0 < regularized_beta(0.3, 3.0, 1.0) < 1.0


def test_reserve_moments_notes_and_inconsistent_mean():
    from src.config.heuristics import HAZARD_MIN_EVENTS
    from src.desk.record import build_record, file_sha256
    from src.engine.reserve import ReserveState

    assert file_sha256(Path("/no/such/hazard.json")) == ""
    plain = ReserveState.from_moments(0.5)
    assert plain.mean() == pytest.approx(0.5, abs=1e-6)
    wide = ReserveState.from_moments(0.5, 10.0)
    assert wide.alpha > 0 and wide.beta > 0
    grown = ReserveState.policy()
    for _ in range(int(HAZARD_MIN_EVENTS)):
        grown = grown.record()
    assert "supplier" in grown.note()
    inconsistent = ReserveState.policy().add_rejection(0.95).tighten(0.2)
    assert inconsistent.mean() != inconsistent.mean()
    assert inconsistent.std() != inconsistent.std()
    assert inconsistent.analytic_cdf(0.5) == 0.0
    full = ReserveState.policy()
    assert full.std() > 0
    cleared = build_record(
        deal_id="d",
        supplier_id="s",
        currency="CNY",
        events=[],
        ask=15.0,
        ceiling=20.0,
        offer=14.0,
        counter=14.0,
        push_counter=14.0,
        half=14.0,
        double=14.0,
        prior_alpha=2.0,
        prior_beta=2.0,
        prior_n=0,
        prior_source="labelled_policy",
        unconfirmed=1,
        hazard_within_cap=True,
    )
    assert cleared.counter is None


def test_project_withholds_a_walk_and_a_failed_check(tmp_path, monkeypatch):
    from src.desk.project import _language, _references, draft_body, project_open_drafts
    from src.desk.service import record_outcome
    from src.engine.reserve import ReserveState

    assert _language(["价格"]) == "zh"
    assert _language(["Hello"]) == "en"
    assert _references("<a@x>", "<a@x>") == "<a@x>"
    assert "元" in draft_body("阀", 12.5, 10, "zh", "CNY")
    assert "USD" in draft_body("valve", 12.5, 10, "en", "USD")
    store, snapshot = _open_deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    record_outcome(store, deal["id"], supplier, outcome="walk_away", quote=15.6, offer=14.0)
    card = {
        "id": supplier,
        "latest_quote": 15.6,
        "counter": 14.0,
        "push_counter": 13.0,
        "blocked": False,
        "sensitivity_half": 14.0,
        "sensitivity_double": 14.0,
    }
    beliefs = {supplier: {"state": ReserveState.policy()}}
    project_open_drafts(
        store,
        deal,
        [card],
        by_supplier={supplier: []},
        beliefs_state=beliefs,
        decision_events=lambda _supplier: [],
        unconfirmed=0,
    )
    assert card["counter"] is None
    quiet = {
        "id": supplier,
        "latest_quote": None,
        "counter": None,
    }
    project_open_drafts(
        store,
        deal,
        [quiet],
        by_supplier={supplier: []},
        beliefs_state=beliefs,
        decision_events=lambda _supplier: [],
        unconfirmed=0,
    )
    failed = {
        "id": supplier,
        "latest_quote": 15.6,
        "counter": 14.0,
        "push_counter": 14.0,
        "blocked": False,
        "sensitivity_half": 14.0,
        "sensitivity_double": 14.0,
    }
    project_open_drafts(
        store,
        deal,
        [failed],
        by_supplier={supplier: [{"raw_text": "单价"}]},
        beliefs_state=beliefs,
        decision_events=lambda _supplier: [],
        unconfirmed=2,
    )
    assert failed["counter"] is None
    store.close()


def test_recompute_helpers_cover_origin_hold_and_reserve(tmp_path, monkeypatch):
    from src.desk.recompute import _headline, _hold_previous, _origin, _reserve_state, recompute

    assert _origin(None) == "live"
    assert _origin({"provenance": {"origin": "fixture"}}) == "fixture"
    assert _origin({"provenance": {"simulated": True}}) == "simulated"
    assert _origin({"provenance": {"fixture": True}}) == "fixture"
    assert _origin({"provenance": {}}) == "live"
    previous = {"counter": 10.0, "accepted_event_ids": ["a"], "blocked": False, "hazard": 0.1}
    held = _hold_previous({"counter": 12.0, "accepted_event_ids": ["a"]}, previous)
    assert held["counter"] == 10.0
    assert held["counter_held"] is True
    assert _hold_previous({"counter": 9.0, "accepted_event_ids": ["a"]}, previous)["counter"] == 9.0
    assert _hold_previous({"counter": 12.0, "accepted_event_ids": ["b"]}, previous)["counter"] == 12.0
    assert _reserve_state([], []) is None
    assert _reserve_state([0.0], []) is None
    state = _reserve_state(
        [10.0, 12.0],
        [
            {"outcome": "agreed", "agreed_price": 9.0},
            {"outcome": "walk_away", "offer": 8.0},
            {"outcome": "continue"},
        ],
        prior_mean=0.8,
        prior_std=0.05,
    )
    assert state is not None and state.n_responses >= 3
    events = [{
        "status": "accepted",
        "supplier_id": "s",
        "parsed": {"unit_price": 15, "freight": 3, "duty_rate": 0.1, "fees": 1, "currency": "CNY"},
        "provenance": {"review_accepted_by": "human"},
    }]
    outcomes = [{"outcome": "agreed", "agreed_price": 14, "supplier_id": "s"}]
    landed = _headline(events, outcomes, unconfirmed=0)
    assert landed["counted"] is True
    other = [{"outcome": "agreed", "agreed_price": 14, "supplier_id": "other"}]
    skipped = _headline(events, other, unconfirmed=0)
    assert skipped["best_first_landed"] is not None
    unit_events = [{
        "status": "accepted",
        "supplier_id": "s",
        "parsed": {"unit_price": 15, "currency": "CNY"},
        "provenance": {"review_accepted_by": "human"},
    }]
    unit = _headline(unit_events, outcomes, unconfirmed=0)
    assert unit["agreed_landed"] == pytest.approx(14.0)
    store, snapshot = _open_deal(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        recompute(store, "ab" * 16)
    store.close()


def test_shadow_rejects_currency_unit_and_amount_mismatches():
    from src.desk.shadow import _mention_agrees, evaluate_shadow

    message = "报价 15.60元/件"
    mention = {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "CNY",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": "15.60元/件",
        "is_question": False,
    }
    assert _mention_agrees(mention, message, 15.60, 1, 100, "CNY") == 15.60
    foreign = dict(mention, currency="USD", evidence_span="15.60 USD")
    usd = evaluate_shadow(
        "price 15.60 USD each",
        {"status": "accepted", "parsed": {"unit_price": 15.60}},
        low=1,
        high=100,
        mentions=[foreign],
        extractor_configured=True,
        currency="USD",
    )
    assert usd["would_accept"] is True
    wrong_currency = evaluate_shadow(
        message,
        {"status": "accepted", "parsed": {"unit_price": 15.60}},
        low=1,
        high=100,
        mentions=[dict(mention, currency="USD")],
        extractor_configured=True,
    )
    assert wrong_currency["would_accept"] is False
    off_amount = evaluate_shadow(
        message,
        {"status": "accepted", "parsed": {"unit_price": 15.60}},
        low=1,
        high=100,
        mentions=[dict(mention, evidence_span="99元/件")],
        extractor_configured=True,
    )
    assert off_amount["would_accept"] is False
    outside = evaluate_shadow(
        message,
        {"status": "accepted", "parsed": {"unit_price": 15.60}},
        low=16,
        high=20,
        mentions=[mention],
        extractor_configured=True,
    )
    assert outside["would_accept"] is False
    assert evaluate_shadow(message, {"status": "review", "parsed": {}}, low=1, high=100)["would_accept"] is False


def test_extract_schema_edges(monkeypatch):
    from src.desk.extract import _chat_url, _from_chat, extract_mentions, mentions_from_payload

    assert _chat_url("https://llm.example/v1/chat/completions").endswith("/chat/completions")
    nested = mentions_from_payload({"mentions": [{"price": 1}, "nope"], "extra": 1})
    assert nested["rejected_rows"] >= 2
    assert mentions_from_payload({"mentions": "nope"})["rejected_rows"] >= 1
    huge = "x" * 70000
    too_big = _from_chat({"choices": [{"message": {"content": huge}}]})
    assert too_big["error"] == "extractor_too_large"
    odd = _from_chat({"choices": [{"message": "text"}]})
    assert odd["error"] == "extractor_malformed"

    def boom(_body):
        raise RuntimeError("down")

    failed = extract_mentions("hi", transport=boom)
    assert failed["error"] == "extractor_failed"
    monkeypatch.delenv("TRADEFLOW_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("TRADEFLOW_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    quiet = extract_mentions("hi")
    assert quiet["configured"] is False


def test_parse_number_shapes_and_store_edges(tmp_path, monkeypatch):
    from src.desk.parse import _number, numeric_tokens
    from src.desk.store import DeskStore, db_path, get_store, reset_stores

    assert _number("1.234,56") == pytest.approx(1234.56)
    assert _number("1,234.50") == pytest.approx(1234.50)
    assert _number("1,5") == pytest.approx(1.5)
    tokens = numeric_tokens("合计 1.234,56 与 1,234.50")
    assert tokens[0][1] == pytest.approx(1234.56)
    monkeypatch.setenv("TRADEFLOW_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("TRADEFLOW_DESK_DB", raising=False)
    assert db_path().endswith("desk.db")
    reset_stores()
    first = get_store(str(tmp_path / "a.db"))
    again = get_store(str(tmp_path / "a.db"))
    assert first is again
    other = get_store(str(tmp_path / "b.db"))
    assert other is not first
    reset_stores()
    store = DeskStore(str(tmp_path / "events.db"))
    deal = store.create_deal(
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=1,
        suppliers=SUPPLIERS,
    )
    store.insert_event(
        deal_id=deal["id"], supplier_id=None, source="paste", raw_text="a",
        parsed={}, confidence="low", status="review", provenance={}, external_id="same",
    )
    duplicate = store.insert_event(
        deal_id=deal["id"], supplier_id=None, source="paste", raw_text="a",
        parsed={}, confidence="low", status="review", provenance={}, external_id="same",
    )
    assert duplicate is None
    assert store.get_decision("missing") is None
    store.close()


def test_send_guard_and_smtp_outcomes(tmp_path, monkeypatch):
    from src.desk.record import build_record
    from src.desk.service import (
        DraftStale,
        SendRefused,
        _draft_rfc822,
        _guard_send,
        _maybe_gmail_drafts,
        draft_digest,
        send_draft,
        set_assumptions,
        smtp_enabled,
        transmit,
    )
    from src.desk.service import accept_review, ingest_message

    store, snapshot = _open_deal(tmp_path, monkeypatch)
    deal = snapshot["deal"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(store, deal["id"], source="paste", raw_text="单价 15.60 元", supplier_id=supplier)
    accept_review(store, deal["id"], opened["event"]["id"], supplier, chosen_token="15.60")
    from src.engine.reserve import ReserveState

    draft = next(item for item in store.drafts(deal["id"]) if item["status"] == "draft")
    original_decision = draft["decision_id"]
    original_sha = store.get_decision(original_decision)["record_sha256"]
    belief = ReserveState.policy()
    record = build_record(
        deal_id=deal["id"],
        supplier_id=supplier,
        currency="CNY",
        events=[{
            "id": opened["event"]["id"],
            "status": "accepted",
            "parsed": {"unit_price": 15.60, "currency": "CNY"},
            "provenance": {"review_accepted_by": "human"},
        }],
        ask=15.60,
        ceiling=20.0,
        offer=15.60,
        counter=15.60,
        push_counter=15.60,
        half=15.60,
        double=15.60,
        prior_alpha=belief.alpha,
        prior_beta=belief.beta,
        prior_n=0,
        prior_source="labelled_policy",
        unconfirmed=0,
        hazard_within_cap=True,
        rival_asks=(),
        risk_cap=0.5,
        reserve_upper=belief.upper,
        reject_ratios=(),
        reference_ask=15.60,
        inputs_stored=True,
    )
    decision_id = store.append_decision(record.as_dict(), record.sha256())
    store.append_chain(
        deal["id"],
        "decision",
        {"decision_id": decision_id, "record_sha256": record.sha256(), "supplier_id": supplier},
    )
    from src.desk.project import draft_body

    rendered = draft_body("valve", 15.60, 10, "zh", "CNY")
    guarded = dict(draft, decision_id=decision_id, body=rendered)
    assert _guard_send(store, guarded, deal)["counter"] == 15.60
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, decision_id=None), deal)
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, decision_id="missing"), deal)
    original_get = store.get_decision

    def mismatched(decision_id_arg):
        item = original_get(decision_id_arg)
        if item is not None and decision_id_arg == decision_id:
            item["record_sha256"] = "0" * 64
        return item

    store.get_decision = mismatched
    with pytest.raises(SendRefused):
        _guard_send(store, guarded, deal)
    store.get_decision = original_get
    original = store.verify_chain
    store.verify_chain = lambda _deal: {"ok": False}
    with pytest.raises(SendRefused):
        _guard_send(store, guarded, deal)
    store.verify_chain = original
    dirty = build_record(
        deal_id=deal["id"], supplier_id=supplier, currency="CNY", events=[],
        ask=15.6, ceiling=20, offer=14, counter=14, push_counter=14, half=14, double=14,
        prior_alpha=1, prior_beta=1, prior_n=0, prior_source="labelled_policy",
        unconfirmed=3, hazard_within_cap=False,
    )
    dirty_id = store.append_decision(dirty.as_dict(), dirty.sha256())
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, decision_id=dirty_id), deal)
    import hashlib
    import json

    def hashed(record_body: dict) -> dict:
        digest = hashlib.sha256(
            json.dumps(record_body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return {"record": record_body, "record_sha256": digest}

    bare = {"deal_id": deal["id"], "supplier_id": supplier, "checks": [{"passed": True}], "unconfirmed": 0}
    above = {
        "deal_id": deal["id"],
        "supplier_id": supplier,
        "checks": [{"passed": True}],
        "unconfirmed": 0,
        "offer": 30.0,
        "ask": 30.0,
        "counter": 30.0,
        "ceiling": 20.0,
    }

    def scripted(decision_id_arg):
        if decision_id_arg == "bare":
            return hashed(bare)
        if decision_id_arg == "above":
            return hashed(above)
        return original_get(decision_id_arg)

    store.get_decision = scripted
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, decision_id="bare"), deal)
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, decision_id="above", body="30.00"), deal)
    store.get_decision = original_get
    high = build_record(
        deal_id=deal["id"], supplier_id=supplier, currency="CNY", events=[],
        ask=30, ceiling=20, offer=30, counter=30, push_counter=30, half=30, double=30,
        prior_alpha=1, prior_beta=1, prior_n=0, prior_source="labelled_policy",
        unconfirmed=0, hazard_within_cap=True,
    )
    if high.counter is not None:
        high_id = store.append_decision(high.as_dict(), high.sha256())
        with pytest.raises(SendRefused):
            _guard_send(store, dict(guarded, decision_id=high_id, body="30.00"), deal)
    with pytest.raises(SendRefused):
        _guard_send(store, dict(guarded, body="no amount here"), deal)

    raw = _draft_rfc822("valve", "14.00", to="ningbo@example.test", reply_to="<q@x>", references="<q@x>")
    assert b"In-Reply-To" in raw
    assert smtp_enabled() is False
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("EMAIL_USERNAME", "desk")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    assert smtp_enabled() is True
    assert asyncio.run(transmit("a@example.test", "body", "valve")) is False

    store.append_chain(
        deal["id"],
        "decision",
        {"decision_id": original_decision, "record_sha256": original_sha, "supplier_id": supplier},
    )
    digest = draft_digest(draft["body"])

    async def down(*_args, **_kwargs):
        return False

    failed = asyncio.run(
        send_draft(store, draft["id"], human_approved=True, body_sha256=digest, sender=down)
    )
    assert failed["status"] == "failed"

    async def narrow(recipient, body, product):
        assert recipient and body and product
        return True

    store.conn.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft["id"],))
    store.conn.commit()
    narrowed = asyncio.run(
        send_draft(store, draft["id"], human_approved=True, body_sha256=digest, sender=narrow)
    )
    assert narrowed["status"] == "sent"
    again = asyncio.run(
        send_draft(store, draft["id"], human_approved=True, body_sha256=digest, sender=narrow)
    )
    assert again["transmitted"] in {0, 1}
    store.conn.execute(
        "UPDATE drafts SET status = 'approved', transmitted = 0 WHERE id = ?",
        (draft["id"],),
    )
    store.conn.commit()
    with pytest.raises(SendRefused, match="already approved"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=digest, sender=down))

    other = snapshot["suppliers"][1]["id"]
    opened_other = ingest_message(
        store, deal["id"], source="paste", raw_text="单价 12.40 元", supplier_id=other,
    )
    accept_review(store, deal["id"], opened_other["event"]["id"], other, chosen_token="12.40")
    other_draft = next(
        item for item in store.drafts(deal["id"])
        if item["supplier_id"] == other and item["status"] == "draft"
    )
    other_digest = draft_digest(other_draft["body"])

    async def explode(*_args, **_kwargs):
        raise RuntimeError("smtp")

    with pytest.raises(RuntimeError):
        asyncio.run(
            send_draft(store, other_draft["id"], human_approved=True, body_sha256=other_digest, sender=explode)
        )
    store.conn.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (other_draft["id"],))
    store.conn.commit()
    with pytest.raises(SendRefused, match="already approved"):
        asyncio.run(
            send_draft(store, other_draft["id"], human_approved=True, body_sha256=other_digest, sender=narrow)
        )

    with pytest.raises(KeyError):
        asyncio.run(send_draft(store, "ab" * 16, human_approved=True, body_sha256="0" * 64, sender=narrow))
    store.conn.execute("UPDATE drafts SET status = 'superseded' WHERE id = ?", (other_draft["id"],))
    store.conn.commit()
    with pytest.raises(DraftStale):
        asyncio.run(
            send_draft(store, other_draft["id"], human_approved=True, body_sha256=other_digest, sender=narrow)
        )
    with pytest.raises(KeyError):
        set_assumptions(store, "ab" * 16, {"include_unverified": 1})

    monkeypatch.setenv("GMAIL_DRAFTS", "1")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "token")

    def boom_token(_opener):
        raise RuntimeError("token")

    monkeypatch.setattr("src.desk.mailbox.refresh_access_token", boom_token)
    _maybe_gmail_drafts(store, deal["id"], {"deal": deal, "drafts": [dict(draft, status="draft")]})

    def token(_opener):
        return "access"

    def created(_opener, _token, _raw, draft_id=None):
        return draft_id or "gmail-1"

    monkeypatch.setattr("src.desk.mailbox.refresh_access_token", token)
    monkeypatch.setattr("src.desk.mailbox.create_gmail_draft", created)
    _maybe_gmail_drafts(store, deal["id"], {"deal": deal, "drafts": [dict(draft, status="draft", supplier_id=supplier)]})

    def boom_draft(*_args, **_kwargs):
        raise RuntimeError("draft")

    monkeypatch.setattr("src.desk.mailbox.create_gmail_draft", boom_draft)
    _maybe_gmail_drafts(store, deal["id"], {"deal": deal, "drafts": [dict(draft, status="draft", supplier_id=supplier)]})
    store.close()


def test_mailbox_poll_and_bus(tmp_path, monkeypatch):
    import imaplib
    import ssl

    from src.desk import mailbox
    from src.desk.mailbox import MailboxError
    from src.desk.service import (
        DeskBus,
        _external_id,
        ingest_mailbox_items,
        poll_forever,
        poll_gmail_once,
        poll_imap_once,
        poll_seconds,
        polling_enabled,
    )

    real_open = mailbox._open_imap

    def boom_ssl(*_args, **_kwargs):
        raise ssl.SSLError("bad")

    monkeypatch.setattr(mailbox, "_open_imap", boom_ssl)
    with pytest.raises(MailboxError):
        mailbox.pull_window(host="mail.example", port=993, username="u", password="p", use_ssl=True)

    def boom_os(*_args, **_kwargs):
        raise OSError("down")

    monkeypatch.setattr(mailbox, "_open_imap", boom_os)
    with pytest.raises(MailboxError):
        mailbox.pull_window(host="127.0.0.1", port=143, username="u", password="p", use_ssl=False)
    with pytest.raises(MailboxError):
        real_open("mail.example", 143, "u", "p", False)
    monkeypatch.setattr(mailbox, "_open_imap", real_open)

    raw = EmailMessage()
    raw["From"] = "Ningbo <ningbo@example.test>"
    raw["Message-ID"] = "<q@example.test>"
    raw.set_content("单价 15.60 元")
    blob = raw.as_bytes()

    class Client:
        untagged_responses = {"OK": [b"UIDVALIDITY 9"]}

        def __init__(self):
            self.logged_out = False

        def login(self, *_args):
            return "OK", []

        def select(self, *_args, **_kwargs):
            return "OK", [b""]

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"4"]
            if "HEADER" in args[-1]:
                return "NO", []
            return "OK", [(b"4", blob)]

        def logout(self):
            raise RuntimeError("logout")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *args, **kwargs: Client())
    messages, validity = mailbox.pull_window(
        host="mail.example", port=993, username="u", password="p", use_ssl=True, skip_ids=set(),
    )
    assert validity == "9"
    assert messages and messages[0]["external_id"] == "<q@example.test>"

    class EmptySelect(Client):
        def select(self, *_args, **_kwargs):
            return "NO", []

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *args, **kwargs: EmptySelect())
    with pytest.raises(MailboxError):
        mailbox.pull_window(host="mail.example", port=993, username="u", password="p", use_ssl=True)

    multipart = EmailMessage()
    multipart["From"] = "Ningbo <ningbo@example.test>"
    multipart.set_content("plain body")
    multipart.add_attachment(b"file", maintype="application", subtype="octet-stream", filename="a.bin")
    parsed = mailbox.parse_rfc822(multipart.as_bytes())
    assert "plain" in parsed["body"]
    assert mailbox.parse_rfc822(b"Subject: hi\r\n\r\nnot-bytes")["body"] in {"", "not-bytes"}
    assert _external_id({"raw": b"abc"}) .startswith("sha256:")

    store, snapshot = _open_deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    counts = ingest_mailbox_items(
        store,
        [
            {"sender": "stranger@example.test", "body": "hi", "message_id": "<s@x>"},
            {"sender": "ningbo@example.test", "body": "单价 15.60 元", "message_id": "<q@example.test>", "uid": "4", "uidvalidity": "9"},
            {"sender": "ningbo@example.test", "body": "单价 15.60 元", "message_id": "<q@example.test>", "uid": "4", "uidvalidity": "9"},
        ],
        deal_id=deal_id,
        channel="imap",
        mailbox_key="imap:test",
    )
    assert counts["ignored"] == 1
    assert counts["ingested"] == 1
    assert counts["duplicates"] == 1
    monkeypatch.delenv("IMAP_HOST", raising=False)
    assert poll_imap_once(store, deal_id=deal_id)["status"] == "not_configured"
    assert poll_gmail_once(store, deal_id=deal_id)["status"] == "not_configured"
    monkeypatch.setenv("IMAP_HOST", "mail.example")
    monkeypatch.setenv("IMAP_USERNAME", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    monkeypatch.setattr(
        "src.desk.mailbox.pull_window",
        lambda **_kwargs: ([{"sender": "ningbo@example.test", "body": "再次 14.20 元", "message_id": "<n@x>"}], "9"),
    )
    polled = poll_imap_once(store, deal_id=deal_id)
    assert polled["status"] == "ok"
    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "token")
    monkeypatch.setattr("src.desk.mailbox.pull_gmail_window", lambda *_args, **_kwargs: [])
    assert poll_gmail_once(store, deal_id=deal_id)["status"] == "ok"
    assert polling_enabled() is True
    monkeypatch.setenv("IMAP_POLL_SECONDS", "nope")
    assert poll_seconds() == 60
    monkeypatch.setenv("IMAP_POLL_SECONDS", "0")
    assert poll_seconds() == 1

    def fail_once():
        raise RuntimeError("poll")

    monkeypatch.setattr("src.desk.service.poll_configured", fail_once)

    async def cycle():
        stop = asyncio.Event()

        async def trip():
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(poll_forever(stop), trip())

    monkeypatch.setattr("src.desk.service.poll_seconds", lambda: 1)
    asyncio.run(cycle())

    bus = DeskBus()
    queue = bus.subscribe(deal_id)
    for index in range(9):
        bus.publish(deal_id, {"n": index})
    assert queue.qsize() == 8
    bus.unsubscribe(deal_id, queue)
    bus.publish(deal_id, {"n": 10})
    store.close()


def test_stream_emits_a_snapshot_then_a_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "desk-coverage-key-01")
    from fastapi.testclient import TestClient
    from src.app.main import app
    from src.desk.service import BUS
    from src.desk.store import reset_stores

    reset_stores()
    client = TestClient(app)
    headers = {"X-API-Key": "desk-coverage-key-01"}
    created = client.post(
        "/api/v1/desk/deals",
        headers=headers,
        json={
            "product": "valve",
            "quantity": 10,
            "target_price": 12,
            "ceiling_price": 20,
            "suppliers": SUPPLIERS,
        },
    )
    assert created.status_code == 200, created.text
    deal_id = created.json()["deal"]["id"]
    missing = client.get("/api/v1/desk/deals/" + ("cd" * 16) + "/stream", headers=headers)
    assert missing.status_code == 404
    with client.stream("GET", f"/api/v1/desk/deals/{deal_id}/stream", headers=headers) as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        first = next(line for line in lines if line)
        assert first.startswith("data:")
        BUS.publish(deal_id, {"published": True})
        second = next(line for line in lines if line)
        assert "published" in second
    reset_stores()

