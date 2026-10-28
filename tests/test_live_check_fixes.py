"""Gaps from the live check on 2026-10-28: the supplier count message and Chinese drafts on USD or EUR deals."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_three_suppliers_get_a_plain_message(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_API_KEY", "a-test-key-long-enough")
    from src.app.main import app

    body = {
        "product": "umbrella", "quantity": 1000, "target_price": 24, "ceiling_price": 30,
        "suppliers": [{"name": n, "address": f"{n}@example.test"} for n in ("a", "b", "c")],
    }
    response = TestClient(app).post(
        "/api/v1/desk/deals", json=body, headers={"X-API-Key": "a-test-key-long-enough"}
    )
    assert response.status_code == 422
    text = str(response.json())
    assert "A deal needs 4 or 5 suppliers, one per line; this has 3." in text
    assert "List should have at least" not in text


@pytest.mark.parametrize(("currency", "word"), [("USD", "美元"), ("EUR", "欧元"), ("CNY", "元")])
def test_a_chinese_supplier_gets_a_chinese_draft_in_any_deal_currency(currency, word):
    from src.desk.project import draft_body
    from src.desk.verify import _body_currency

    body = draft_body("折叠伞 / folding umbrella", 3.85, 1000, "zh", currency)
    assert body.startswith("您好，关于折叠伞")
    assert f"每件 3.85 {word}、" in body
    # The send check reads the currency back from the same words.
    assert _body_currency(body) == currency


def test_the_cny_chinese_draft_is_unchanged():
    from src.desk.project import draft_body

    assert draft_body("伞", 23.52, 1000, "zh", "CNY") == (
        "您好，关于伞，我们可以按每件 23.52 元、数量 1000 继续。请确认是否可以安排。"
    )
