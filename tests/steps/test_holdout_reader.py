"""Holdout reader misses. The sentences are original; they are not supplier mail."""
from __future__ import annotations

import hashlib
import json

import pytest


def test_deal_currency_comes_from_the_deal_and_its_symbols():
    from src.desk.parse import parse_quote, price_from_token
    from src.desk.shadow import evaluate_shadow

    usd = parse_quote("Our offer is $18.40 per piece.", deal_currency="USD")
    assert usd["status"] == "accepted"
    assert usd["parsed"]["unit_price"] == pytest.approx(18.40)
    assert price_from_token("Quoted $18.40 per piece", "18.40", deal_currency="USD") == pytest.approx(18.40)
    assert price_from_token("单价 18.40 元", "18.40", deal_currency="USD") is None
    eur = parse_quote("Unit price €4.20 each.", deal_currency="EUR")
    assert eur["parsed"]["unit_price"] == pytest.approx(4.20)
    yuan = parse_quote("单价 18.40 元/件", deal_currency="CNY")
    assert yuan["parsed"]["unit_price"] == pytest.approx(18.40)

    reading = {"status": "accepted", "parsed": {"unit_price": 18.40}}
    mention = {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "USD",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": "$18.40/pc",
        "is_question": False,
    }
    agreed = evaluate_shadow(
        "offer $18.40/pc",
        reading,
        low=1,
        high=100,
        mentions=[mention],
        extractor_configured=True,
        currency="USD",
    )
    assert agreed["would_accept"] is True
    yuan_span = dict(mention, evidence_span="18.40元/件")
    refused = evaluate_shadow(
        "offer 18.40元/件",
        reading,
        low=1,
        high=100,
        mentions=[yuan_span],
        extractor_configured=True,
        currency="USD",
    )
    assert refused["would_accept"] is False


def test_question_marks_are_not_questions_by_themselves():
    from src.desk.parse import parse_quote

    forum = parse_quote("Unit price??18.40??USD per piece", deal_currency="USD")
    assert forum["status"] == "accepted"
    assert forum["parsed"]["unit_price"] == pytest.approx(18.40)
    asked = parse_quote("Can you do 18.40 USD per piece?", deal_currency="USD")
    assert asked["status"] == "review"
    structural = parse_quote("Is 18.40 USD per piece ok?", deal_currency="USD")
    assert structural["status"] == "review"


def test_quoted_supplier_text_is_kept_and_buyer_echoes_are_not():
    from src.desk.parse import parse_quote

    whole = parse_quote("> Unit price 18.40 USD per piece", deal_currency="USD")
    assert whole["parsed"]["unit_price"] == pytest.approx(18.40)
    preface = parse_quote(
        "Latest offer follows.\n> Unit price 18.40 USD per piece",
        deal_currency="USD",
    )
    assert preface["parsed"]["unit_price"] == pytest.approx(18.40)
    echo = parse_quote(
        "Unit price 18.40 USD per piece\n> your target was 10.00 USD per piece",
        deal_currency="USD",
    )
    assert echo["status"] == "accepted"
    assert echo["parsed"]["unit_price"] == pytest.approx(18.40)
    history = parse_quote(
        "We can do 31.90 CNY now.\n\nOn Mon, 12 Oct 2026 at 09:14, buyer wrote:\n"
        "> Unit price 35.00 CNY per piece"
    )
    assert history["parsed"]["unit_price"] is None


def test_lead_time_ranges_are_not_price_ranges():
    from src.desk.parse import parse_quote

    lead = parse_quote(
        "Unit price 18.40 USD per piece, lead time 20~30 days",
        deal_currency="USD",
    )
    assert lead["status"] == "accepted"
    assert lead["parsed"]["unit_price"] == pytest.approx(18.40)
    assert lead["provenance"]["review_reason"] is None
    chinese = parse_quote("单价 32.60 元/件，交期 20~30 天")
    assert chinese["parsed"]["unit_price"] == pytest.approx(32.60)
    band = parse_quote("Unit price 18-20 USD per piece", deal_currency="USD")
    assert band["status"] == "review"
    assert band["provenance"]["review_reason"] == "price range"


def test_invoice_rows_inherit_the_header_currency():
    from src.desk.parse import parse_quote

    table = "\n".join(
        [
            "Proforma",
            "Currency: USD",
            "Item | Qty | Unit price",
            "bracket | 100 | 18.40",
        ]
    )
    got = parse_quote(table, deal_currency="USD")
    assert got["status"] == "review"
    assert got["parsed"]["unit_price"] is None
    assert got["provenance"]["review_reason"] == "unrecognised term: Proforma"
    assert "18.40" in got["provenance"]["candidates"]
    yuan = "\n".join(["币种：元", "品名 | 数量 | 单价", "支架 | 100 | 32.60"])
    cny = parse_quote(yuan, deal_currency="CNY")
    assert cny["parsed"]["unit_price"] is None
    assert cny["provenance"]["review_reason"] == "unrecognised term: 币种"
    assert "32.60" in cny["provenance"]["candidates"]
    total = "\n".join(
        [
            "Currency: USD",
            "Item | Qty | Amount",
            "bracket | 100 | 1840.00",
        ]
    )
    assert parse_quote(total, deal_currency="USD")["parsed"]["unit_price"] is None


def test_free_sample_does_not_hide_the_goods_price():
    from src.desk.parse import parse_quote

    offer = parse_quote("Free sample we offer 18.40 USD per piece", deal_currency="USD")
    assert offer["status"] == "accepted"
    assert offer["parsed"]["unit_price"] == pytest.approx(18.40)
    fee = parse_quote("Sample fee 40 USD", deal_currency="USD")
    assert fee["parsed"]["unit_price"] is None
    assert fee["status"] == "review"


def test_identical_prices_are_one_price():
    from src.desk.parse import parse_quote

    repeated = parse_quote(
        "Unit price 18.40 USD per piece. Unit price 18.40 USD per piece.",
        deal_currency="USD",
    )
    assert repeated["status"] == "accepted"
    assert repeated["parsed"]["unit_price"] == pytest.approx(18.40)
    echoed = parse_quote(
        "Unit price 18.40 USD per piece. Can you also confirm 18.40 USD per piece?",
        deal_currency="USD",
    )
    assert echoed["status"] == "accepted"
    assert echoed["parsed"]["unit_price"] == pytest.approx(18.40)
    two = parse_quote(
        "Unit price 18.40 USD per piece. Unit price 19.10 USD per piece.",
        deal_currency="USD",
    )
    assert two["status"] == "review"
    assert two["provenance"]["review_reason"] == "more than one price"


def test_digits_glued_to_letters_are_not_amounts():
    from src.desk.parse import parse_quote
    from src.desk.span_read import read_amounts

    assert read_amounts("3X USD/PC") == []
    assert read_amounts("model XT660") == []
    assert read_amounts("18.40 USD") == [pytest.approx(18.40)]
    assert read_amounts("15.60元") == [pytest.approx(15.60)]
    masked = parse_quote("Masked price 3X USD/PC", deal_currency="USD")
    assert masked["parsed"]["unit_price"] is None
    coded = parse_quote("Model XT660, unit price 18.40 USD per piece", deal_currency="USD")
    assert coded["status"] == "accepted"
    assert coded["parsed"]["unit_price"] == pytest.approx(18.40)
    assert "660" not in coded["provenance"]["tokens"]
    assert "3" not in masked["provenance"]["tokens"]


def test_bare_extractor_list_fails_closed_with_a_reason():
    from src.desk.extract import _from_chat, extract_mentions, mentions_from_payload
    from src.desk.shadow import shadow_from_message

    listed = mentions_from_payload([{"class": "own_unit_offer", "evidence_span": "18.40"}])
    assert listed["mentions"] == []
    assert listed["error"] == "extractor_not_object"
    content = json.dumps([{"class": "own_unit_offer"}])
    chat = _from_chat({"choices": [{"message": {"content": content}}]})
    assert chat["mentions"] == []
    assert chat["error"] == "extractor_not_object"
    assert chat["response_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    direct = extract_mentions("18.40", transport=lambda _body: [{"class": "own_unit_offer"}])
    assert direct["mentions"] == []
    assert direct["error"] == "extractor_not_object"
    shadow = shadow_from_message(
        "unit price 18.40 USD",
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        transport=lambda _body: [],
    )
    assert shadow["would_accept"] is False
    assert shadow["error"] == "extractor_not_object"


def test_extractor_class_and_role_do_not_accept_fees_totals_or_the_buyer():
    from src.desk.shadow import evaluate_shadow

    base = {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "USD",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": "18.40 USD per piece",
        "is_question": False,
    }

    def would(span: str, rule: float, **overrides: object) -> bool:
        mention = dict(base, evidence_span=span, **overrides)
        decision = evaluate_shadow(
            span,
            {"status": "accepted", "parsed": {"unit_price": rule}},
            low=1,
            high=10000,
            mentions=[mention],
            extractor_configured=True,
            currency="USD",
        )
        return bool(decision["would_accept"])

    assert would("18.40 USD per piece", 18.40) is True
    assert would("sample fee 40 USD", 40, **{"class": "fee"}) is False
    assert would("sample fee 40 USD", 40) is False
    assert would("shipping 12 USD", 12, **{"class": "fee"}) is False
    assert would("1840 USD", 1840, unit="other") is False
    assert would("total 1840 USD", 1840) is False
    assert would("duty 12 USD", 12) is False
    assert would("18.40 USD per piece", 18.40, speaker="buyer") is False


def test_default_model_is_nemotron_and_a_fallback_is_tried(monkeypatch):
    import urllib.error
    import urllib.request

    from src.config.heuristics import EXTRACTOR_BASE_URL, EXTRACTOR_FALLBACK_MODEL, EXTRACTOR_MODEL
    from src.desk.extract import endpoint, extract_mentions, model_name

    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TRADEFLOW_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("TRADEFLOW_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert EXTRACTOR_BASE_URL == "https://api.tokenfactory.nebius.com/v1"
    assert EXTRACTOR_MODEL == "nvidia/Nemotron-3-Ultra-550b-a55b"
    assert EXTRACTOR_FALLBACK_MODEL == "nvidia/nemotron-3-super-120b-a12b"
    assert endpoint() == EXTRACTOR_BASE_URL
    assert model_name() == EXTRACTOR_MODEL
    called = {"n": 0}

    def _blocked(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("no key, no call")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    quiet = extract_mentions("unit price 18.40 USD")
    assert quiet["configured"] is False
    assert called["n"] == 0

    seen = {}

    def _transport(body):
        seen["model"] = body["model"]
        seen["temperature"] = body["temperature"]
        return {"choices": [{"message": {"content": json.dumps({"mentions": []})}}]}

    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "test-key")
    preview = extract_mentions("unit price 18.40 USD", transport=_transport)
    assert seen["model"] == EXTRACTOR_MODEL
    assert seen["temperature"] == 0
    assert preview.get("error") is None

    monkeypatch.setenv("TRADEFLOW_LLM_BASE_URL", EXTRACTOR_BASE_URL)
    calls: list[str] = []

    def _urlopen(request, timeout=0):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body["model"])
        if body["model"] == EXTRACTOR_MODEL:
            raise urllib.error.URLError("primary down")

        class _Ok:
            def read(self):
                payload = {"choices": [{"message": {"content": json.dumps({"mentions": []})}}]}
                return json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return _Ok()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    fell_back = extract_mentions("unit price 18.40 USD")
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
    assert fell_back["model"] == EXTRACTOR_FALLBACK_MODEL
    assert fell_back.get("error") is None
    assert fell_back["response_sha256"]


def test_decision_record_stores_the_raw_response_hash(tmp_path, monkeypatch):
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore

    digest = hashlib.sha256(b'{"mentions": []}').hexdigest()

    def _fake(_message, *, transport=None, budget_seconds=None):
        del budget_seconds
        return {
            "mentions": [],
            "numeric_field_discarded": False,
            "configured": True,
            "rejected_rows": 1,
            "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
            "prompt_sha256": "p",
            "response_sha256": digest,
            "error": "extractor_not_object",
        }

    monkeypatch.setattr("src.desk.shadow.extract_mentions", _fake)
    store = DeskStore(str(tmp_path / "desk.db"))
    opened = create_deal(
        store,
        product="Bracket",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        currency="CNY",
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    provenance = ingested["event"]["provenance"]
    assert provenance["extractor_error"] == "extractor_not_object"
    assert provenance["response_sha256"] == digest
    assert ingested["event"]["status"] == "review"
    accepted = accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="18.40")
    card = next(row for row in accepted["suppliers"] if row["id"] == supplier)
    stored = store.get_decision(card["decision_id"])
    assert stored is not None
    assert stored["record"]["extractor_response_sha256"] == digest
    store.close()
