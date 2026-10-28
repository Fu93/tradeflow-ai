"""A supplier's stated quote validity is read and shown, and never moves a rank, counter or draft."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.desk.validity import read_validity

ROOT = Path(__file__).resolve().parents[1]
RECEIVED = "2026-10-28T09:00:00+00:00"


@pytest.mark.parametrize(
    ("text", "until", "counted_from"),
    [
        ("单价 27.80 元/把，报价有效期 7 天。", "2026-11-04", "received"),
        ("單價 27.80 元，本報價三天內有效", "2026-10-31", "received"),
        ("价格有效期：一个月", "2026-11-28", "received"),
        ("有效期：15天，交期20天", "2026-11-12", "received"),
        ("有效期十五天", "2026-11-12", "received"),
        ("有效期至11月5日", "2026-11-05", "stated_date"),
        ("报价有效期至１１月５日", "2026-11-05", "stated_date"),
        ("有效期至2026-11-05", "2026-11-05", "stated_date"),
        ("1月5日前有效", "2027-01-05", "stated_date"),
        ("Unit price CNY 27.80, valid for 2 weeks.", "2026-11-11", "received"),
        ("valid for a week", "2026-11-04", "received"),
        ("7 days validity", "2026-11-04", "received"),
        ("This quotation is valid until Nov 5.", "2026-11-05", "stated_date"),
        ("Price valid until 5th November 2026", "2026-11-05", "stated_date"),
        ("valid until 2026-11-05", "2026-11-05", "stated_date"),
    ],
)
def test_stated_validity_becomes_a_date(text, until, counted_from):
    reading = read_validity(text, RECEIVED)
    assert reading["status"] == "stated"
    assert reading["valid_until"] == until
    assert reading["counted_from"] == counted_from
    # The span is verbatim, so the desk can show where the date came from.
    assert reading["span"] in text


@pytest.mark.parametrize(
    "text",
    [
        "交期15天",  # lead time, not validity
        "产品有效期 24 个月，单价 27.80 元",  # shelf life
        "有效期24个月",  # months with no price word: likely shelf life
        "CE certificate valid until 2027-01-01. Price 27.80 RMB",
        "质保期 5 个工作日",
        "valid till 11/05",  # day and month order is ambiguous
        "> 报价有效期 3 天\n单价 26 元",  # quoted history, not this message
        "有效期 5000 天",  # beyond a year: a misreading
        "1030天内有效",  # not 30 days read out of 1030
    ],
)
def test_not_a_quote_validity(text):
    assert read_validity(text, RECEIVED) is None


def test_working_days_are_shown_without_a_date():
    reading = read_validity("Quote valid for 5 business days", RECEIVED)
    assert reading["status"] == "working_days"
    assert reading["valid_until"] is None
    assert reading["span"] == "valid for 5 business days"


def test_two_validities_that_disagree_give_no_date():
    reading = read_validity("报价有效期7天，3天内有效", RECEIVED)
    assert reading["status"] == "conflict"
    assert reading["valid_until"] is None
    assert reading["spans"] == ["报价有效期7天", "3天内有效"]


def test_a_date_without_a_year_rolls_forward_only_across_new_year():
    assert read_validity("有效期至1月5日", "2026-12-28T00:00:00+00:00")["valid_until"] == "2027-01-05"
    # A date a week back stays in the past, so it shows as expired.
    assert read_validity("有效期至10月20日", RECEIVED)["valid_until"] == "2026-10-20"


def test_no_received_time_means_no_reading():
    assert read_validity("报价有效期 7 天", None) is None
    assert read_validity("报价有效期 7 天", "not a time") is None


QUOTES = {
    "Ningbo": "单价 26.00 元/把。",
    "Wenzhou": "单价 26.50 元/把。",
    "Yiwu": "单价 29.00 元/把。",
    "Foshan": "单价 28.40 元/把。",
}
VALIDITY = {
    "Ningbo": "报价有效期至2020-01-01。",  # long expired
    "Wenzhou": "报价有效期 7 天。",
    "Yiwu": "Quote valid for 5 business days.",
    "Foshan": "",
}


def _run(tmp_path, monkeypatch, with_validity: bool):
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk import service
    from src.desk.store import DeskStore
    from src.desk.verify import _guard_send

    store = DeskStore(str(tmp_path / ("with.db" if with_validity else "without.db")))
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
            body = text + (VALIDITY[name] if with_validity else "")
            event = service.ingest_message(
                store, deal_id, source="paste", raw_text=body, supplier_id=ids[name]
            )["event"]
            token = event["provenance"].get("suggested_token") or event["provenance"]["candidates"][0]
            snapshot = service.accept_review(store, deal_id, event["id"], ids[name], chosen_token=token)
        guards = {}
        for draft in store.drafts(deal_id):
            if draft["status"] == "draft":
                guards[draft["supplier_id"]] = _guard_send(store, draft, store.get_deal(deal_id))["counter"]
        names = {value: key for key, value in ids.items()}
        return snapshot, {names[key]: value for key, value in guards.items()}
    finally:
        store.close()


def test_validity_is_shown_and_never_moves_rank_counter_or_guard(tmp_path, monkeypatch):
    plain, plain_guard = _run(tmp_path, monkeypatch, with_validity=False)
    stated, stated_guard = _run(tmp_path, monkeypatch, with_validity=True)
    fields = ("rank", "latest_quote", "counter", "default_counter", "push_counter", "counter_relation")
    before = {row["name"]: tuple(row[f] for f in fields) for row in plain["suppliers"]}
    after = {row["name"]: tuple(row[f] for f in fields) for row in stated["suppliers"]}
    assert before == after
    assert plain_guard == stated_guard and stated_guard

    cards = {row["name"]: row for row in stated["suppliers"]}
    assert cards["Ningbo"]["quote_validity"]["valid_until"] == "2020-01-01"
    assert cards["Ningbo"]["rank"] == 1  # expired, and still ranked: a person decides
    assert cards["Wenzhou"]["quote_validity"]["status"] == "stated"
    assert cards["Wenzhou"]["quote_validity"]["counted_from"] == "received"
    assert cards["Yiwu"]["quote_validity"]["status"] == "working_days"
    assert cards["Foshan"]["quote_validity"] is None
    assert all(row["quote_received_at"] for row in stated["suppliers"])


def test_desk_and_side_panel_show_the_flag_without_using_it():
    desk = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    panel = (ROOT / "extension/desk_panel.js").read_text(encoding="utf-8")
    for script in (desk, panel):
        assert "function validityText(" in script
        assert "Confirm the price still stands before you send." in script or \
            "confirm the price still stands before you send." in script
    assert "It is still ranked." in desk
    assert "counted from when the desk received the message" in desk
    # The flag is display only: neither script sends it back to the server.
    assert "quote_validity:" not in desk and "quote_validity:" not in panel
