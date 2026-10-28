"""A person can put the solver's push-lower figure in a draft; the send guard re-derives it."""
from __future__ import annotations

import json

import pytest

TEXTS = {
    "Ningbo": "单价 27.80 元/把，起订 1000 把。",
    "Wenzhou": "我们单价 26.50 元/把。",
    "Yiwu": "单价 29.00 元/把。",
    "Foshan": "单价 28.40 元/把，15 天交货。",
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
        store, product="umbrella", quantity=1000, target_price=24, ceiling_price=30,
        risk_tolerance=0.5, seed=11, currency="CNY",
        suppliers=[{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"} for n in TEXTS],
    )
    deal_id = opened["deal"]["id"]
    ids = {row["name"]: row["id"] for row in opened["suppliers"]}
    snapshot = None
    for name, text in TEXTS.items():
        event = service.ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=ids[name])["event"]
        token = event["provenance"].get("suggested_token") or event["provenance"]["candidates"][0]
        snapshot = service.accept_review(store, deal_id, event["id"], ids[name], chosen_token=token)
    yield store, deal_id, ids, snapshot
    store.close()


def _card(snapshot, supplier_id):
    return next(row for row in snapshot["suppliers"] if row["id"] == supplier_id)


def _open_draft(store, deal_id, supplier_id):
    return next(
        row for row in store.drafts(deal_id) if row["supplier_id"] == supplier_id and row["status"] == "draft"
    )


def test_default_draft_carries_the_best_price_on_the_table(desk):
    store, deal_id, ids, snapshot = desk
    leader = _card(snapshot, ids["Wenzhou"])
    assert leader["rank"] == 1 and leader["offer_mode"] == "default"
    assert leader["counter"] == leader["default_counter"] == 26.5
    assert leader["push_counter"] is not None and leader["push_counter"] < leader["counter"]


def test_push_mode_puts_the_solver_figure_in_the_draft_and_the_guard_accepts_it(desk):
    from src.desk import service
    from src.desk.verify import _guard_send

    store, deal_id, ids, snapshot = desk
    supplier = ids["Wenzhou"]
    before = _card(snapshot, supplier)
    pushed = service.set_offer_mode(store, deal_id, supplier, "push", human_confirmed=True)
    card = _card(pushed, supplier)
    assert card["offer_mode"] == "push"
    assert card["counter_relation"] == "push_lower"
    assert card["counter"] == before["push_counter"]
    assert card["default_counter"] == before["counter"]
    assert card["counter"] < card["default_counter"] <= card["latest_quote"]
    draft = _open_draft(store, deal_id, supplier)
    assert f"{card['counter']:.2f}" in draft["body"]
    record = store.get_decision(draft["decision_id"])["record"]
    assert record["offer_mode"] == "push" and record["counter"] == card["counter"]
    assert all(check["passed"] for check in record["checks"])
    deal = store.get_deal(deal_id)
    assert _guard_send(store, draft, deal)["counter"] == card["counter"]
    # Other suppliers are untouched.
    assert _card(pushed, ids["Ningbo"])["offer_mode"] == "default"


def test_switching_back_restores_the_default_and_is_not_held(desk):
    from src.desk import service
    from src.desk.verify import _guard_send

    store, deal_id, ids, snapshot = desk
    supplier = ids["Wenzhou"]
    service.set_offer_mode(store, deal_id, supplier, "push", human_confirmed=True)
    back = service.set_offer_mode(store, deal_id, supplier, "default", human_confirmed=True)
    card = _card(back, supplier)
    assert card["offer_mode"] == "default"
    assert card["counter"] == card["default_counter"] == 26.5
    assert card["counter_held"] is False
    draft = _open_draft(store, deal_id, supplier)
    assert "26.50" in draft["body"]
    assert _guard_send(store, draft, store.get_deal(deal_id))["offer_mode"] == "default"


def test_a_forged_push_record_without_the_chained_choice_is_refused(desk):
    """A record rewritten to claim push is refused: its hash no longer matches the chain."""
    import hashlib

    from src.desk.service import SendRefused
    from src.desk.verify import _guard_send

    store, deal_id, ids, snapshot = desk
    supplier = ids["Wenzhou"]
    card = _card(snapshot, supplier)
    draft = _open_draft(store, deal_id, supplier)
    stored = store.get_decision(draft["decision_id"])
    forged = dict(stored["record"], offer_mode="push", counter=card["push_counter"])
    digest = hashlib.sha256(
        json.dumps(forged, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    store.conn.execute("DROP TRIGGER IF EXISTS decisions_no_update")
    store.conn.execute(
        "UPDATE decisions SET record_json = ?, record_sha256 = ? WHERE id = ?",
        (json.dumps(forged, sort_keys=True), digest, draft["decision_id"]),
    )
    store.conn.commit()
    with pytest.raises(SendRefused):
        _guard_send(store, draft, store.get_deal(deal_id))


def test_offer_mode_needs_a_person_and_a_known_mode(desk):
    from src.desk import service
    from src.desk.store import DeskRejected

    store, deal_id, ids, _snapshot = desk
    with pytest.raises(DeskRejected):
        service.set_offer_mode(store, deal_id, ids["Yiwu"], "push", human_confirmed=False)
    with pytest.raises(DeskRejected):
        service.set_offer_mode(store, deal_id, ids["Yiwu"], "lowest", human_confirmed=True)
    with pytest.raises(KeyError):
        service.set_offer_mode(store, deal_id, "nobody", "push", human_confirmed=True)
    kinds = [entry["type"] for entry in store.verify_chain(deal_id)["entries"]]
    assert "offer_mode" not in kinds


def test_offer_mode_route_requires_the_key_and_validates(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "offer-mode-key")
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.app.main import app
    from src.desk.store import reset_stores

    reset_stores()
    client = TestClient(app)
    headers = {"X-API-Key": "offer-mode-key"}
    opened = client.post("/api/v1/desk/deals", headers=headers, json={
        "product": "umbrella", "quantity": 1000, "target_price": 24, "ceiling_price": 30,
        "suppliers": [{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"} for n in TEXTS],
    }).json()
    deal_id, supplier = opened["deal"]["id"], opened["suppliers"][0]["id"]
    path = f"/api/v1/desk/deals/{deal_id}/suppliers/{supplier}/offer-mode"
    assert client.post(path, json={"mode": "push", "human_confirmed": True}).status_code == 401
    assert client.post(path, headers=headers, json={"mode": "lowest", "human_confirmed": True}).status_code == 422
    ok = client.post(path, headers=headers, json={"mode": "push", "human_confirmed": True})
    assert ok.status_code == 200, ok.text
    reset_stores()
