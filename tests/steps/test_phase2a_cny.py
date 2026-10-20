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
    assert bare["provenance"]["review_reason"] == "unit mismatch"
    assert "unknown" in bare["provenance"]["unit_basis"]
    assert price_from_token("报价28，含税，起订500个", "28") is None

    answered = parse_quote("多少钱？28，不含税")
    assert answered["provenance"]["currency_evidence"] == _EVIDENCE
    assert answered["parsed"]["unit_price"] is None
    assert price_from_token("多少钱？28，不含税", "28") is None

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
    unstated = parse_quote("单价28")
    assert unstated["status"] != "accepted"
    assert unstated["provenance"]["review_reason"] == "unit mismatch"
    accepted = parse_quote("每件28")
    assert accepted["status"] == "accepted"
    assert accepted["parsed"]["unit_price"] == pytest.approx(28)
    assert "per_piece" in accepted["provenance"]["unit_basis"]
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
    with pytest.raises(DeskRejected, match="unit mismatch"):
        accept_review(store, deal_id, implicit, supplier, chosen_token="28")
    accepted = accept_review(
        store, deal_id, implicit, supplier, chosen_token="28", unit_basis="per_piece"
    )
    kept = [row for row in accepted["events"] if row["id"] == implicit][0]
    assert kept["parsed"]["unit_price"] == pytest.approx(28)
    rows = store.conn.execute(
        "SELECT type, payload_json FROM desk_chain WHERE deal_id = ?",
        (deal_id,),
    ).fetchall()
    ingested = [json.loads(row["payload_json"]) for row in rows if row["type"] == "quote_ingested"]
    assert any(row.get("currency_evidence") == _EVIDENCE for row in ingested)
    assert any(row.get("review_reason") == "unit mismatch" for row in ingested)
    reviews = [json.loads(row["payload_json"]) for row in rows if row["type"] == "review"]
    confirmed = [row for row in reviews if row.get("to") == "accepted"]
    assert confirmed[-1]["stated_basis"] == "unknown"
    assert confirmed[-1]["confirmed_basis"] == "per_piece"
    assert confirmed[-1]["pieces_per_basis"] is None
    store.close()
    reset_stores()


def test_other_word_orders_stay_out_of_the_counter(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "phase2a-desk-key")
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.parse import parse_quote, price_from_token, token_issue
    from src.desk.service import DeskRejected, accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore, reset_stores

    orders = (
        ("按箱报价480元", "480", "箱"),
        ("按箱算480元", "480", "箱"),
        ("按箱计480元", "480", "箱"),
        ("一箱 480 元", "480", "箱"),
        ("按打报价48元", "48", "打"),
        ("一打 48 元", "48", "打"),
        ("48元/打", "48", "打"),
        ("每打 36 元", "36", "打"),
        ("36元一打", "36", "打"),
        ("per dozen 36 CNY", "36", "打"),
        ("一吨 8000 元", "8000", "吨"),
        ("8000元/吨", "8000", "吨"),
        ("按吨报价8000元", "8000", "吨"),
        ("3元/克", "3", "克"),
        ("每克 3 元", "3", "克"),
        ("12元/码", "12", "码"),
        ("一码 12 元", "12", "码"),
        ("5元/张", "5", "张"),
        ("一张 5 元", "5", "张"),
        ("2元/片", "2", "片"),
        ("一片 2 元", "2", "片"),
        ("1.5元/点", "1.5", "点"),
        ("一点 1.5 元", "1.5", "点"),
        ("按点计价1.5元", "1.5", "点"),
        ("按箱报价，480元", "480", "箱"),
    )
    for text, token, basis in orders:
        got = parse_quote(text)
        assert got["status"] != "accepted", text
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["review_reason"] == "unit mismatch", text
        assert basis in got["provenance"]["unit_basis"], text
        assert token in got["provenance"]["candidates"], text
        assert token_issue(text, token) == "unit mismatch", text
        assert price_from_token(text, token) is None, text

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="valve",
        quantity=500,
        target_price=10,
        ceiling_price=40,
        risk_tolerance=0.5,
        seed=6,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(
        store, deal_id, source="paste", raw_text="按打报价48元", supplier_id=supplier
    )
    event_id = opened["event"]["id"]
    held = store.get_snapshot(deal_id)
    card = next(row for row in held["suppliers"] if row["id"] == supplier)
    assert card["reference_ask"] is None
    assert card["counter"] is None
    with pytest.raises(DeskRejected, match="unit mismatch"):
        accept_review(store, deal_id, event_id, supplier, chosen_token="48")
    still = store.get_snapshot(deal_id)
    card = next(row for row in still["suppliers"] if row["id"] == supplier)
    assert card["reference_ask"] is None
    assert card["counter"] is None
    client = TestClient(app)
    refused = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        headers={"X-API-Key": "phase2a-desk-key"},
        json={"supplier_id": supplier, "action": "accept", "chosen_token": "48"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == "unit mismatch"
    chosen = accept_review(
        store, deal_id, event_id, supplier, chosen_token="48", unit_basis="per_piece"
    )
    card = next(row for row in chosen["suppliers"] if row["id"] == supplier)
    assert card["reference_ask"] == pytest.approx(48)
    assert card["counter"] is not None
    rows = store.conn.execute(
        "SELECT type, payload_json FROM desk_chain WHERE deal_id = ?",
        (deal_id,),
    ).fetchall()
    review = [
        json.loads(row["payload_json"])
        for row in rows
        if row["type"] == "review" and json.loads(row["payload_json"]).get("to") == "accepted"
    ]
    assert review[-1]["stated_basis"] == "打"
    assert review[-1]["confirmed_basis"] == "per_piece"
    assert review[-1]["pieces_per_basis"] is None

    converted_open = ingest_message(
        store, deal_id, source="paste", raw_text="一箱 480 元", supplier_id=supplier
    )
    blocked = store.get_snapshot(deal_id)
    blocked_card = next(row for row in blocked["suppliers"] if row["id"] == supplier)
    assert blocked_card["latest_quote"] == pytest.approx(48)
    assert blocked_card["reference_ask"] == pytest.approx(48)
    converted = accept_review(
        store,
        deal_id,
        converted_open["event"]["id"],
        supplier,
        chosen_token="480",
        unit_basis="箱",
        pieces_per_basis=20,
    )
    after = next(row for row in converted["suppliers"] if row["id"] == supplier)
    assert after["latest_quote"] == pytest.approx(24)
    assert after["counter"] is not None
    rows = store.conn.execute(
        "SELECT type, payload_json FROM desk_chain WHERE deal_id = ?",
        (deal_id,),
    ).fetchall()
    review = [
        json.loads(row["payload_json"])
        for row in rows
        if row["type"] == "review" and json.loads(row["payload_json"]).get("to") == "accepted"
    ]
    assert review[-1]["stated_basis"] == "箱"
    assert review[-1]["pieces_per_basis"] == pytest.approx(20)
    assert review[-1]["value"] == pytest.approx(24)
    store.close()
    reset_stores()


def test_a_prefill_binds_only_to_the_number_attached_to_a_price_cue():
    from src.desk.parse import parse_quote, price_from_token

    junk = "报价看货，1688平台，起订500个，交期15天，电话13800138000，超过1000，含税3%"
    empty = parse_quote(junk)
    assert empty["provenance"]["candidates"] == []
    assert empty["provenance"]["suggested_token"] is None
    assert empty["parsed"]["unit_price"] is None
    assert empty["provenance"]["review_reason"] == "no currency-anchored price"
    for token in ("1688", "500", "15", "13800138000", "1000", "3"):
        assert price_from_token(junk, token) is None

    loose = (
        "报价看货，1688平台，起订500个，交期15天，电话13800138000，"
        "超过1000，含税3%，2024年5月，货号8891，单价12元/件"
    )
    held = parse_quote(loose)
    assert held["status"] == "accepted"
    assert held["parsed"]["unit_price"] == pytest.approx(12)
    assert held["provenance"]["candidates"] == ["12"]
    assert held["provenance"]["suggested_token"] == "12"
    for token in ("1688", "500", "15", "13800138000", "1000", "3", "2024", "5", "8891"):
        assert token not in held["provenance"]["candidates"]
        assert price_from_token(loose, token) is None

    detached = parse_quote("报价看货28")
    assert detached["provenance"]["candidates"] == []
    assert detached["provenance"]["suggested_token"] is None
    assert price_from_token("报价看货28", "28") is None
    assert parse_quote("报价，28")["provenance"]["suggested_token"] is None
    labelled = parse_quote("报价:28")
    assert labelled["provenance"]["suggested_token"] == "28"
    assert labelled["provenance"]["review_reason"] == "unit mismatch"
    assert price_from_token("报价:28", "28") is None
    ahead = parse_quote("per piece 31")
    assert ahead["provenance"]["suggested_token"] == "31"
    assert "per_piece" in ahead["provenance"]["unit_basis"]
    assert price_from_token("per piece 31", "31") == pytest.approx(31)
    one_unit = parse_quote("28一件")
    assert one_unit["provenance"]["suggested_token"] == "28"
    assert "per_piece" in one_unit["provenance"]["unit_basis"]
    each = parse_quote("16 each")
    assert each["provenance"]["suggested_token"] == "16"
    assert "per_piece" in each["provenance"]["unit_basis"]

    counts = (
        "报价500万",
        "报价3千",
        "起订500个",
        "超过1000",
        "不低于500",
        "含税3%",
        "涨了3个点",
        "2024年5月12日",
        "电话13800138000",
        "货号8891",
        "型号:660",
        "1688平台",
        "sku 8891",
    )
    for text in counts:
        got = parse_quote(text)
        assert got["provenance"]["candidates"] == [], text
        assert got["provenance"]["suggested_token"] is None, text
        assert got["parsed"]["unit_price"] is None, text


def test_sub_yuan_units_scale_to_yuan():
    from src.config.heuristics import FEN_IN_YUAN, JIAO_IN_YUAN, LI_IN_YUAN
    from src.desk.parse import parse_quote, price_from_token

    assert FEN_IN_YUAN == pytest.approx(0.01)
    assert JIAO_IN_YUAN == pytest.approx(0.1)
    assert LI_IN_YUAN == pytest.approx(0.001)

    fen = parse_quote("单价3分")
    assert fen["status"] != "accepted"
    assert fen["parsed"]["unit_price"] is None
    assert fen["parsed"]["quoted_amount"] == pytest.approx(3 * FEN_IN_YUAN)
    assert fen["provenance"]["suggested_token"] == "3"
    assert fen["provenance"]["confirm_value"] == pytest.approx(3 * FEN_IN_YUAN)
    assert fen["provenance"]["confirm_value"] != pytest.approx(3)
    assert price_from_token("单价3分", "3") is None

    mao = parse_quote("每件5毛")
    assert mao["status"] == "accepted"
    assert mao["parsed"]["unit_price"] == pytest.approx(5 * JIAO_IN_YUAN)
    assert price_from_token("每件5毛", "5") == pytest.approx(5 * JIAO_IN_YUAN)

    jiao = parse_quote("2角一件")
    assert jiao["status"] == "accepted"
    assert jiao["parsed"]["unit_price"] == pytest.approx(2 * JIAO_IN_YUAN)
    assert "per_piece" in jiao["provenance"]["unit_basis"]
    assert price_from_token("2角一件", "2") == pytest.approx(2 * JIAO_IN_YUAN)

    li = parse_quote("8厘")
    assert li["parsed"]["unit_price"] is None
    assert li["parsed"]["quoted_amount"] == pytest.approx(8 * LI_IN_YUAN)
    assert li["provenance"]["confirm_value"] == pytest.approx(8 * LI_IN_YUAN)
    traditional = parse_quote("每件8釐")
    assert traditional["status"] == "accepted"
    assert traditional["parsed"]["unit_price"] == pytest.approx(8 * LI_IN_YUAN)
    piece_fen = parse_quote("每件3分")
    assert piece_fen["parsed"]["unit_price"] == pytest.approx(3 * FEN_IN_YUAN)
    assert price_from_token("每件3分", "3") == pytest.approx(3 * FEN_IN_YUAN)


@pytest.mark.parametrize(
    "text",
    [
        "之前买过12元/件",
        "以前的单价是12元/件",
        "上次12元/件",
        "当时12元/件",
        "听说12元/件",
        "据说12元",
        "网上看到12元/件",
        "如果12元/件",
        "假如单价12元/件",
        "估计12元/件",
        "预计12元",
        "应该12元/件",
        "能不能12元/件",
        "可以吗12元",
        "希望12元/件",
        "给个12元",
        "最低多少12元",
    ],
)
def test_past_hearsay_estimates_and_asks_are_not_confirmable(text):
    from src.desk.parse import parse_quote, price_from_token, token_issue

    got = parse_quote(text)
    assert got["status"] != "accepted"
    assert got["parsed"]["unit_price"] is None
    assert got["provenance"]["candidates"] == []
    assert got["provenance"]["suggested_token"] is None
    assert got["provenance"]["review_reason"] == "reference, not this supplier's price"
    token = got["provenance"]["labelled_references"][0]["token"]
    assert token_issue(text, token) == "reference, not this supplier's price"
    assert price_from_token(text, token) is None


def test_a_real_offer_beside_a_hypothetical_clause_stays_confirmable():
    from src.desk.parse import parse_quote, price_from_token

    got = parse_quote("应该没问题，单价12元/件")
    assert got["status"] == "accepted"
    assert got["parsed"]["unit_price"] == pytest.approx(12)
    assert price_from_token("应该没问题，单价12元/件", "12") == pytest.approx(12)
    offer = parse_quote("可以做，单价28元/件")
    assert offer["status"] == "accepted"
    assert offer["parsed"]["unit_price"] == pytest.approx(28)


def test_traditional_and_taiwan_amounts_are_unknown_currency():
    from src.desk.parse import parse_quote, price_from_token

    for text in ("報價 28 元", "單價 28 元", "NT$ 40", "NT$40", "新台幣 40", "台幣40", "新台币40"):
        got = parse_quote(text)
        assert got["status"] != "accepted", text
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["candidates"] == [], text
        assert got["provenance"]["suggested_token"] is None, text
        assert got["provenance"]["review_reason"] == "unknown currency", text
        assert got["provenance"]["currency_evidence"] is None, text
    mixed = parse_quote("单价 12元/件（约 NT$50）")
    assert mixed["provenance"]["candidates"] == []
    assert mixed["provenance"]["suggested_token"] is None
    assert mixed["parsed"]["unit_price"] is None
    assert price_from_token("单价 12元/件（约 NT$50）", "12") is None
    assert price_from_token("单价 12元/件（约 NT$50）", "50") is None
    simplified = parse_quote("报价 28 元/件")
    assert simplified["status"] == "accepted"
    assert simplified["parsed"]["unit_price"] == pytest.approx(28)


def test_basis_and_surcharges_are_read_per_number():
    from src.desk.parse import parse_quote, price_from_token

    pieces = parse_quote("1000个 31元/个")
    assert pieces["status"] == "accepted"
    assert pieces["parsed"]["unit_price"] == pytest.approx(31)
    assert pieces["provenance"]["candidates"] == ["31"]
    assert "1000" not in pieces["provenance"]["candidates"]
    carton = parse_quote("按箱报价，480元")
    assert carton["provenance"]["review_reason"] == "unit mismatch"
    assert "箱" in carton["provenance"]["unit_basis"]
    assert carton["provenance"]["suggested_token"] == "480"
    assert price_from_token("按箱报价，480元", "480") is None
    quantity = parse_quote("报价1000个")
    assert quantity["provenance"]["candidates"] == []
    assert price_from_token("报价1000个", "1000") is None
    neighbour = parse_quote("500个。单价31元/件")
    assert neighbour["status"] == "accepted"
    assert neighbour["parsed"]["unit_price"] == pytest.approx(31)

    for text in ("加收2元", "加收了2元/件", "加价到15元/件", "差价5元", "差价是5元/个", "另加8元"):
        got = parse_quote(text)
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["candidates"] == [], text
        assert got["provenance"]["suggested_token"] is None, text
    beside = parse_quote("单价12元/件，加收了2元")
    assert beside["status"] == "accepted"
    assert beside["parsed"]["unit_price"] == pytest.approx(12)
    assert beside["provenance"]["candidates"] == ["12"]
    assert price_from_token("单价12元/件，加收了2元", "2") is None
    assert price_from_token("单价12元/件，加收了2元", "12") == pytest.approx(12)
    raised = parse_quote("单价12元/件，加价到15元")
    assert raised["parsed"]["unit_price"] == pytest.approx(12)
    assert "15" not in raised["provenance"]["candidates"]


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
    assert implicit["would_accept"] is False
    piece = evaluate_shadow(
        "单价15元/件，单价16元/件",
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("单价15元/件")],
        extractor_configured=True,
    )
    assert piece["would_accept"] is False
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


def _chat(span: str) -> dict:
    body = {
        "mentions": [
            {
                "class": "own_unit_offer",
                "evidence_span": span,
                "speaker": "supplier",
                "polarity": "affirmative",
                "currency": "CNY",
                "unit": "per_piece",
                "is_question": False,
            }
        ]
    }
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}


def test_shadow_agrees_only_when_the_rule_reads_the_same_span():
    from src.desk.shadow import evaluate_shadow, shadow_from_message

    message = "单价12元/件"
    regex_only = evaluate_shadow(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("12")],
        extractor_configured=True,
    )
    assert regex_only["would_accept"] is False
    other_basis = evaluate_shadow(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("单价12元/件", unit="other")],
        extractor_configured=True,
    )
    assert other_basis["would_accept"] is False
    other_currency = evaluate_shadow(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("单价12元/件", currency="USD")],
        extractor_configured=True,
    )
    assert other_currency["would_accept"] is False
    agreed = evaluate_shadow(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        mentions=[_mention("单价12元/件")],
        extractor_configured=True,
    )
    assert agreed["would_accept"] is True
    assert agreed["applied"] is False
    assert agreed["span_value"] == pytest.approx(12)

    stable = shadow_from_message(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        transport=lambda _body: _chat("单价12元/件"),
    )
    assert stable["would_accept"] is True
    assert stable.get("unstable") is not True
    assert stable["applied"] is False

    answers = ["单价12元/件", "单价13元/件"]

    def _disagree(_body):
        return _chat(answers.pop(0))

    unstable = shadow_from_message(
        message,
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        transport=_disagree,
    )
    assert unstable["would_accept"] is False
    assert unstable["unstable"] is True
    listed = shadow_from_message(
        "单价15元/件，单价16元/件",
        {"status": "review", "parsed": {}},
        low=1,
        high=100,
        transport=lambda _body: _chat("单价15元/件"),
    )
    assert listed["would_accept"] is False
    assert listed.get("unstable") is not True


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


def test_nvidia_404_with_a_function_id_disables_the_hop(monkeypatch):
    import src.desk.extract as extract
    from fastapi.testclient import TestClient
    from src.app.main import app

    extract.reset_nvidia_hop()
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    body = b'{"error":"Function \'nv-fn-9f3a\': Not found for account acct-1"}'

    def _transport(request):
        if request["model"] == EXTRACTOR_NVIDIA_MODEL:
            raise _http(404, body)
        raise TimeoutError("slow")

    try:
        failed = extract.extract_mentions("单价 18 元", transport=_transport)
        assert failed["error"] == "extractor_nvidia_unconfigured"
        assert failed["attempts"][-1]["status"] == "config"
        assert extract.nvidia_hop_status() == "nvidia hop disabled: function not found for account"
        health = TestClient(app).get("/health").json()
        assert health["status"] == "ok"
        assert "ready" not in health["nvidia"]
    finally:
        extract.reset_nvidia_hop()


def test_a_404_without_that_phrase_does_not_disable_the_hop(monkeypatch):
    import src.desk.extract as extract

    extract.reset_nvidia_hop()
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")

    def _transport(request):
        if request["model"] == EXTRACTOR_NVIDIA_MODEL:
            raise _http(404, b'{"error":"model missing"}')
        raise TimeoutError("slow")

    try:
        result = extract.extract_mentions("单价 18 元", transport=_transport)
        assert result["attempts"][-1]["status"] == "http_404"
        assert extract.nvidia_hop_status() == "nvidia hop ready"
        health_phrase = extract.nvidia_hop_status()
        assert "ready" in health_phrase
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
    occupied = threading.Event()
    running = {"n": 0}
    guard = threading.Lock()

    def _block() -> None:
        with guard:
            running["n"] += 1
            if running["n"] >= int(pool._max_workers):
                occupied.set()
        held.wait()

    for _index in range(int(pool._max_workers)):
        pool.submit(_block)
    assert occupied.wait(5)
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
        queued_deadline = time.monotonic() + 5
        while import_status()["queued"] < 1:
            assert time.monotonic() < queued_deadline
        hold_until = time.monotonic() + 0.2
        while time.monotonic() < hold_until:
            assert import_status()["queued"] >= 1
        assert import_status()["concurrency"] == pool._max_workers
        held.set()
        assert started.wait(5)
        assert seen["budget"] > 2.75
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
