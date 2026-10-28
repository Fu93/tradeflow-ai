"""Minimum order and lead time are read with their words, shown, and never move a rank or counter."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.desk.terms import read_lead_time, read_moq

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("text", "quantity", "span"),
    [
        ("单价 27.80 元/把，起订 1000 把，15 天出货。", 1000, "起订 1000 把"),
        ("Unit price 26 RMB/pc, MOQ 50,000 pcs, lead time 90 days", 50000, "MOQ 50,000 pcs"),
        ("单价 26 元/件，最低起订量5万件，交期90天", 50000, "最低起订量5万件"),
        ("MOQ: 3k pcs. Delivery time: 4 weeks.", 3000, "MOQ: 3k pcs"),
        ("1000把起订，交期 25-30 天", 1000, "1000把起订"),
        ("起訂量：２０００件", 2000, "起訂量：２０００件"),
        ("Minimum order quantity is 500 units", 500, "Minimum order quantity is 500 units"),
    ],
)
def test_minimum_order(text, quantity, span):
    found = read_moq(text)
    assert found == {"status": "stated", "quantity": quantity, "span": span}


@pytest.mark.parametrize(
    ("text", "days", "span"),
    [
        ("单价 27.80 元/把，起订 1000 把，15 天出货。", 15, "15 天出货"),
        ("lead time 90 days", 90, "lead time 90 days"),
        ("交期90天", 90, "交期90天"),
        ("Delivery time: 4 weeks.", 28, "Delivery time: 4 weeks"),
        ("交期 25-30 天", 30, "交期 25-30 天"),  # a range shows its upper end
        ("交貨期約20日", 20, "交貨期約20日"),
    ],
)
def test_lead_time(text, days, span):
    assert read_lead_time(text) == {"status": "stated", "days": days, "span": span}


def test_working_days_and_conflicts():
    assert read_lead_time("交期 20 个工作日") == {"status": "working_days", "days": None, "span": "交期 20 个工作日"}
    assert read_lead_time("交期15天，30天交货")["status"] == "conflict"
    assert read_moq("起订 500 件，起订 1000 件")["status"] == "conflict"


@pytest.mark.parametrize(
    "text",
    [
        "单价 26 元/件",
        "老板，周期多久？",
        "> 起订 99999 件\n单价 26 元",  # quoted history
        "2000 件以上可以优惠",  # a tier, not a minimum
        "单价 26 元，30 天付款",  # payment terms, not lead time
    ],
)
def test_nothing_to_read(text):
    assert read_moq(text) is None
    assert read_lead_time(text) is None


def test_a_long_digit_run_stays_fast():
    text = "MOQ " + "1" * 20000 + " pcs, 交期 " + "9" * 20000 + " 天"
    started = time.perf_counter()
    read_moq(text)
    read_lead_time(text)
    assert time.perf_counter() - started < 2.0


QUOTES = {
    "Ningbo": "单价 26.00 元/把，MOQ 50,000 pcs，交期 90 天。",
    "Wenzhou": "单价 26.50 元/把，起订 500 把，15 天出货。",
    "Yiwu": "单价 29.00 元/把。",
    "Foshan": "单价 28.40 元/把。",
}


def test_cards_show_terms_flag_a_big_moq_and_keep_the_ranking(tmp_path, monkeypatch):
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk import service
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    try:
        opened = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
            risk_tolerance=0.5, seed=11, currency="CNY",
            suppliers=[{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"} for n in QUOTES],
        )
        deal_id = opened["deal"]["id"]
        ids = {row["name"]: row["id"] for row in opened["suppliers"]}
        snapshot = None
        for name, text in QUOTES.items():
            event = service.ingest_message(
                store, deal_id, source="paste", raw_text=text, supplier_id=ids[name]
            )["event"]
            token = event["provenance"].get("suggested_token") or event["provenance"]["candidates"][0]
            snapshot = service.accept_review(store, deal_id, event["id"], ids[name], chosen_token=token)
    finally:
        store.close()
    cards = {row["name"]: row for row in snapshot["suppliers"]}
    # Cheapest, so still first; the MOQ is flagged, not used to rank.
    assert cards["Ningbo"]["rank"] == 1
    assert cards["Ningbo"]["supplier_moq"]["quantity"] == 50000
    assert cards["Ningbo"]["moq_above_order"] is True
    assert cards["Ningbo"]["supplier_lead_time"]["days"] == 90
    assert cards["Wenzhou"]["moq_above_order"] is False
    assert cards["Wenzhou"]["supplier_lead_time"]["span"] == "15 天出货"
    assert cards["Yiwu"]["supplier_moq"] is None and cards["Yiwu"]["supplier_lead_time"] is None


def test_desk_shows_terms_folds_the_working_and_offers_a_new_deal():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    page = (ROOT / "src/web/templates/desk.html").read_text(encoding="utf-8")
    assert "function termsLines(" in script
    assert "is above your order. It is still ranked." in script
    assert 'summary.textContent = "How these figures were worked out"' in script
    assert "workingsOpen.has(supplier.id)" in script
    assert 'id="new-deal"' in page
    assert 'sessionStorage.removeItem("tfDeskDeal")' in script
