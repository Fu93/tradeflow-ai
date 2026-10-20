"""Operator migration, anchor failure, and the shared counter.

Restarting the desk does not re-sign a chain. A send whose anchor write
failed is not repeated by deleting the unanchored row. The draft and the
send guard share one reserve-aware decision.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _deal(tmp_path: Path, monkeypatch, *, ceiling: float = 20.0):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "operator-chain-key")
    from src.desk.service import create_deal
    from src.desk.store import DeskStore, reset_stores

    reset_stores()
    store = DeskStore(tmp_path / "desk.db")
    snapshot = create_deal(
        store,
        product="valve",
        quantity=100,
        target_price=4.0,
        ceiling_price=ceiling,
        risk_tolerance=0.5,
        seed=1,
        currency="CNY",
        suppliers=[
            {"name": "North", "address": "north@example.test"},
            {"name": "South", "address": "south@example.test"},
            {"name": "East", "address": "east@example.test"},
            {"name": "West", "address": "west@example.test"},
        ],
    )
    return store, snapshot


def test_restart_does_not_migrate_and_anchor_head_is_kept(tmp_path, monkeypatch):
    from src.desk.store import DeskRejected, DeskStore

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    path = store.path
    store.close()
    reopened = DeskStore(path)
    chain = reopened.verify_chain(deal_id)
    assert chain["ok"] is True
    assert all(not (entry.get("payload") or {}).get("migrated") for entry in chain["entries"])
    with pytest.raises(DeskRejected, match="keyed chain"):
        reopened.migrate_deal(deal_id)
    reopened.close()

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    anchored, anchored_snapshot = _deal(tmp_path / "anchored", monkeypatch)
    before = anchor.read_bytes()
    with pytest.raises(DeskRejected, match="anchor head"):
        anchored.migrate_deal(anchored_snapshot["deal"]["id"])
    assert anchor.read_bytes() == before
    anchored.close()


def test_failed_anchor_blocks_send_even_after_the_row_is_deleted(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import SendRefused, accept_review, draft_digest, ingest_message, send_draft

    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "heads.jsonl"))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    calls: list[str] = []

    async def _grab(recipient, body, product, **kwargs):
        calls.append(recipient)
        return True

    for name, price in (("North", "15.60"), ("South", "14.00"), ("East", "16.00"), ("West", "17.00")):
        ingested = ingest_message(
            store, deal_id, source="paste", supplier_id=by_name[name],
            raw_text=f"unit price {price} CNY per piece",
        )
        accept_review(store, deal_id, ingested["event"]["id"], by_name[name], chosen_token=price)

    def _boom(*_args, **_kwargs):
        raise OSError("anchor down")

    monkeypatch.setattr(chain, "_export_anchor", _boom)
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == by_name["North"] and item["status"] == "draft")
    with pytest.raises(SendRefused, match="anchor did not record"):
        asyncio.run(
            send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]), sender=_grab)
        )
    assert calls == []
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    store.conn.execute(
        "DELETE FROM desk_chain WHERE deal_id = ? AND type = 'send_approved'",
        (deal_id,),
    )
    store.conn.commit()
    with pytest.raises(SendRefused, match="audit chain broken"):
        asyncio.run(
            send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]), sender=_grab)
        )
    assert calls == []
    pending = store.conn.execute(
        "SELECT seq FROM desk_anchor_pending WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()
    assert pending is not None
    store.close()


def test_walk_away_reopen_requote_sends_the_shared_counter(tmp_path, monkeypatch):
    from src.desk.service import accept_review, draft_digest, ingest_message, record_outcome, reopen_supplier, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    prices = {"North": "14.00", "South": "16.00", "East": "17.00", "West": "18.00"}
    for name, price in prices.items():
        ingested = ingest_message(
            store, deal_id, source="paste", supplier_id=by_name[name],
            raw_text=f"unit price {price} CNY per piece",
        )
        accept_review(store, deal_id, ingested["event"]["id"], by_name[name], chosen_token=price)
    record_outcome(store, deal_id, by_name["North"], outcome="walk_away", quote=14.0, offer=10.0)
    reopen_supplier(store, deal_id, by_name["North"], human_confirmed=True)
    again = ingest_message(
        store, deal_id, source="paste", supplier_id=by_name["North"],
        raw_text="unit price 13.00 CNY per piece",
    )
    fresh = accept_review(store, deal_id, again["event"]["id"], by_name["North"], chosen_token="13.00")
    cards = {row["id"]: row for row in fresh["suppliers"]}
    assert all(card["counter"] is not None for card in cards.values())
    for supplier_id, card in cards.items():
        draft = next(
            item for item in store.drafts(deal_id)
            if item["supplier_id"] == supplier_id and item["status"] == "draft"
        )
        sent = asyncio.run(
            send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
        assert sent["status"] == "approved"
        stored = store.get_decision(str(draft["decision_id"]))
        assert float(stored["record"]["counter"]) == pytest.approx(float(card["counter"]))
    store.close()


def test_address_and_product_are_rejected_at_input(tmp_path, monkeypatch):
    from src.desk.service import DeskRejected, confirm_supplier_address, create_deal
    from src.desk.store import DeskStore, reset_stores

    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "operator-chain-key")
    reset_stores()
    store = DeskStore(tmp_path / "desk.db")
    suppliers = [
        {"name": "North", "address": "north@example.test"},
        {"name": "South", "address": "south@example.test"},
        {"name": "East", "address": "east@example.test"},
        {"name": "West", "address": "west@example.test, bcc@evil.test"},
    ]
    with pytest.raises(DeskRejected, match="single email"):
        create_deal(
            store, product="valve", quantity=100, target_price=4.0, ceiling_price=20.0,
            risk_tolerance=0.5, seed=1, currency="CNY", suppliers=suppliers,
        )
    suppliers[-1]["address"] = "west@example.test"
    with pytest.raises(DeskRejected, match="control"):
        create_deal(
            store, product="valve\nBcc: x@evil.test", quantity=100, target_price=4.0,
            ceiling_price=20.0, risk_tolerance=0.5, seed=1, currency="CNY", suppliers=suppliers,
        )
    snapshot = create_deal(
        store, product="valve", quantity=100, target_price=4.0, ceiling_price=20.0,
        risk_tolerance=0.5, seed=1, currency="CNY", suppliers=suppliers,
    )
    supplier = snapshot["suppliers"][0]["id"]
    with pytest.raises(DeskRejected, match="single email"):
        confirm_supplier_address(
            store, snapshot["deal"]["id"], supplier,
            address="north@example.test\r\nBcc: x@evil.test", human_confirmed=True,
        )
    store.close()


def test_a_corrupt_anchor_index_fails_only_the_damaged_deal(tmp_path, monkeypatch):
    from src.desk.service import create_deal

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, first = _deal(tmp_path, monkeypatch)
    second = create_deal(
        store,
        product="gasket",
        quantity=100,
        target_price=4.0,
        ceiling_price=20.0,
        risk_tolerance=0.5,
        seed=1,
        currency="CNY",
        suppliers=[
            {"name": "Alpha", "address": "alpha@example.test"},
            {"name": "Beta", "address": "beta@example.test"},
            {"name": "Gamma", "address": "gamma@example.test"},
            {"name": "Delta", "address": "delta@example.test"},
        ],
    )
    first_id = first["deal"]["id"]
    second_id = second["deal"]["id"]
    index = Path(str(anchor) + ".index")
    index.write_text("{", encoding="utf-8")
    assert store.verify_chain(first_id)["ok"] is True
    assert store.verify_chain(second_id)["ok"] is True
    kept = [line for line in anchor.read_text(encoding="utf-8").splitlines() if second_id not in line]
    anchor.write_text("\n".join(kept) + "\n", encoding="utf-8")
    index.write_text("{", encoding="utf-8")
    assert store.verify_chain(first_id)["ok"] is True
    assert store.verify_chain(second_id)["ok"] is False
    store.close()


def test_round5_chain_verifies_and_is_not_migrated(tmp_path, monkeypatch):
    from src.desk.audit_chain import GENESIS, round5_digest
    from src.desk.store import DeskRejected

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    rows = store.conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    for row in rows:
        payload = json.loads(row["payload_json"])
        digest = round5_digest(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, prev)
        store.conn.execute(
            "UPDATE desk_chain SET prev_hash = ?, entry_hash = ?, key_id = NULL WHERE id = ?",
            (prev, digest, row["id"]),
        )
        prev = digest
    store.conn.commit()
    verified = store.verify_chain(deal_id)
    assert verified["ok"] is True
    assert verified["entries"][0]["key_id"] == ""
    with pytest.raises(DeskRejected, match="keyed chain"):
        store.migrate_deal(deal_id)
    store.close()


def test_migrated_snapshot_does_not_show_accepted_quotes(tmp_path, monkeypatch):
    from src.desk.audit_chain import GENESIS, legacy_digest
    from src.desk.service import accept_review, ingest_message, publish

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    ingested = ingest_message(
        store, deal_id, source="paste", supplier_id=supplier, raw_text="unit price 15.60 CNY per piece",
    )
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="15.60")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    rows = store.conn.execute(
        "SELECT * FROM desk_chain WHERE deal_id = ? ORDER BY seq ASC",
        (deal_id,),
    ).fetchall()
    prev = GENESIS
    for row in rows:
        payload = json.loads(row["payload_json"])
        digest = legacy_digest(row["deal_id"], int(row["seq"]), row["type"], row["ts"], payload, prev)
        store.conn.execute(
            "UPDATE desk_chain SET prev_hash = ?, entry_hash = ?, key_id = NULL WHERE id = ?",
            (prev, digest, row["id"]),
        )
        prev = digest
    store.conn.commit()
    store.migrate_deal(deal_id)
    fresh = publish(store, deal_id)
    assert all(event["status"] != "accepted" for event in fresh["events"])
    assert all(row["quote_confirmed"] is False for row in fresh["suppliers"])
    assert all(not row["accepted_event_ids"] for row in fresh["suppliers"])
    assert all(row["counter"] is None for row in fresh["suppliers"])
    store.close()


def test_dollars_resolve_to_usd_and_ambiguity_stays_unconfirmed():
    from src.desk.parse import parse_quote, price_from_token

    text = "31.00 dollars per piece"
    reading = parse_quote(text, deal_currency="CNY")
    assert reading["status"] != "accepted"
    assert reading["parsed"]["unit_price"] is None
    assert reading["parsed"]["currency"] == "USD"
    assert price_from_token(text, "31.00", deal_currency="CNY") is None
    assert price_from_token(text, "31.00", deal_currency="USD") == pytest.approx(31.0)
    ambiguous = "31.00 dollars or yuan per piece"
    assert parse_quote(ambiguous, deal_currency="CNY")["parsed"]["unit_price"] is None
    assert parse_quote(ambiguous, deal_currency="USD")["status"] != "accepted"
    assert price_from_token(ambiguous, "31.00", deal_currency="CNY") is None
    assert price_from_token(ambiguous, "31.00", deal_currency="USD") is None


def test_reanchor_records_the_head_after_an_outage(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import reanchor_deal

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    original = chain._export_anchor

    def _boom(*_args, **_kwargs):
        raise OSError("anchor down")

    monkeypatch.setattr(chain, "_export_anchor", _boom)
    store.append_chain(deal_id, "note", {"text": "during the outage"})
    assert store.verify_chain(deal_id)["ok"] is False
    monkeypatch.setattr(chain, "_export_anchor", original)
    fresh = reanchor_deal(store, deal_id, human_confirmed=True)
    assert any(entry["type"] == "reanchor" for entry in store.verify_chain(deal_id)["entries"])
    assert store.verify_chain(deal_id)["ok"] is True
    assert store.verify_chain(deal_id)["anchor"] == "anchored"
    assert fresh["deal"]["id"] == deal_id
    store.close()


def test_inbound_threading_headers_must_be_message_ids():
    from src.desk.mailbox import parse_rfc822
    from src.desk.transmission import reply_headers

    raw = (
        b"From: North <north@example.test>\r\n"
        b"Message-ID: not-an-id\r\n"
        b"References: <ok@example.test> <bad>\r\n"
        b"\r\nhello\r\n"
    )
    item = parse_rfc822(raw)
    assert item["message_id"] == ""
    assert item["references"] == "<ok@example.test>"
    good = (
        b"From: North <north@example.test>\r\n"
        b"Message-ID: <quote.1@example.test>\r\n"
        b"References: <thread@example.test>\r\n"
        b"\r\nhello\r\n"
    )
    parsed = parse_rfc822(good)
    assert parsed["message_id"] == "<quote.1@example.test>"
    assert parsed["references"] == "<thread@example.test>"
    reply_to, references = reply_headers(
        [
            {
                "type": "quote_ingested",
                "payload": {
                    "supplier_id": "north",
                    "message_id": "not-an-id\r\nBcc: x@evil.test",
                    "references": "<ok@example.test>, <bad>",
                },
            }
        ],
        "north",
    )
    assert reply_to == ""
    assert references == ""
