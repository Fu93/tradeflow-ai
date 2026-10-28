"""Fixes for problems a real buyer hit: chat-only suppliers, the deal currency, plain review reasons."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu-laowang", "channel": "wechat"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    for key in ("DESK_SMTP_SEND", "TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    yield store
    store.close()


def test_a_wechat_supplier_is_approved_and_never_mailed(desk, monkeypatch):
    from src.desk.service import accept_review, create_deal, ingest_message, send_draft
    from src.desk.verify import draft_digest

    snapshot = create_deal(
        desk, product="umbrella", quantity=1000, target_price=12, ceiling_price=20,
        risk_tolerance=0.5, seed=11, suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    ids = {row["name"]: row for row in snapshot["suppliers"]}
    assert ids["Yiwu"]["channel"] == "wechat" and ids["Yiwu"]["address"] == "yiwu-laowang"
    yiwu = ids["Yiwu"]["id"]
    event = ingest_message(desk, deal_id, source="paste", raw_text="单价 15.60 元", supplier_id=yiwu)["event"]
    accept_review(desk, deal_id, event["id"], yiwu, chosen_token="15.60")
    draft = next(item for item in desk.drafts(deal_id) if item["supplier_id"] == yiwu and item["status"] == "draft")

    # Mail sending is on, and still nothing is mailed to a WeChat contact.
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_USERNAME", "buyer@example.test")
    monkeypatch.setenv("EMAIL_PASSWORD", "desk-secret")
    calls = []

    async def spy(*args, **kwargs):
        calls.append(args)
        return True

    approved = asyncio.run(
        send_draft(desk, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]), sender=spy)
    )
    assert calls == []
    assert approved["status"] == "approved"
    assert approved["transmitted"] == 0
    assert desk.verify_chain(deal_id)["ok"]


@pytest.mark.parametrize(
    ("channel", "address", "ok"),
    [
        ("email", "sales@example.test", True),
        ("wechat", "yiwu-laowang", True),
        ("WeChat", "老王", True),
        ("whatsapp", "+86 138 0000 0000", True),
        ("email", "yiwu-laowang", False),  # an email supplier needs an email
        ("fax", "0571-1234", False),  # unknown channel
        ("wechat", "a<b>", False),
        ("wechat", "line one\nline two", False),
        ("wechat", "   ", False),
    ],
)
def test_supplier_contact_rules(channel, address, ok):
    from src.desk.routes import SupplierIn

    if ok:
        made = SupplierIn(name="Yiwu", address=address, channel=channel)
        assert made.channel == channel.lower()
    else:
        with pytest.raises((ValidationError, Exception)):
            SupplierIn(name="Yiwu", address=address, channel=channel)


def test_desk_form_reads_chat_contacts_and_sends_the_currency():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    page = (ROOT / "src/web/templates/desk.html").read_text(encoding="utf-8")
    assert "(wechat|weixin|微信|whatsapp|chat)" in script
    assert "copy the text and send it in " in script
    assert 'id="deal-currency"' in page
    for code in ("CNY", "USD", "EUR"):
        assert f'<option value="{code}"' in page
    assert 'currency: document.getElementById("deal-currency").value || "CNY"' in script
    # Money on the cards follows the deal's currency, not a fixed CNY.
    assert 'money(supplier.counter, "CNY")' not in script
    assert "money(supplier.counter, dealCurrency)" in script


def test_review_reasons_are_in_plain_words():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert "function plainReason(" in script
    assert "Colloquial 块 and bare numbers land here." in script
    assert "Pick the supplier's own offer." in script
    # The reader's own reason stays available for whoever wants it.
    assert 'reasonLine.title = "Reader\'s reason: " + reason;' in script
