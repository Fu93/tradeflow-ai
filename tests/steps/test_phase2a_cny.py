"""Phase 2a Chinese quote reading. The sentences are original B2B wording."""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from src.config.heuristics import (
    EXTRACTOR_FALLBACK_MODEL,
    EXTRACTOR_MODEL,
    EXTRACTOR_NVIDIA_MODEL,
    EXTRACTOR_SUPER_DIRECT_CHARS,
)

_EVIDENCE = "implicit currency from deal + language"
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def _http(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://example.test/v1/chat/completions",
        code,
        "no",
        Message(),
        io.BytesIO(body),
    )


def test_measure_words_are_a_unit_basis_not_a_currency_miss():
    from src.desk.parse import parse_quote, price_from_token, token_issue

    carton = parse_quote("每箱 480 元，一箱 20 件。")
    assert carton["status"] == "review"
    assert carton["parsed"]["unit_price"] is None
    assert carton["provenance"]["review_reason"] == "unit mismatch"
    assert carton["provenance"]["review_reason"] != "not the deal currency"
    assert "480" in carton["provenance"]["candidates"]
    assert carton["provenance"]["suggested_token"] == "480"
    assert "箱" in carton["provenance"]["unit_basis"]
    assert carton["provenance"]["normalised_unit_price"] is None
    assert token_issue("每箱 480 元，一箱 20 件。", "480") == "unit mismatch"
    assert price_from_token("每箱 480 元，一箱 20 件。", "480") is None
    assert "20" not in carton["provenance"]["candidates"]

    for text, basis in (
        ("8.5元/公斤", "公斤"),
        ("每斤 6 元", "斤"),
        ("12元/条", "条"),
        ("一双 15 元", "双"),
        ("每套 88 元", "套"),
        ("一台 260 元", "台"),
        ("3.2元/米", "米"),
        ("每包 6.5 元", "包"),
    ):
        got = parse_quote(text)
        assert got["parsed"]["unit_price"] is None
        assert got["provenance"]["review_reason"] == "unit mismatch"
        assert basis in got["provenance"]["unit_basis"]
        assert got["provenance"]["candidates"]

    piece = parse_quote("12元/个")
    assert piece["status"] == "accepted"
    assert piece["parsed"]["unit_price"] == pytest.approx(12)
    assert "per_piece" in piece["provenance"]["unit_basis"]

    usd = parse_quote("USD 9.20 per carton", deal_currency="USD")
    assert usd["provenance"]["review_reason"] == "unit mismatch"
    assert usd["parsed"]["unit_price"] is None
    foreign = parse_quote("USD 9.20 per piece")
    assert foreign["parsed"]["unit_price"] is None
    assert foreign["provenance"]["review_reason"] == "not the deal currency"


def test_a_stated_pack_count_converts_only_when_the_policy_is_on(monkeypatch):
    import src.config.heuristics as heuristics
    from src.desk.parse import parse_quote

    text = "每箱 480 元，一箱 20 件。"
    assert parse_quote(text)["provenance"]["normalised_unit_price"] is None
    monkeypatch.setattr(heuristics, "UNIT_BASIS_FROM_MESSAGE", 1)
    converted = parse_quote(text)
    assert converted["status"] == "review"
    assert converted["parsed"]["unit_price"] is None
    assert converted["provenance"]["normalised_unit_price"] == pytest.approx(24)
    assert converted["provenance"]["review_reason"] == "unit mismatch"


def test_implicit_rmb_is_a_prefilled_candidate_and_not_an_accept():
    from src.desk.parse import parse_quote, price_from_token

    bare = parse_quote("报价28，含税，起订500个")
    assert bare["status"] == "review"
    assert bare["parsed"]["unit_price"] is None
    assert bare["provenance"]["currency_evidence"] == _EVIDENCE
    assert bare["provenance"]["suggested_token"] == "28"
    assert "28" in bare["provenance"]["candidates"]
    assert "500" not in bare["provenance"]["candidates"]
    assert price_from_token("报价28，含税，起订500个", "28") == pytest.approx(28)

    answered = parse_quote("多少钱？28，不含税")
    assert answered["provenance"]["currency_evidence"] == _EVIDENCE
    assert answered["parsed"]["unit_price"] is None
    assert price_from_token("多少钱？28，不含税", "28") == pytest.approx(28)

    english = parse_quote("unit price 33", deal_currency="CNY")
    assert english["provenance"]["currency_evidence"] is None
    assert english["status"] != "accepted"
    usd = parse_quote("报价28", deal_currency="USD")
    assert usd["provenance"]["currency_evidence"] is None
    explicit = parse_quote("单价 18.40 元/件")
    assert explicit["status"] == "accepted"
    assert explicit["provenance"]["currency_evidence"] is None


def test_implicit_currency_accepts_only_when_the_labelled_policy_is_on(monkeypatch):
    import src.config.heuristics as heuristics
    from src.desk.parse import parse_quote

    assert parse_quote("单价28")["status"] != "accepted"
    monkeypatch.setattr(heuristics, "IMPLICIT_CNY_AUTO_ACCEPT", 1)
    accepted = parse_quote("单价28")
    assert accepted["status"] == "accepted"
    assert accepted["parsed"]["unit_price"] == pytest.approx(28)
    hedged = parse_quote("大概28元")
    assert hedged["status"] != "accepted"
    assert hedged["parsed"]["unit_price"] is None


@pytest.mark.parametrize(
    "text,label",
    [
        ("服务费 80元/小时", "元/小时"),
        ("现场 200 元/天", "元/天"),
        ("上门 50 元/次", "元/次"),
        ("运费 35 元", "运费"),
        ("打样费 120 元", "打样费"),
        ("开模费 3000 元", "开模费"),
        ("服务费 15 元", "服务费"),
        ("手续费 8 元", "手续费"),
    ],
)
def test_time_and_service_rates_are_fees_not_unit_prices(text, label):
    from src.desk.parse import parse_quote, price_from_token, token_issue

    got = parse_quote(text)
    assert got["parsed"]["unit_price"] is None
    assert got["provenance"]["candidates"] == []
    assert got["provenance"]["review_reason"] == "fee, not a unit price"
    assert got["provenance"]["fee_lines"]
    assert got["provenance"]["fee_lines"][0]["label"] == label
    assert token_issue(text, got["provenance"]["fee_lines"][0]["token"]) == "fee, not a unit price"
    assert price_from_token(text, got["provenance"]["fee_lines"][0]["token"]) is None


def test_a_goods_price_beside_a_fee_is_not_auto_accepted():
    from src.desk.parse import parse_quote, price_from_token

    got = parse_quote("单价12元/件，运费80元")
    assert got["status"] == "review"
    assert got["parsed"]["unit_price"] is None
    assert got["parsed"]["fees"] is None
    assert "12" in got["provenance"]["candidates"]
    assert "80" not in got["provenance"]["candidates"]
    assert got["provenance"]["fee_lines"][0]["label"] == "运费"
    assert price_from_token("单价12元/件，运费80元", "80") is None
    assert price_from_token("单价12元/件，运费80元", "12") == pytest.approx(12)


@pytest.mark.parametrize(
    "text",
    [
        "大概 33 元",
        "单价 33 元左右",
        "不到 30 元",
        "低于市场价 25 元",
        "高于 40 元我们不做",
        "最多 32 元",
        "至少 20 元",
        "比别家便宜 5 元",
        "市场价 26 元",
        "别家报了 22 元",
        "我方目标价 20 元",
        "你报的 19 元我们看到了",
    ],
)
def test_hedges_and_other_speakers_are_references_not_a_supplier_price(text):
    from src.desk.parse import parse_quote, price_from_token, token_issue

    got = parse_quote(text)
    assert got["status"] != "accepted"
    assert got["parsed"]["unit_price"] is None
    assert got["provenance"]["candidates"] == []
    assert got["provenance"]["suggested_token"] is None
    assert got["provenance"]["labelled_references"]
    assert got["provenance"]["review_reason"] == "reference, not this supplier's price"
    token = got["provenance"]["labelled_references"][0]["token"]
    assert token_issue(text, token) == "reference, not this supplier's price"
    assert price_from_token(text, token) is None


def test_a_supplier_offer_beside_a_rival_line_stays_the_offer():
    from src.desk.parse import parse_quote

    got = parse_quote("别家 22 元。单价 28 元/件")
    assert got["status"] == "accepted"
    assert got["parsed"]["unit_price"] == pytest.approx(28)
    assert "22" not in got["provenance"]["candidates"]
    assert got["provenance"]["labelled_references"][0]["token"] == "22"


def test_confirm_uses_the_accurate_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    from src.desk.service import DeskRejected, accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="valve",
        quantity=500,
        target_price=10,
        ceiling_price=40,
        risk_tolerance=0.5,
        seed=3,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]

    def opened(text: str) -> str:
        result = ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier)
        return str(result["event"]["id"])

    carton = opened("每箱 480 元，一箱 20 件。")
    with pytest.raises(DeskRejected, match="unit mismatch"):
        accept_review(store, deal_id, carton, supplier, chosen_token="480")
    fee = opened("运费 35 元")
    with pytest.raises(DeskRejected, match="fee, not a unit price"):
        accept_review(store, deal_id, fee, supplier, chosen_token="35")
    hedge = opened("大概 33 元")
    with pytest.raises(DeskRejected, match="reference, not this supplier's price"):
        accept_review(store, deal_id, hedge, supplier, chosen_token="33")
    implicit = opened("报价28，含税")
    event = store.event(deal_id, implicit)
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["currency_evidence"] == _EVIDENCE
    assert event["provenance"]["auto_accept"] is False
    accepted = accept_review(store, deal_id, implicit, supplier, chosen_token="28")
    kept = [row for row in accepted["events"] if row["id"] == implicit][0]
    assert kept["parsed"]["unit_price"] == pytest.approx(28)
    rows = store.conn.execute(
        "SELECT type, payload_json FROM desk_chain WHERE deal_id = ?",
        (deal_id,),
    ).fetchall()
    ingested = [json.loads(row["payload_json"]) for row in rows if row["type"] == "quote_ingested"]
    assert any(row.get("currency_evidence") == _EVIDENCE for row in ingested)
    assert any(row.get("review_reason") == "unit mismatch" for row in ingested)
    store.close()
    reset_stores()


def _mention(span: str, **overrides: object) -> dict:
    row = {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "CNY",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": span,
        "is_question": False,
    }
    row.update(overrides)
    return row


def test_shadow_validates_the_span_without_a_rule_value():
    from src.desk.shadow import evaluate_shadow

    message = "起订500个，单价28元/件"
    decision = evaluate_shadow(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("起订500个，单价28元/件")],
        extractor_configured=True,
    )
    assert decision["would_accept"] is True
    assert decision["applied"] is False
    assert decision["auto_accept"] is False
    assert decision["rule_value"] is None
    assert decision["span_value"] == pytest.approx(28)

    implicit = evaluate_shadow(
        "报价28，含税",
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("28")],
        extractor_configured=True,
    )
    assert implicit["would_accept"] is True
    assert implicit["span_value"] == pytest.approx(28)
    english = evaluate_shadow(
        "quote 28",
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("28")],
        extractor_configured=True,
    )
    assert english["would_accept"] is False


def test_shadow_rejects_a_bad_span_role():
    from src.desk.shadow import evaluate_shadow

    def would(message: str, span: str, **overrides: object) -> bool:
        decision = evaluate_shadow(
            message,
            {"status": "review", "parsed": {}},
            low=1,
            high=10000,
            mentions=[_mention(span, **overrides)],
            extractor_configured=True,
        )
        return bool(decision["would_accept"])

    assert would("480元/箱", "480元/箱") is False
    assert would("运费80元", "运费80元") is False
    assert would("80元/小时", "80元/小时") is False
    assert would("大概28元", "大概28元") is False
    assert would("单价15元/件，单价16元/件", "单价15元/件，单价16元/件") is False
    assert would("起订500个", "起订500个") is False
    assert would("单价28元/件", "单价28元/件", speaker="buyer") is False
    assert would("报价 15.60元/件", "99元/件") is False
    quiet = evaluate_shadow("单价28元/件", {"status": "review", "parsed": {}}, low=1, high=100)
    assert quiet["would_accept"] is False


def test_nvidia_function_not_found_disables_the_hop(monkeypatch, caplog):
    import src.desk.extract as extract

    extract.reset_nvidia_hop()
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        if body["model"] == EXTRACTOR_NVIDIA_MODEL:
            raise _http(404, b'{"error":"Function not found for account"}')
        raise TimeoutError("slow")

    try:
        with caplog.at_level("WARNING"):
            failed = extract.extract_mentions("单价 18 元", transport=_transport)
        assert EXTRACTOR_NVIDIA_MODEL in calls
        assert failed["error"] == "extractor_nvidia_unconfigured"
        assert extract.nvidia_hop_status() == "nvidia hop disabled: function not found for account"
        assert "function not found for account" in caplog.text
        calls.clear()
        again = extract.extract_mentions("单价 18 元", transport=_transport)
        assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
        assert again["attempts"][-1]["status"] == "config"
        assert again["attempts"][-1]["provider"] == "nvidia"
        extract._disable_nvidia()
        assert extract.nvidia_hop_status().startswith("nvidia hop disabled")
    finally:
        extract.reset_nvidia_hop()


def test_a_plain_nvidia_client_error_does_not_disable_the_hop(monkeypatch):
    import src.desk.extract as extract

    extract.reset_nvidia_hop()
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")

    def _transport(body):
        if body["model"] == EXTRACTOR_NVIDIA_MODEL:
            raise _http(400, b"Function not found for account")
        raise TimeoutError("slow")

    try:
        result = extract.extract_mentions("单价 18 元", transport=_transport)
        assert result["attempts"][-1]["status"] == "http_400"
        assert extract.nvidia_hop_status() == "nvidia hop ready"
    finally:
        extract.reset_nvidia_hop()


def test_a_long_message_skips_ultra_and_calls_super(monkeypatch):
    from src.desk.extract import extract_mentions

    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        return {"choices": [{"finish_reason": "stop", "message": {"content": "{\"mentions\":[]}"}}]}

    long_message = "报价" + ("甲" * int(EXTRACTOR_SUPER_DIRECT_CHARS))
    result = extract_mentions(long_message, transport=_transport)
    assert calls == [EXTRACTOR_FALLBACK_MODEL]
    assert result["attempts"][0]["status"] == "skipped_length"
    assert result["attempts"][0]["model"] == EXTRACTOR_MODEL
    calls.clear()
    short = extract_mentions("单价 18 元/件", transport=_transport)
    assert calls[0] == EXTRACTOR_MODEL
    assert short["attempts"][0]["status"] == "ok"


def test_burst_import_starts_the_budget_when_the_call_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("TRADEFLOW_EXTRACTOR_TIMEOUT_BUDGET", "3")
    from src.desk.service import (
        _extractor_pool,
        create_deal,
        import_status,
        ingest_message,
        reset_import_status,
    )
    from src.desk.store import DeskStore, reset_stores
    import src.desk.shadow as shadow

    reset_stores()
    reset_import_status()
    seen: dict[str, float] = {}
    started = threading.Event()

    def _record(*_args, **kwargs):
        seen["budget"] = float(kwargs["budget_seconds"])
        started.set()
        return {
            "would_accept": False,
            "applied": False,
            "auto_accept": False,
            "attempts": [],
            "mentions": [],
        }

    monkeypatch.setattr(shadow, "shadow_from_message", _record)
    pool = _extractor_pool()
    held = threading.Event()
    for _index in range(int(pool._max_workers)):
        pool.submit(held.wait)
    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="valve",
        quantity=100,
        target_price=10,
        ceiling_price=40,
        risk_tolerance=0.5,
        seed=4,
        suppliers=SUPPLIERS,
    )
    try:
        ingest_message(
            store,
            snapshot["deal"]["id"],
            source="paste",
            raw_text="报价28，含税",
            supplier_id=snapshot["suppliers"][0]["id"],
        )
        time.sleep(0.6)
        assert import_status()["queued"] >= 1
        assert import_status()["concurrency"] == pool._max_workers
        held.set()
        assert started.wait(3)
        assert seen["budget"] > 2.7
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and import_status()["finished"] < 1:
            time.sleep(0.05)
        assert import_status()["finished"] >= 1
    finally:
        held.set()
        store.close()
        reset_stores()
        reset_import_status()


def test_health_reports_the_nvidia_hop_and_the_import_queue():
    from fastapi.testclient import TestClient
    from src.app.main import app
    from src.desk.extract import nvidia_hop_status, reset_nvidia_hop

    reset_nvidia_hop()
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["nvidia"] == "nvidia hop ready"
    assert body["nvidia"] == nvidia_hop_status()
    assert "queued" in body["extractor_import"]
    assert "concurrency" in body["extractor_import"]
    readme = Path("README.md").read_text(encoding="utf-8")
    for phrase in (
        "P2a unit basis",
        "P2a implicit RMB",
        "P2a fees",
        "P2a speaker",
        "P2a agreement",
        "P2a extractor ops",
        "tests/steps/test_phase2a_cny.py",
        "tail truncation is detectable only with an anchor",
    ):
        assert phrase in readme
