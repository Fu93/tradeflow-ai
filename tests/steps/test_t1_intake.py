"""T1 intake: currency comes from the deal, and a client flag cannot make a price eligible."""
from __future__ import annotations

import json

import pytest

KEY = "desk-step-intake-key"
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


@pytest.fixture
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    from src.desk.store import reset_stores

    reset_stores()
    yield
    reset_stores()


def _client():
    from fastapi.testclient import TestClient
    from src.app.main import app

    return TestClient(app)


def test_usd_deal_rejects_a_cny_token_and_stores_the_raw_hash(desk_env):
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskRejected, DeskStore
    import os

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=100,
        target_price=4,
        ceiling_price=8,
        risk_tolerance=0.5,
        seed=3,
        currency="USD",
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    opened = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 15.60 元",
        supplier_id=snapshot["suppliers"][0]["id"],
        provenance_extra={"simulated": True},
    )
    event = store.event(deal_id, opened["event"]["id"])
    assert event["raw_sha256"]
    with pytest.raises(DeskRejected):
        accept_review(store, deal_id, event["id"], snapshot["suppliers"][0]["id"], chosen_token="15.60")
    store.close()


def test_intake_routes_fail_closed(desk_env):
    client = _client()
    headers = {"X-API-Key": KEY}
    missing = client.get("/api/v1/desk/deals/" + ("ab" * 16), headers=headers)
    assert missing.status_code == 404
    bad = client.get("/api/v1/desk/deals/has-space", headers=headers)
    assert bad.status_code == 422
    created = client.post(
        "/api/v1/desk/deals",
        headers=headers,
        json={
            "product": "valve",
            "quantity": 100,
            "target_price": 4,
            "ceiling_price": 8,
            "currency": "USD",
            "suppliers": SUPPLIERS,
        },
    )
    assert created.status_code == 200, created.text
    deal_id = created.json()["deal"]["id"]
    assert created.json()["deal"]["currency"] == "USD"
    priced = client.post(
        f"/api/v1/desk/deals/{deal_id}/quotes",
        headers=headers,
        json={"source": "paste", "raw_text": "hello", "llm_fields": {"price": 1}},
    )
    assert priced.status_code == 422
    gone = client.post(
        f"/api/v1/desk/deals/{deal_id}/quotes",
        headers=headers,
        json={"source": "paste", "raw_text": "hello", "supplier_id": "missing-supplier"},
    )
    assert gone.status_code in {404, 422}
    review = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/no-such-event",
        headers=headers,
        json={"action": "hold"},
    )
    assert review.status_code == 422
    evidence = client.get("/api/v1/desk/deals/" + ("cd" * 16) + "/evidence.json", headers=headers)
    assert evidence.status_code == 404
    verify = client.get("/api/v1/desk/deals/" + ("cd" * 16) + "/audit/verify", headers=headers)
    assert verify.status_code == 404
    poll = client.post("/api/v1/desk/poll", headers=headers, json={"channel": "fax"})
    assert poll.status_code == 422
    idle = client.post("/api/v1/desk/poll", headers=headers, json={"channel": "imap"})
    assert idle.status_code == 200
    assert idle.json()["status"] in {"not_configured", "no-deals", "ok"}
    body = json.dumps({"ok": True})
    assert "ok" in body
