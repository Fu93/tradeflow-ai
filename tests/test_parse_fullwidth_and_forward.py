"""Two rule-reader misreads found by the held-out set (2026-10-28), fixed after its baseline.

1. Full-width digits with a full-width full stop ("２８．５０元") were split and read as 50.
2. A price under a Chinese forward rule ("—— 转发 ——") was taken as the current offer.
"""
from __future__ import annotations

import pytest

from src.desk.parse import parse_quote, price_from_token
from src.desk.span_read import read_one


@pytest.mark.parametrize(
    "text, expected",
    [
        ("单价：２８．５０元／件，含税。", 28.5),
        ("单价：２８元／件。", 28.0),
        ("Unit price ３１．５０ CNY/pc.", 31.5),
    ],
)
def test_full_width_amounts_are_read_whole(text, expected):
    reading = parse_quote(text, deal_currency="CNY")
    assert reading["status"] == "accepted"
    assert reading["parsed"]["unit_price"] == pytest.approx(expected)
    token = reading["provenance"]["suggested_token"]
    # The token is the message's own characters, so a confirmation quotes the message.
    assert token in text
    assert price_from_token(text, token, deal_currency="CNY") == pytest.approx(expected)


def test_span_reader_reads_full_width_amounts():
    assert read_one("单价：２８．５０元") == pytest.approx(28.5)
    assert read_one("１０００把 ２８元") is None  # two amounts: not one reading


@pytest.mark.parametrize(
    "rule",
    ["—— 转发 ——", "---- 轉發郵件 ----", "=== 转发消息 ===", "-- 转发 --", "———转发———"],
)
def test_text_under_a_forward_rule_is_not_the_current_offer(rule):
    text = "同上次报价。\n\n" + rule + "\n单价 31.00 元"
    reading = parse_quote(text, deal_currency="CNY")
    assert reading["status"] == "review"
    assert reading["parsed"]["unit_price"] is None


def test_a_word_forward_inside_a_sentence_does_not_cut_the_message():
    reading = parse_quote("单价 31.00 元，请确认——转发给老板了", deal_currency="CNY")
    assert reading["status"] == "accepted"
    assert reading["parsed"]["unit_price"] == pytest.approx(31.0)


def test_full_width_price_runs_through_the_desk_and_the_send_guard(tmp_path, monkeypatch):
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk import service
    from src.desk.store import DeskStore
    from src.desk.verify import _guard_send

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = service.create_deal(
        store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
        risk_tolerance=0.5, seed=11, currency="CNY",
        suppliers=[{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"}
                   for n in ("Ningbo", "Wenzhou", "Yiwu", "Foshan")],
    )
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    text = "单价：２８．５０元／件，含税。"
    event = service.ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier)["event"]
    snapshot = service.accept_review(store, deal_id, event["id"], supplier, chosen_token="２８．５０")
    card = next(row for row in snapshot["suppliers"] if row["id"] == supplier)
    assert card["latest_quote"] == pytest.approx(28.5)
    assert card["counter"] is not None
    draft = next(row for row in store.drafts(deal_id) if row["supplier_id"] == supplier and row["status"] == "draft")
    assert _guard_send(store, draft, store.get_deal(deal_id))["ask"] == pytest.approx(28.5)
    store.close()
