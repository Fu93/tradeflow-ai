"""T10 audit: the verify endpoint recomputes the chain."""
from __future__ import annotations

import os

import pytest

KEY = "desk-step-audit-key"
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


def test_audit_verify_and_evidence_match_the_chain(desk_env):
    from fastapi.testclient import TestClient
    from src.app.main import app
    from src.desk.service import create_deal
    from src.desk.store import DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=10,
        target_price=4,
        ceiling_price=8,
        risk_tolerance=0.5,
        seed=1,
        currency="CNY",
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    store.close()
    client = TestClient(app)
    headers = {"X-API-Key": KEY}
    verified = client.get(f"/api/v1/desk/deals/{deal_id}/audit/verify", headers=headers)
    assert verified.status_code == 200, verified.text
    assert verified.json()["ok"] is True
    evidence = client.get(f"/api/v1/desk/deals/{deal_id}/evidence.json", headers=headers)
    assert evidence.status_code == 200
    assert evidence.json()["ok"] is True
    assert "entries" in evidence.json()
