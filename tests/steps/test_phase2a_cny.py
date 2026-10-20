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
from src.desk.parse import UNIT_LEXICON

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
    assert answered["provenance"]["currency_evidence"] is None
    assert answered["parsed"]["unit_price"] is None
    assert answered["provenance"]["review_reason"] == "unrecognised term: 多少钱"
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
        store,
        deal_id,
        implicit,
        supplier,
        chosen_token="28",
        human_confirmed=True,
        unit_basis="per_piece",
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
    assert confirmed[-1]["human_confirmed"] is True
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
    override = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        headers={"X-API-Key": "phase2a-desk-key"},
        json={
            "supplier_id": supplier,
            "action": "accept",
            "chosen_token": "48",
            "unit_basis": "per_piece",
            "human_confirmed": True,
        },
    )
    assert override.status_code == 422
    assert override.json()["detail"] == "unit mismatch"
    silent_dozen = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        headers={"X-API-Key": "phase2a-desk-key"},
        json={
            "supplier_id": supplier,
            "action": "accept",
            "chosen_token": "48",
            "pieces_per_basis": 12,
        },
    )
    assert silent_dozen.status_code == 422
    chosen = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        headers={"X-API-Key": "phase2a-desk-key"},
        json={
            "supplier_id": supplier,
            "action": "accept",
            "chosen_token": "48",
            "pieces_per_basis": 12,
            "human_confirmed": True,
        },
    )
    assert chosen.status_code == 200, chosen.text
    card = next(row for row in chosen.json()["suppliers"] if row["id"] == supplier)
    assert card["reference_ask"] == pytest.approx(4)
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
    assert review[-1]["pieces_per_basis"] == pytest.approx(12)
    assert review[-1]["human_confirmed"] is True
    assert review[-1]["value"] == pytest.approx(4)

    converted_open = ingest_message(
        store, deal_id, source="paste", raw_text="每箱 480 元，一箱 20 件", supplier_id=supplier
    )
    blocked = store.get_snapshot(deal_id)
    blocked_card = next(row for row in blocked["suppliers"] if row["id"] == supplier)
    assert blocked_card["latest_quote"] == pytest.approx(4)
    assert blocked_card["reference_ask"] == pytest.approx(4)
    carton_id = converted_open["event"]["id"]
    for body in (
        {"unit_basis": "per_piece", "human_confirmed": True},
        {"pieces_per_basis": 1, "human_confirmed": True},
        {"pieces_per_basis": 20},
    ):
        blocked_post = client.post(
            f"/api/v1/desk/deals/{deal_id}/review/{carton_id}",
            headers={"X-API-Key": "phase2a-desk-key"},
            json={"supplier_id": supplier, "action": "accept", "chosen_token": "480", **body},
        )
        assert blocked_post.status_code == 422
        assert blocked_post.json()["detail"] == "unit mismatch"
    converted = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{carton_id}",
        headers={"X-API-Key": "phase2a-desk-key"},
        json={
            "supplier_id": supplier,
            "action": "accept",
            "chosen_token": "480",
            "pieces_per_basis": 20,
            "human_confirmed": True,
        },
    )
    assert converted.status_code == 200, converted.text
    after = next(row for row in converted.json()["suppliers"] if row["id"] == supplier)
    assert after["latest_quote"] == pytest.approx(24)
    assert after["reference_ask"] == pytest.approx(4)
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
    assert review[-1]["human_confirmed"] is True
    store.close()
    reset_stores()


def test_ui_payload_settles_unknown_basis_and_refuses_a_contradiction(tmp_path, monkeypatch):
    """The desk and extension post this JSON. A script field alone does not."""
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "phase2a-desk-key")
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.parse import conversion_pieces, parse_quote, price_from_token
    from src.desk.service import _chained_piece_amount, create_deal, ingest_message
    from src.desk.shadow import evaluate_shadow
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="valve",
        quantity=2000,
        target_price=8,
        ceiling_price=40,
        risk_tolerance=0.5,
        seed=6,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    client = TestClient(app)
    headers = {"X-API-Key": "phase2a-desk-key"}

    def post_quote(text: str) -> dict:
        opened = ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier)
        return opened["event"]

    def review(event_id: str, token: str, **extra: object):
        body = {"supplier_id": supplier, "action": "accept", "chosen_token": token}
        body.update(extra)
        return client.post(
            f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
            headers=headers,
            json=body,
        )

    for text, token in (
        ("单价 3.2 元", "3.2"),
        ("报价 12.5", "12.5"),
        ("含税单价 6.80 元", "6.80"),
    ):
        event = post_quote(text)
        assert event["status"] == "review"
        assert event["parsed"]["unit_price"] is None
        plain = review(event["id"], token)
        assert plain.status_code == 422
        assert plain.json()["detail"] == "unit mismatch"
        silent = review(event["id"], token, unit_basis="per_piece")
        assert silent.status_code == 422
        clicked = review(event["id"], token, human_confirmed=True, unit_basis="per_piece")
        assert clicked.status_code == 200, clicked.text
        reading = parse_quote(text)
        assert reading["provenance"]["basis_prompts"][token]["label"] == "per piece (deal unit)"
        assert reading["provenance"]["basis_prompts"][token]["kind"] == "per_piece"

    moq_text = "单价 15.60 元，起订量 1000 件"
    moq = post_quote(moq_text)
    assert moq["status"] == "review"
    assert moq["parsed"]["unit_price"] is None
    assert moq["provenance"]["auto_accept"] is False
    assert price_from_token(moq_text, "15.60") == pytest.approx(15.60)
    moq_ok = review(moq["id"], "15.60")
    assert moq_ok.status_code == 200, moq_ok.text

    dozen = parse_quote("36元/打")
    assert dozen["provenance"]["basis_prompts"]["36"]["pieces_per_basis"] == pytest.approx(12)
    assert dozen["provenance"]["basis_prompts"]["36"]["label"] == "12 pieces per 打"
    dozen_event = post_quote("36元/打")
    assert review(dozen_event["id"], "36", unit_basis="per_piece", human_confirmed=True).status_code == 422
    assert review(dozen_event["id"], "36", pieces_per_basis=1, human_confirmed=True).status_code == 422
    dozen_ok = review(dozen_event["id"], "36", pieces_per_basis=12, human_confirmed=True)
    assert dozen_ok.status_code == 200, dozen_ok.text
    dozen_card = next(row for row in dozen_ok.json()["suppliers"] if row["id"] == supplier)
    assert dozen_card["latest_quote"] == pytest.approx(3)
    chained = [
        json.loads(row["payload_json"])
        for row in store.conn.execute(
            "SELECT type, payload_json FROM desk_chain WHERE deal_id = ?",
            (deal_id,),
        ).fetchall()
        if row["type"] == "review" and json.loads(row["payload_json"]).get("token") == "36"
    ]
    assert _chained_piece_amount("36元/打", chained[-1], "CNY") == pytest.approx(3)
    contradicted = dict(chained[-1])
    contradicted["pieces_per_basis"] = 1
    assert _chained_piece_amount("36元/打", contradicted, "CNY") is None
    mixed = "每箱 480 元，一箱 20 件。另 36元/打"
    assert conversion_pieces(mixed, "箱") == pytest.approx(20)
    assert conversion_pieces(mixed, "打") == pytest.approx(12)
    half = parse_quote("480元/箱，一箱 20.5 件")
    assert half["provenance"]["basis_prompts"]["480"]["label"] == "20.5 pieces per 箱"
    stated = parse_quote("48元/打，一打 10 件")
    assert stated["provenance"]["basis_prompts"]["48"]["pieces_per_basis"] == pytest.approx(10)

    kilo = post_quote("按公斤算 18 元")
    assert "18" not in (parse_quote("按公斤算 18 元")["provenance"]["basis_prompts"])
    assert review(kilo["id"], "18", unit_basis="per_piece", human_confirmed=True).status_code == 422
    assert review(kilo["id"], "18", pieces_per_basis=1, human_confirmed=True).status_code == 422

    compound = parse_quote("1块2毛")
    assert compound["provenance"]["candidates"] == ["1"]
    assert compound["provenance"]["confirm_value"] == pytest.approx(1.2)
    assert compound["status"] != "accepted"
    assert parse_quote("1元2角")["provenance"]["confirm_value"] == pytest.approx(1.2)
    assert "2" not in parse_quote("1元2角")["provenance"]["candidates"]

    pair = parse_quote("含税单价 12.80 元/件，不含税 11.30")
    assert pair["status"] != "accepted"
    assert pair["parsed"]["unit_price"] is None
    assert pair["provenance"]["suggested_token"] is None
    assert pair["provenance"]["quoted_amount"] is None
    assert "12.80" in pair["provenance"]["candidates"]
    assert "11.30" in pair["provenance"]["candidates"]
    one_side = parse_quote("含税33元/件，不含税另计")
    assert one_side["parsed"]["unit_price"] is None
    assert one_side["provenance"]["suggested_token"] is None
    single = parse_quote("报价28，含税")
    assert single["provenance"]["candidates"] == ["28"]
    assert single["provenance"]["suggested_token"] == "28"

    ladder = "单价 15 元/件；1000件以上 14 元/件"
    high = parse_quote(ladder, quantity=2000)
    low = parse_quote(ladder, quantity=100)
    assert high["status"] != "accepted"
    assert high["parsed"]["unit_price"] is None
    assert high["provenance"]["suggested_token"] is None
    assert high["provenance"]["candidates"] == ["15", "14"]
    thresholds = {row["token"]: row["threshold"] for row in high["provenance"]["tiers"]}
    assert thresholds["15"] is None
    assert thresholds["14"] == pytest.approx(1000)
    assert high["provenance"]["highlighted_token"] == "14"
    assert low["provenance"]["highlighted_token"] == "15"
    assert low["provenance"]["suggested_token"] is None
    shadow = evaluate_shadow(
        ladder,
        high,
        low=1,
        high=100,
        mentions=[
            {
                "class": "own_unit_offer",
                "speaker": "supplier",
                "polarity": "affirmative",
                "is_question": False,
                "evidence_span": "单价 15 元/件",
                "currency": "CNY",
                "unit": "per_piece",
            }
        ],
        extractor_configured=True,
    )
    assert shadow["would_accept"] is False
    assert shadow["applied"] is False

    panel = Path("src/web/static/js/desk.js").read_text(encoding="utf-8")
    background = Path("extension/background.js").read_text(encoding="utf-8")
    hud = Path("extension/content.js").read_text(encoding="utf-8")
    assert "human_confirmed" in panel
    assert "per piece (deal unit)" in panel
    assert "human_confirmed" in background
    assert "per piece (deal unit)" in hud
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

    for text in ("報價 28 元", "單價 28 元"):
        got = parse_quote(text)
        assert got["status"] != "accepted", text
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["candidates"] == [], text
        assert got["provenance"]["suggested_token"] is None, text
        assert got["provenance"]["review_reason"] == "unknown currency", text
        assert got["provenance"]["currency_evidence"] is None, text
    for text in ("NT$ 40", "NT$40", "新台幣 40", "台幣40", "新台币40", "TWD 40", "₩40"):
        got = parse_quote(text)
        assert got["status"] != "accepted", text
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["currency_evidence"] is None, text
        assert "not the deal currency" in got["provenance"]["review_reasons"], text
        token = "40"
        assert price_from_token(text, token) is None, text
    mixed = parse_quote("单价 12元/件（约 NT$50）")
    assert mixed["provenance"]["candidates"] == []
    assert mixed["provenance"]["suggested_token"] is None
    assert mixed["parsed"]["unit_price"] is None
    assert "not the deal currency" in mixed["provenance"]["review_reasons"]
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


_STATED_NOT_PIECE = (
    "18/kg",
    "18元/kg",
    "每kg 18 元",
    "按套出售 88元",
    "立方价 320 元",
    "45元/平方",
    "45元/㎡",
    "30元/卷",
    "5元/对",
    "整箱价 480 元",
    "批发价按箱 480 元",
    "按吨 5200 元",
    "吨价 5200 元",
    "克价 3 元",
    "米价 3.5 元",
    "18元/瓶",
)


def test_a_stated_basis_never_confirms_as_the_raw_per_piece_price(tmp_path, monkeypatch):
    """The per-piece button is absent, and the UI payload cannot rewrite the basis."""
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "phase2a-desk-key")
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.parse import parse_quote
    from src.desk.service import create_deal, ingest_message
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store,
        product="valve",
        quantity=2000,
        target_price=8,
        ceiling_price=40,
        risk_tolerance=0.5,
        seed=6,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    client = TestClient(app)
    headers = {"X-API-Key": "phase2a-desk-key"}

    def review(event_id: str, token: str, **extra: object):
        body = {"supplier_id": supplier, "action": "accept", "chosen_token": token}
        body.update(extra)
        return client.post(
            f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
            headers=headers,
            json=body,
        )

    for text in _STATED_NOT_PIECE:
        reading = parse_quote(text)
        assert reading["parsed"]["unit_price"] is None, text
        assert "unknown" not in reading["provenance"]["unit_basis"], text
        assert reading["provenance"]["candidates"], text
        for prompt in reading["provenance"]["basis_prompts"].values():
            assert prompt["kind"] != "per_piece", text
        token = reading["provenance"]["candidates"][0]
        event = ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier)["event"]
        plain = review(event["id"], token)
        clicked = review(event["id"], token, human_confirmed=True, unit_basis="per_piece")
        assert plain.status_code == 422, text
        assert clicked.status_code == 422, text
        assert plain.json()["detail"] == "unit mismatch", text
        assert clicked.json()["detail"] == "unit mismatch", text
    exact = parse_quote("报价18元")
    assert exact["provenance"]["basis_prompts"]["18"]["kind"] == "per_piece"
    even = parse_quote("报价18元整")
    assert even["provenance"]["basis_prompts"] == {}
    assert even["provenance"]["review_reason"] == "unrecognised term: 整"
    hud = Path("extension/content.js").read_text(encoding="utf-8")
    assert "event.isTrusted" in hud
    assert "basisEvent.isTrusted" in hud
    assert 'mode: "open"' in hud
    store.close()
    reset_stores()


def test_basis_choice_cannot_skip_currency_reference_or_the_band(tmp_path, monkeypatch):
    """A non-deal currency stays 422 even when the per-piece button is posted."""
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "phase2a-desk-key")
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.parse import parse_quote, price_from_token
    from src.desk.service import create_deal, ingest_message
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    client = TestClient(app)
    headers = {"X-API-Key": "phase2a-desk-key"}
    for code in ("USD", "EUR"):
        snapshot = create_deal(
            store,
            product="valve",
            quantity=1000,
            target_price=10,
            ceiling_price=40,
            risk_tolerance=0.5,
            seed=6,
            suppliers=SUPPLIERS,
            currency=code,
        )
        deal_id = snapshot["deal"]["id"]
        supplier = snapshot["suppliers"][0]["id"]

        def review(event_id: str, token: str, **extra: object):
            body = {"supplier_id": supplier, "action": "accept", "chosen_token": token}
            body.update(extra)
            return client.post(
                f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
                headers=headers,
                json=body,
            )

        for text in ("单价 15.6 人民币", "报价 15.6", "报价 15.6，含税", "45元/平方"):
            reading = parse_quote(text, deal_currency=code)
            assert reading["provenance"]["basis_prompts"] == {}, text
            assert "not the deal currency" in reading["provenance"]["review_reasons"], text
            if text == "45元/平方":
                assert "unit mismatch" in reading["provenance"]["review_reasons"]
            event = ingest_message(
                store, deal_id, source="paste", raw_text=text, supplier_id=supplier
            )["event"]
            assert event["parsed"]["unit_price"] is None
            token = "45" if text.startswith("45") else "15.6"
            plain = review(event["id"], token)
            clicked = review(event["id"], token, human_confirmed=True, unit_basis="per_piece")
            assert plain.status_code == 422, (code, text, plain.text)
            assert clicked.status_code == 422, (code, text, clicked.text)
            assert plain.json()["detail"] == "not the deal currency"
            assert clicked.json()["detail"] == "not the deal currency"
        card = next(row for row in store.get_snapshot(deal_id)["suppliers"] if row["id"] == supplier)
        assert card["latest_quote"] is None
        good = "unit price 15.60 USD per piece" if code == "USD" else "unit price 15.60 EUR per piece"
        opened = ingest_message(store, deal_id, source="paste", raw_text=good, supplier_id=supplier)
        assert price_from_token(good, "15.60", deal_currency=code) == pytest.approx(15.60)
        accepted = review(opened["event"]["id"], "15.60")
        assert accepted.status_code == 200, accepted.text
        card = next(row for row in accepted.json()["suppliers"] if row["id"] == supplier)
        assert card["latest_quote"] == pytest.approx(15.60)
    compound = parse_quote("3块5一个")
    assert compound["provenance"]["candidates"] == ["3"]
    assert compound["parsed"]["unit_price"] == pytest.approx(3.5)
    assert price_from_token("3块5一个", "3") == pytest.approx(3.5)
    loose = parse_quote("2块8")
    assert loose["provenance"]["candidates"] == ["2"]
    assert "8" not in loose["provenance"]["candidates"]
    assert loose["provenance"]["confirm_value"] == pytest.approx(2.8)
    assert parse_quote("1块2毛")["provenance"]["confirm_value"] == pytest.approx(1.2)
    store.close()
    reset_stores()


@pytest.mark.parametrize("surface,canon", UNIT_LEXICON)
def test_every_lexicon_unit_refuses_the_per_piece_shortcut(surface, canon):
    """A unit the lexicon knows is never settled by the unknown-basis shortcut."""
    from src.desk.parse import parse_quote

    for text in (
        f"18元/{surface}",
        f"每{surface} 18 元",
        f"{surface}价 18 元",
        f"按{surface} 18 元",
    ):
        got = parse_quote(text)
        for prompt in got["provenance"]["basis_prompts"].values():
            assert prompt.get("kind") != "per_piece", text
        if canon != "per_piece":
            assert got["parsed"]["unit_price"] is None, text


def test_compound_amounts_use_yuan_jiao_and_fen_positions():
    """One walker: a bare digit is jiao, a leading-zero pair is jiao then fen."""
    from src.desk.parse import parse_quote

    five = parse_quote("3块05")
    assert five["provenance"]["candidates"] == ["3"]
    assert five["provenance"]["confirm_value"] == pytest.approx(3.05)
    named = parse_quote("3元5角2分")
    assert named["provenance"]["candidates"] == ["3"]
    assert named["provenance"]["confirm_value"] == pytest.approx(3.52)
    mao = parse_quote("2块8毛8")
    assert mao["provenance"]["candidates"] == ["2"]
    assert mao["provenance"]["confirm_value"] == pytest.approx(2.88)
    separate = parse_quote("1块20")
    assert separate["provenance"]["confirm_value"] == pytest.approx(1.0)


def test_random_terms_never_offer_the_shortcut_or_implicit_currency():
    """A token outside the allowlist, however it is written, blocks both decisions."""
    import random

    from src.desk.parse import parse_quote

    rng = random.Random(20)
    cjk = list("瓶罐筐篓捆扎称重桶柜架勺叉碗盘壶杯")
    latin = ("widget", "crate", "bottle", "kilo", "sqmx", "lotx")
    forms: list[str] = ["称重", "整套", "以公斤计", "单位"]
    for _ in range(24):
        token = "".join(rng.choice(cjk) for _ in range(2))
        word = rng.choice(latin)
        forms.append(token)
        forms.append("\u200b".join(token))
        forms.append(word)
        forms.append("\u200b".join(word))
        forms.append("".join(chr(ord(char) + 0xFEE0) for char in word))
    seen: set[str] = set()
    for form in forms:
        if form in seen:
            continue
        seen.add(form)
        text = f"单价 15.6 元{form}"
        got = parse_quote(text)
        assert got["parsed"]["unit_price"] is None, text
        assert got["provenance"]["currency_evidence"] is None, text
        for prompt in got["provenance"]["basis_prompts"].values():
            assert prompt.get("kind") != "per_piece", text
        reason = str(got["provenance"]["review_reason"] or "")
        assert reason.startswith("unrecognised term:"), (text, reason)
        assert "15.6" in got["provenance"]["candidates"], text


def test_ordinary_per_piece_quotes_still_confirm_and_other_currencies_do_not(tmp_path, monkeypatch):
    """Clean per-piece quotes confirm. Any other currency marker does not."""
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "phase2a-key")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "phase2a-desk-key")
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.parse import parse_quote
    from src.desk.service import create_deal, ingest_message
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(str(tmp_path / "desk.db"))
    client = TestClient(app)
    headers = {"X-API-Key": "phase2a-desk-key"}

    def open_deal(currency: str):
        snapshot = create_deal(
            store,
            product="valve",
            quantity=1000,
            target_price=8,
            ceiling_price=40,
            risk_tolerance=0.5,
            seed=6,
            suppliers=SUPPLIERS,
            currency=currency,
        )
        return snapshot["deal"]["id"], snapshot["suppliers"][0]["id"]

    def review(deal_id: str, supplier: str, event_id: str, token: str, **extra: object):
        body = {"supplier_id": supplier, "action": "accept", "chosen_token": token}
        body.update(extra)
        return client.post(
            f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
            headers=headers,
            json=body,
        )

    deal_id, supplier = open_deal("CNY")
    explicit = parse_quote("单价 14.80 元/件")
    assert explicit["status"] == "accepted"
    assert explicit["parsed"]["unit_price"] == pytest.approx(14.80)
    opened = ingest_message(
        store, deal_id, source="paste", raw_text="单价 14.80 元/件", supplier_id=supplier
    )
    assert review(deal_id, supplier, opened["event"]["id"], "14.80").status_code == 200
    bare = parse_quote("单价 15.60 元")
    assert bare["provenance"]["basis_prompts"]["15.60"]["kind"] == "per_piece"
    assert bare["provenance"]["currency_evidence"] is None
    held = ingest_message(store, deal_id, source="paste", raw_text="单价 15.60 元", supplier_id=supplier)
    assert review(deal_id, supplier, held["event"]["id"], "15.60").status_code == 422
    clicked = review(
        deal_id, supplier, held["event"]["id"], "15.60", human_confirmed=True, unit_basis="per_piece"
    )
    assert clicked.status_code == 200, clicked.text
    moq = ingest_message(
        store, deal_id, source="paste", raw_text="单价 15.60 元，起订量 1000 件", supplier_id=supplier
    )
    assert review(deal_id, supplier, moq["event"]["id"], "15.60").status_code == 200
    rmb = ingest_message(store, deal_id, source="paste", raw_text="31 RMB per piece", supplier_id=supplier)
    assert review(deal_id, supplier, rmb["event"]["id"], "31").status_code == 200
    folded = parse_quote("单\u200b价 15.6 元称\u200b重")
    assert folded["provenance"]["currency_evidence"] is None
    assert folded["provenance"]["basis_prompts"] == {}
    assert folded["provenance"]["review_reason"] == "unrecognised term: 称重"
    wide = parse_quote("单价 15.6 元" + "".join(chr(ord(char) + 0xFEE0) for char in "WIDGET"))
    assert wide["provenance"]["basis_prompts"] == {}
    assert wide["provenance"]["review_reason"] == "unrecognised term: WIDGET"
    for text, token in (
        ("单价 15 港元", "15"),
        ("单价 15 澳元", "15"),
        ("单价 15 韩元", "15"),
        ("单价 15 日元", "15"),
        ("单价 15 新台币", "15"),
        ("单价 15 港币", "15"),
        ("单价 15 泰铢", "15"),
        ("单价 15 卢布", "15"),
        ("单价 15 TWD", "15"),
        ("单价 15 ₩", "15"),
        ("单价 15 刀", "15"),
        ("单价 15 美刀", "15"),
        ("单价 15 欧", "15"),
    ):
        reading = parse_quote(text)
        assert reading["provenance"]["basis_prompts"] == {}, text
        assert reading["provenance"]["currency_evidence"] is None, text
        assert "not the deal currency" in reading["provenance"]["review_reasons"], text
        event = ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier)["event"]
        plain = review(deal_id, supplier, event["id"], token)
        posted = review(
            deal_id, supplier, event["id"], token, human_confirmed=True, unit_basis="per_piece"
        )
        assert plain.status_code == 422, (text, plain.text)
        assert posted.status_code == 422, (text, posted.text)
        assert plain.json()["detail"] == "not the deal currency", text
        assert posted.json()["detail"] == "not the deal currency", text

    usd_id, usd_supplier = open_deal("USD")
    for text, token, plain_ok in (
        ("单价 15.6 美元", "15.6", False),
        ("15.6美元一个", "15.6", True),
        ("美元一个15.6", "15.6", True),
    ):
        reading = parse_quote(text, deal_currency="USD")
        assert reading["provenance"]["currency_evidence"] != "not the deal currency"
        assert "not the deal currency" not in reading["provenance"]["review_reasons"], text
        event = ingest_message(store, usd_id, source="paste", raw_text=text, supplier_id=usd_supplier)["event"]
        plain = review(usd_id, usd_supplier, event["id"], token)
        if plain_ok:
            assert plain.status_code == 200, (text, plain.text)
        else:
            assert plain.status_code == 422, (text, plain.text)
            assert plain.json()["detail"] == "unit mismatch", text
            posted = review(
                usd_id,
                usd_supplier,
                event["id"],
                token,
                human_confirmed=True,
                unit_basis="per_piece",
            )
            assert posted.status_code == 200, (text, posted.text)
    eur_id, eur_supplier = open_deal("EUR")
    euro = ingest_message(store, eur_id, source="paste", raw_text="15.6欧元/件", supplier_id=eur_supplier)
    assert review(eur_id, eur_supplier, euro["event"]["id"], "15.6").status_code == 200
    slang = ingest_message(store, eur_id, source="paste", raw_text="单价 15 刀", supplier_id=eur_supplier)
    refused = review(eur_id, eur_supplier, slang["event"]["id"], "15")
    assert refused.status_code == 422
    assert refused.json()["detail"] == "not the deal currency"
    store.close()
    reset_stores()
