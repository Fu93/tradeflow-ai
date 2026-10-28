"""Freight, fees and duty typed with a quote rank suppliers by landed cost, end to end."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUOTES = {
    # name: (message, freight per unit, fees per unit, duty rate)
    "Ningbo": ("单价 26.00 元/把。", 3.0, 0.0, 0.0),
    "Wenzhou": ("单价 26.50 元/把。", 1.0, 0.0, 0.0),
    "Yiwu": ("单价 29.00 元/把。", 1.0, 0.0, 0.0),
    "Foshan": ("单价 28.40 元/把。", 1.0, 0.0, 0.0),
}


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk import service
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = service.create_deal(
        store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
        risk_tolerance=0.5, seed=11, currency="CNY",
        suppliers=[{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"} for n in QUOTES],
    )
    yield store, opened["deal"]["id"], {row["name"]: row["id"] for row in opened["suppliers"]}
    store.close()


def _quote(store, deal_id, supplier_id, text, **landed):
    from src.desk import service

    event = service.ingest_message(
        store, deal_id, source="paste", raw_text=text, supplier_id=supplier_id, **landed
    )["event"]
    token = event["provenance"].get("suggested_token") or event["provenance"]["candidates"][0]
    return service.accept_review(store, deal_id, event["id"], supplier_id, chosen_token=token)


def test_complete_landed_inputs_rank_by_landed_cost_and_the_guard_agrees(desk):
    from src.desk.verify import _guard_send

    store, deal_id, ids = desk
    snapshot = None
    for name, (text, freight, fees, duty) in QUOTES.items():
        snapshot = _quote(store, deal_id, ids[name], text, freight=freight, fees=fees, duty_rate=duty)
    assert snapshot["ranking_basis"] == "landed_cost"
    ranks = {row["name"]: row["rank"] for row in snapshot["suppliers"]}
    # Ningbo has the lowest unit price (26.00) but 3.00 freight: landed 29.00.
    assert ranks["Wenzhou"] == 1
    assert ranks["Ningbo"] == 2
    cards = {row["name"]: row for row in snapshot["suppliers"]}
    assert cards["Ningbo"]["landed_cost"] == pytest.approx(29.0)
    assert cards["Wenzhou"]["landed_cost"] == pytest.approx(27.5)
    draft = next(
        row for row in store.drafts(deal_id) if row["supplier_id"] == ids["Wenzhou"] and row["status"] == "draft"
    )
    assert _guard_send(store, draft, store.get_deal(deal_id))["counter"] is not None


def test_a_missing_component_falls_back_to_the_unit_price(desk):
    store, deal_id, ids = desk
    snapshot = None
    for name, (text, freight, fees, duty) in QUOTES.items():
        landed = {"freight": freight, "fees": fees, "duty_rate": duty} if name != "Yiwu" else {"freight": freight}
        snapshot = _quote(store, deal_id, ids[name], text, **landed)
    assert snapshot["ranking_basis"] == "unit_price"
    ranks = {row["name"]: row["rank"] for row in snapshot["suppliers"]}
    assert ranks["Ningbo"] == 1


def test_desk_form_sends_all_three_or_none():
    page = (ROOT / "src/web/templates/desk.html").read_text(encoding="utf-8")
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    for element in ('id="paste-freight"', 'id="paste-fees"', 'id="paste-duty"'):
        assert element in page
    assert "function landedInputs()" in script
    assert "Fill freight, fees and duty together" in script
    assert "duty_rate: numbers[2] / 100" in script
    assert "if (landed.values) Object.assign(payload, landed.values);" in script
