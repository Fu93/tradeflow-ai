"""Supplier waiting/approval states stay independent of another thread's speed."""
import asyncio
from contextlib import closing

import pytest

from src.desk import service
from src.desk.store import DeskStore
from src.desk.verify import draft_digest


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "thread-test-chain")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", "")
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "0")
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "0")
    monkeypatch.delenv("DESK_SMTP_SEND", raising=False)
    with closing(DeskStore(str(tmp_path / "desk.db"))) as store:
        current = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=24, ceiling_price=40,
            risk_tolerance=.5, seed=11,
            suppliers=[{"name": name, "address": name.lower() + "@example.test"} for name in "ABCD"],
        )
        yield store, current["deal"]["id"], {row["name"]: row["id"] for row in current["suppliers"]}


def confirm(desk, name, price):
    store, deal, ids = desk
    event = service.ingest_message(
        store, deal, source="paste", raw_text=f"单价 {price:.2f} 元。", supplier_id=ids[name],
    )["event"]
    return service.accept_review(store, deal, event["id"], ids[name], chosen_token=f"{price:.2f}")


@pytest.mark.parametrize("receipt", [False, True])
def test_approval_is_not_delivery_and_slow_supplier_does_not_block_another(desk, monkeypatch, receipt):
    store, deal, ids = desk
    current = confirm(desk, "A", 28.5)
    card = next(row for row in current["suppliers"] if row["id"] == ids["A"])
    assert card["conversation"]["state"] == "needs_approval"
    draft = next(row for row in current["drafts"] if row["supplier_id"] == ids["A"])
    monkeypatch.setattr(service, "smtp_enabled", lambda: receipt)
    calls = []

    async def fake_transport(*args, **kwargs):
        calls.append(1)
        return True

    result = asyncio.run(service.send_draft(
        store, draft["id"], human_approved=True,
        body_sha256=draft_digest(draft["body"]), sender=fake_transport,
    ))
    assert result["transmitted"] == receipt
    event = service.ingest_message(
        store, deal, source="paste", raw_text="单价 27.00 元。", supplier_id=ids["B"],
    )["event"]
    current = store.get_snapshot(deal)
    threads = {row["id"]: row["conversation"] for row in current["suppliers"]}
    assert threads[ids["B"]]["state"] == "needs_review"
    assert threads[ids["A"]]["state"] == ("waiting_supplier" if receipt else "approved_not_sent")
    assert bool(threads[ids["A"]]["waiting_since"]) == receipt
    assert len(calls) == int(receipt)
    assert store.outcomes(deal) == []
    service.accept_review(store, deal, event["id"], ids["B"], chosen_token="27.00")
    assert store.outcomes(deal) == []
    assert all(not row["walked_away"] for row in store.get_snapshot(deal)["suppliers"])


def test_busy_supplier_history_does_not_hide_other_suppliers_current_drafts(desk):
    store, deal, ids = desk
    for name, price in zip("ABCD", (30, 25, 28, 27), strict=True):
        confirm(desk, name, price)
    for index in range(24):
        current = service.set_offer_mode(
            store, deal, ids["A"], "push" if index % 2 == 0 else "default", human_confirmed=True,
        )
    latest = {}
    for draft in store.drafts(deal):
        if draft["status"] != "superseded" and draft["supplier_id"] not in latest:
            latest[draft["supplier_id"]] = draft["id"]
    assert len(store.drafts(deal)) > 20
    assert len(latest) == 4
    assert set(latest.values()) <= {draft["id"] for draft in current["drafts"]}
