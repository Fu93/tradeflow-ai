"""The desk's sample deal and sample message: fictional, valid, and stored as simulated."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "src" / "web" / "static" / "samples" / "desk_sample.json"
KEY = "desk-sample-key"


@pytest.fixture
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "DESK_SMTP_SEND"):
        monkeypatch.delenv(name, raising=False)
    from src.desk.store import reset_stores

    reset_stores()
    yield tmp_path
    reset_stores()


def _sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_sample_is_labelled_fictional_and_uses_a_reserved_domain():
    data = _sample()
    assert "fictional" in data["label"].lower() and "not a real quote" in data["label"].lower()
    for _name, address in data["deal"]["suppliers"]:
        assert address.endswith("@example.test")


def test_sample_deal_passes_the_deal_schema():
    from src.desk.routes import DealIn

    deal = _sample()["deal"]
    DealIn.model_validate({
        "product": deal["product"],
        "quantity": deal["quantity"],
        "target_price": deal["target"],
        "ceiling_price": deal["ceiling"],
        "suppliers": [{"name": name, "address": address} for name, address in deal["suppliers"]],
    })


def test_sample_message_sent_unchanged_is_stored_as_simulated(desk_env):
    from fastapi.testclient import TestClient
    from src.app.main import app

    data = _sample()
    deal = data["deal"]
    client = TestClient(app)
    headers = {"X-API-Key": KEY}
    opened = client.post("/api/v1/desk/deals", headers=headers, json={
        "product": deal["product"], "quantity": deal["quantity"],
        "target_price": deal["target"], "ceiling_price": deal["ceiling"],
        "suppliers": [{"name": n, "address": a, "channel": "email"} for n, a in deal["suppliers"]],
    })
    assert opened.status_code == 200, opened.text
    deal_id = opened.json()["deal"]["id"]
    supplier = opened.json()["suppliers"][0]["id"]
    # The body the page sends for an unchanged sample message.
    posted = client.post(f"/api/v1/desk/deals/{deal_id}/quotes", headers=headers, json={
        "source": "paste", "raw_text": data["message"], "supplier_id": supplier,
        "simulated": True, "page_title": "TradeFlow sample message (simulated)",
    })
    assert posted.status_code == 200, posted.text
    event = posted.json()["event"]
    assert event["provenance"]["origin"] == "simulated"
    assert event["provenance"]["simulated"] is True
    assert event["status"] == "review"
    assert event["parsed"]["unit_price"] is None


def test_desk_page_wires_the_samples_without_numbers_in_code():
    page = (ROOT / "src/web/templates/desk.html").read_text(encoding="utf-8")
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert 'id="sample-deal"' in page and 'id="sample-message"' in page
    assert '"/static/samples/desk_sample.json"' in script
    assert "payload.simulated = true;" in script
    assert _sample()["message"] not in script
