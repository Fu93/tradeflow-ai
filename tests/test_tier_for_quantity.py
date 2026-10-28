"""A tier table pre-selects the row for the deal's quantity; the quote still waits for a person."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.desk.tiers import tier_for_quantity

ROOT = Path(__file__).resolve().parents[1]

LADDERS = {
    "range_then_open": "1000-4999 把 28 元/把，5000 把以上 26 元/把",
    "open_rows": "1000把以上 28元/把；5000把以上 26元/把",
    "breakpoints": "单价：1000件 28元，3000件 27元，5000件 26元",
    "moq_then_plus": "MOQ 1000: 28 RMB/pc; 5000+: 26 RMB/pc",
    "lines_from_one": "1-999 pcs: 30 RMB/pc\n1000-4999 pcs: 28 RMB/pc\n5000+ pcs: 26 RMB/pc",
    "full_width": "１０００－４９９９件 ２８元，５０００件以上 ２６元",
    "thousands_commas": "1,000-4,999 pcs: RMB 28.50\n5,000 pcs and above: RMB 26.80",
    "k_touching": "1k-5k pcs ¥28, 5k+ pcs ¥26",
}


@pytest.mark.parametrize(
    ("name", "quantity", "token"),
    [
        ("range_then_open", 1000, "28"),
        ("range_then_open", 6000, "26"),
        ("open_rows", 3500, "28"),
        ("open_rows", 5000, "26"),
        ("breakpoints", 3500, "27"),
        ("breakpoints", 6000, "26"),
        ("moq_then_plus", 6000, "26"),
        ("lines_from_one", 300, "30"),
        ("lines_from_one", 2000, "28"),
        ("full_width", 6000, "26"),
        ("thousands_commas", 6000, "26.80"),
        ("k_touching", 1000, "28"),
        ("k_touching", 6000, "26"),
    ],
)
def test_the_row_for_the_quantity(name, quantity, token):
    found = tier_for_quantity(LADDERS[name], quantity)
    assert found["token"] == token
    assert found["below_smallest"] is False
    assert token in found["row"]


def test_below_the_smallest_row_is_flagged():
    found = tier_for_quantity(LADDERS["range_then_open"], 300)
    assert found["token"] == "28" and found["below_smallest"] is True


@pytest.mark.parametrize(
    ("text", "quantity"),
    [
        ("1k-5k pcs ¥28, 5k+ pcs ¥26", 5000),  # exactly on a shared boundary
        ("1000-1999件 28元，3000件以上 26元", 2500),  # in a gap between rows
        ("1000-5000件 28元，3000件以上 26元", 4000),  # rows overlap
        ("单价 27.80 元/把，起订 1000 把", 1000),  # one price is not a ladder
        ("1000件 28元，USD 4 for 5000 pcs", 6000),  # another currency
        ("1000件 28元，5000件 28元", 6000),  # the same price twice
        ("老板好，隔壁厂给你 24 元那是次料，我们正品单价 27.80 元/把，起订 1000 把，15 天出货。", 1000),
        ("1件以上 30元，1000件以上 28元", 1000),  # an open row of one piece
        ("> 1000件 28元\n> 5000件 26元\n单价 27 元", 6000),  # quoted history is not read
        (LADDERS["range_then_open"], None),
        (LADDERS["range_then_open"], 0),
    ],
)
def test_not_a_clear_ladder(text, quantity):
    assert tier_for_quantity(text, quantity) is None


def test_a_long_digit_run_does_not_backtrack():
    text = "1" * 20000 + " 件 28 元，" + "9" * 20000 + "-" + "8" * 20000 + " 件 26 元"
    started = time.perf_counter()
    tier_for_quantity(text, 1000)
    assert time.perf_counter() - started < 2.0


def test_desk_pre_selects_the_row_and_keeps_the_quote_in_review(tmp_path, monkeypatch):
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk import service
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    try:
        opened = service.create_deal(
            store, product="umbrella", quantity=6000, target_price=24, ceiling_price=30,
            risk_tolerance=0.5, seed=11, currency="CNY",
            suppliers=[{"name": n, "address": f"{n}@example.test", "channel": "email"} for n in "abcd"],
        )
        deal_id = opened["deal"]["id"]
        supplier = opened["suppliers"][0]["id"]
        # The rules pre-select the first row (28); the tier step picks the 5000+ row.
        event = service.ingest_message(
            store, deal_id, source="paste", raw_text=LADDERS["breakpoints"], supplier_id=supplier
        )["event"]
        provenance = event["provenance"]
        assert event["status"] == "review"
        assert provenance["suggested_token"] == "26"
        assert provenance["suggested_by"] == "tier"
        assert provenance["rules_suggested_token"] == "28"
        assert provenance["tier_pick"]["row"] == "5000件 26元"
        assert (event.get("parsed") or {}).get("unit_price") is None
        snapshot = service.accept_review(store, deal_id, event["id"], supplier, chosen_token="26")
        card = next(row for row in snapshot["suppliers"] if row["id"] == supplier)
        assert card["latest_quote"] == pytest.approx(26.0)
    finally:
        store.close()


def test_the_held_out_rule_reader_is_untouched():
    """The tier step lives outside parse_quote, so the rules-only evaluation does not move."""
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("eval_quote_reader", ROOT / "scripts" / "eval_quote_reader.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    rows, _sha = ev.load()
    report = ev.evaluate(rows, [], base="http://fake.invalid", target=30, ceiling=40)
    baseline = json.loads((ROOT / "results" / "quote_reader_eval_rules_only.json").read_text(encoding="utf-8"))
    assert report["readers"]["rules"]["tally"] == baseline["readers"]["rules"]["tally"]


def test_desk_explains_the_tier_pick():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert "Tier table: for " in script
    assert "is below the smallest row" in script
