"""P0 verification spine: chain, decision record, currency, extractor, re-accept."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest


def _deal(store, currency="CNY"):
    from src.desk.service import create_deal

    return create_deal(
        store,
        product="Widget",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        currency=currency,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )


def test_chain_detects_tampering_and_blocks_update(tmp_path):
    from src.desk.audit_chain import GENESIS
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    deal = _deal(store)
    deal_id = deal["deal"]["id"]
    store.append_chain(deal_id, "quote_ingested", {"raw_sha256": "abc"})
    verified = store.verify_chain(deal_id)
    assert verified["ok"] is True
    assert verified["entries"][0]["prev_hash"] == GENESIS
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE desk_chain SET ts = 'x' WHERE deal_id = ?", (deal_id,))
    store.conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM desk_chain WHERE deal_id = ?", (deal_id,))
    store.conn.rollback()
    store.conn.execute("DROP TRIGGER desk_chain_no_update")
    store.conn.execute(
        "UPDATE desk_chain SET payload_json = ? WHERE deal_id = ?",
        (json.dumps({"forged": True}), deal_id),
    )
    store.conn.commit()
    broken = store.verify_chain(deal_id)
    assert broken["ok"] is False
    assert broken["broken_seq"] == 1
    store.close()


def test_usd_deal_rejects_a_cny_token_and_stores_currency(tmp_path):
    from src.desk.parse import parse_quote, price_from_token
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    deal = _deal(store, currency="USD")
    assert deal["deal"]["currency"] == "USD"
    reading = parse_quote("单价 14.80 元", deal_currency="USD")
    assert reading["parsed"]["unit_price"] is None
    assert price_from_token("单价 14.80 元", "14.80", deal_currency="USD") is None
    usd = parse_quote("unit price 14.80 USD per piece", deal_currency="USD")
    assert usd["parsed"]["unit_price"] == pytest.approx(14.80)
    store.close()


def test_reaccept_is_blocked_and_unconfirmed_withholds_the_counter(tmp_path):
    from src.desk.service import DeskRejected, accept_review, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = _deal(store)
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingested = ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    assert ingested["event"]["raw_sha256"] == hashlib.sha256("单价 14.80 元".encode()).hexdigest()
    accepted = accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    card = next(row for row in accepted["suppliers"] if row["id"] == supplier)
    assert card["counter"] is not None
    assert card.get("decision_id")
    chain = store.verify_chain(deal_id)
    assert chain["ok"] is True
    kinds = [item["type"] for item in chain["entries"]]
    assert "quote_ingested" in kinds
    assert "decision" in kinds
    assert "draft_created" in kinds
    with pytest.raises(DeskRejected):
        accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    forged = dict(ingested["event"])
    forged["status"] = "accepted"
    forged["provenance"] = {"unconfirmed": True, "price_source": "raw_text"}
    forged["parsed"] = {"unit_price": 1.0}
    from src.desk.headline import confirmed_prices

    _kept, count = confirmed_prices([forged])
    assert count == 1
    store.close()


def test_send_guard_refuses_a_broken_chain(tmp_path):
    import asyncio

    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = _deal(store)
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingested = ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    store.conn.execute("DROP TRIGGER desk_chain_no_update")
    store.conn.execute(
        "UPDATE desk_chain SET payload_json = ? WHERE deal_id = ? AND type = 'decision'",
        (json.dumps({"forged": True}), deal_id),
    )
    store.conn.commit()
    with pytest.raises(SendRefused):
        asyncio.run(
            send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
    store.close()


def test_extractor_drops_off_schema_rows_and_oversized_bodies():
    from src.desk.extract import mentions_from_payload
    from src.desk.extract import _from_chat

    hostile = mentions_from_payload(
        {
            "refund_amount": 10,
            "mentions": [
                {"class": "not-a-class", "evidence_span": "1", "price": {"nested": {"amount": 3}}},
                {
                    "class": "own_unit_offer",
                    "evidence_span": "15.60",
                    "speaker": "supplier",
                    "polarity": "affirmative",
                    "currency": "CNY",
                    "unit": "per_piece",
                    "is_question": False,
                },
            ],
        }
    )
    assert hostile["rejected_rows"] >= 1
    assert hostile["mentions"][0]["class"] == "own_unit_offer"
    assert "price" not in hostile["mentions"][0]
    huge = "x" * 70000
    parsed = _from_chat({"choices": [{"message": {"content": huge}}]})
    assert parsed["error"] == "extractor_too_large"
    assert parsed["mentions"] == []
