"""Import the latest visible supplier bubble from the mock chat page."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")


def test_mock_chat_import_respects_captcha(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "desk-mock-key")
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.app.main import app
    from src.desk.store import reset_stores

    reset_stores()
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto((ROOT / "extension" / "mock_chat.html").as_uri())
        page.add_script_tag(path=str(ROOT / "extension" / "desk_import.js"))
        page.click("#btn-sim-quote")
        payload = page.evaluate("() => tradeflowImportPayload(document)")
        assert payload["ok"] is True
        assert payload["simulated"] is True
        assert "14.80" in payload["text"]
        assert "浙江" in payload["shop"]
        mapped = page.evaluate(
            """() => tradeflowMatchSuppliers(
                [{id: 'a', name: 'Ningbo'}, {id: 'b', name: '浙江頂尖雨具製造實業有限公司'}],
                tradeflowImportPayload(document)
            ).map((row) => row.id)"""
        )
        assert mapped == ["b"]
        ambiguous = page.evaluate(
            """() => tradeflowMatchSuppliers(
                [{id: 'a', name: 'Ningbo'}, {id: 'b', name: 'Wenzhou'}],
                {shop: 'Ningbo and Wenzhou', title: '', url: ''}
            ).map((row) => row.id)"""
        )
        assert ambiguous == ["a", "b"]
        page.click("#btn-sim-captcha")
        blocked = page.evaluate("() => tradeflowImportPayload(document)")
        assert blocked["ok"] is False
        assert blocked["reason"] == "captcha"
        browser.close()

    client = TestClient(app)
    headers = {"X-API-Key": "desk-mock-key"}
    deal_body = {
        "product": "umbrella",
        "quantity": 1000,
        "target_price": 12,
        "ceiling_price": 20,
        "risk_tolerance": 0.5,
        "suppliers": [
            {"name": "Ningbo", "address": "ningbo@example.test"},
            {"name": "Wenzhou", "address": "wenzhou@example.test"},
            {"name": "Yiwu", "address": "yiwu@example.test"},
            {"name": "Foshan", "address": "foshan@example.test"},
        ],
    }
    denied = client.post("/api/v1/desk/deals", json=deal_body)
    assert denied.status_code == 401
    created = client.post(
        "/api/v1/desk/deals",
        json=deal_body,
        headers=headers,
    )
    assert created.status_code == 200, created.text
    deal = created.json()
    supplier_id = next(row["id"] for row in deal["suppliers"] if row["name"] == "Ningbo")
    quoted = client.post(
        f"/api/v1/desk/deals/{deal['deal']['id']}/quotes",
        json={
            "source": "chat-import",
            "raw_text": payload["text"],
            "supplier_id": supplier_id,
            "simulated": True,
        },
        headers=headers,
    )
    assert quoted.status_code == 200, quoted.text
    event = quoted.json()["event"]
    assert event["source"] == "chat-import"
    assert event["status"] == "review"
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["review_reason"] == "simulated test fixture"
    reset_stores()
