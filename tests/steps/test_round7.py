"""Round-7 regressions: truncation, one quoting set, anchor retry, stale constants."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import uuid
from pathlib import Path

import pytest

from tests.steps.test_operator_chain import _deal


def _quote_all(store, deal_id, by_name, prices):
    from src.desk.service import accept_review, ingest_message

    for name, price in prices.items():
        ingested = ingest_message(
            store, deal_id, source="paste", supplier_id=by_name[name],
            raw_text=f"unit price {price} CNY per piece",
        )
        accept_review(store, deal_id, ingested["event"]["id"], by_name[name], chosen_token=price)


def _truncate_from(store, deal_id: str, type_name: str) -> None:
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    row = store.conn.execute(
        "SELECT MIN(seq) AS seq FROM desk_chain WHERE deal_id = ? AND type = ?",
        (deal_id, type_name),
    ).fetchone()
    assert row is not None and row["seq"] is not None
    store.conn.execute(
        "DELETE FROM desk_chain WHERE deal_id = ? AND seq >= ?",
        (deal_id, int(row["seq"])),
    )
    store.conn.commit()


def test_reanchor_refuses_a_truncated_send_on_a_file_anchor(tmp_path, monkeypatch):
    from src.desk.service import DeskRejected, SendRefused, draft_digest, reanchor_deal, send_draft

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == by_name["North"] and item["status"] == "draft")
    asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    before = anchor.read_bytes()
    _truncate_from(store, deal_id, "send_approved")
    checked = store.verify_chain(deal_id)
    assert checked["ok"] is False
    assert checked["anchor"] == "anchor missing because the chain was cut"
    with pytest.raises(DeskRejected, match="chain was cut"):
        reanchor_deal(store, deal_id, human_confirmed=True)
    assert anchor.read_bytes() == before
    with pytest.raises(SendRefused, match="audit chain broken"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    store.close()


def test_reanchor_refuses_a_truncated_send_on_a_url_anchor(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from src.desk.service import DeskRejected, SendRefused, draft_digest, reanchor_deal, send_draft

    class _Anchor(BaseHTTPRequestHandler):
        heads: dict[str, dict] = {}

        def do_POST(self):
            count = int(self.headers.get("Content-Length") or 0)
            item = json.loads(self.rfile.read(count))
            _Anchor.heads[item["deal_id"]] = item
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            from urllib.parse import parse_qs, urlparse

            deal = parse_qs(urlparse(self.path).query).get("deal_id", [""])[0]
            item = _Anchor.heads.get(deal)
            if item is None:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(item).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            return

    _Anchor.heads = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Anchor)
    port = server.server_address[1]
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", f"http://127.0.0.1:{port}/anchor")
    try:
        store, snapshot = _deal(tmp_path, monkeypatch)
        deal_id = snapshot["deal"]["id"]
        by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
        _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
        draft = next(
            item for item in store.drafts(deal_id)
            if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
        )
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        remembered = dict(_Anchor.heads[deal_id])
        _truncate_from(store, deal_id, "send_approved")
        checked = store.verify_chain(deal_id)
        assert checked["anchor"] == "anchor missing because the chain was cut"
        with pytest.raises(DeskRejected, match="chain was cut"):
            reanchor_deal(store, deal_id, human_confirmed=True)
        assert _Anchor.heads[deal_id] == remembered
        with pytest.raises(SendRefused, match="audit chain broken"):
            asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        store.close()
    finally:
        server.shutdown()
        server.server_close()


def test_a_down_anchor_is_not_reported_as_a_cut(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import DeskRejected, reanchor_deal

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    count = store.verify_chain(deal_id)["count"]

    def _down(*_args, **_kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(chain, "_read_anchor_url", _down)
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", "http://anchor.invalid/heads")
    checked = store.verify_chain(deal_id)
    assert checked["ok"] is False
    assert checked["anchor"] == "anchor missing because the anchor store was down"
    with pytest.raises(DeskRejected, match="anchor store was down"):
        reanchor_deal(store, deal_id, human_confirmed=True)
    assert store.verify_links(deal_id)["count"] == count
    store.close()


def test_walk_away_reopen_requote_drafts_and_sends_all_four(tmp_path, monkeypatch):
    from src.desk.service import accept_review, draft_digest, ingest_message, record_outcome, reopen_supplier, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch, ceiling=20.0)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "14.00", "South": "16.00", "East": "17.00", "West": "18.00"})
    record_outcome(store, deal_id, by_name["South"], outcome="walk_away")
    reopen_supplier(store, deal_id, by_name["South"], human_confirmed=True)
    again = ingest_message(
        store, deal_id, source="paste", supplier_id=by_name["South"],
        raw_text="unit price 13.00 CNY per piece",
    )
    fresh = accept_review(store, deal_id, again["event"]["id"], by_name["South"], chosen_token="13.00")
    cards = {row["name"]: row for row in fresh["suppliers"]}
    assert set(cards) == {"North", "South", "East", "West"}
    assert all(card["counter"] is not None for card in cards.values())
    for name, card in cards.items():
        draft = next(
            item for item in store.drafts(deal_id)
            if item["supplier_id"] == card["id"] and item["status"] == "draft"
        )
        sent = asyncio.run(
            send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
        assert sent["status"] == "approved"
        stored = store.get_decision(str(draft["decision_id"]))
        assert float(stored["record"]["counter"]) == pytest.approx(float(card["counter"]))
    store.close()


def test_failed_anchor_send_can_be_retried_once_after_reanchor(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import SendRefused, draft_digest, reanchor_deal, send_draft

    script = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "desk.js"
    assert "anchor did not record this send; re-anchor, then approve this draft again" in script.read_text(encoding="utf-8")
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "heads.jsonl"))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == by_name["North"] and item["status"] == "draft")
    original = chain._export_anchor

    def _boom(*_args, **_kwargs):
        raise OSError("anchor down")

    monkeypatch.setattr(chain, "_export_anchor", _boom)
    with pytest.raises(SendRefused, match="anchor did not record"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert store.get_draft(draft["id"])["status"] == "draft"
    shown = store.get_snapshot(deal_id)
    pending = next(item for item in shown["drafts"] if item["id"] == draft["id"])
    assert pending["anchor_pending"] is True
    assert pending["status"] == "draft"
    monkeypatch.setattr(chain, "_export_anchor", original)
    reanchor_deal(store, deal_id, human_confirmed=True)
    cleared = store.get_snapshot(deal_id)
    again = next(item for item in cleared["drafts"] if item["id"] == draft["id"])
    assert again["anchor_pending"] is False
    assert again["status"] == "draft"
    sent = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert sent["status"] == "approved"
    with pytest.raises(SendRefused, match="already approved"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    store.close()


def test_changed_constants_regenerate_the_draft(tmp_path, monkeypatch):
    import src.desk.record as record
    from src.desk.service import DraftStale, draft_digest, send_draft

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == by_name["North"] and item["status"] == "draft")
    original = record.constants_snapshot

    def _moved():
        snap = dict(original())
        snap["registry_sha256"] = "ab" * 32
        return snap

    monkeypatch.setattr(record, "constants_snapshot", _moved)
    with pytest.raises(DraftStale, match="Policy constants changed"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert store.get_draft(draft["id"])["status"] == "superseded"
    fresh = next(
        item for item in store.drafts(deal_id)
        if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
    )
    assert fresh["id"] != draft["id"]
    stored = store.get_decision(str(fresh["decision_id"]))
    assert stored["record"]["constants"]["registry_sha256"] == "ab" * 32
    sent = asyncio.run(send_draft(store, fresh["id"], human_approved=True, body_sha256=draft_digest(fresh["body"])))
    assert sent["status"] == "approved"
    store.close()


def test_parenthetical_conversion_keeps_the_primary_currency():
    from src.desk.parse import parse_quote, price_from_token

    text = "Unit price 33元（约4.6美元）."
    reading = parse_quote(text)
    assert reading["status"] != "accepted"
    assert reading["parsed"]["unit_price"] is None
    assert reading["provenance"]["review_reason"] == "unit mismatch"
    assert "33" in reading["provenance"]["candidates"]
    assert price_from_token(text, "33", deal_currency="CNY") is None
    assert price_from_token(text, "4.6", deal_currency="CNY") is None
    english = "unit price 15.60 CNY per piece (about USD 2.20)"
    english_reading = parse_quote(english)
    assert english_reading["status"] == "accepted"
    assert english_reading["parsed"]["unit_price"] == pytest.approx(15.60)
    assert price_from_token(english, "2.20", deal_currency="CNY") is None
    ambiguous = "31.00 dollars or yuan per piece"
    assert parse_quote(ambiguous, deal_currency="CNY")["status"] != "accepted"
    assert price_from_token(ambiguous, "31.00", deal_currency="CNY") is None
    assert price_from_token(ambiguous, "31.00", deal_currency="USD") is None


def test_policy_and_post_migration_suppliers_need_a_human(tmp_path, monkeypatch):
    from src.desk.service import (
        DeskRejected,
        accept_review,
        confirm_keep_supplier,
        confirm_policy,
        confirm_supplier_address,
        draft_digest,
        ingest_message,
        send_draft,
    )

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    limits = store.get_deal(deal_id)
    with pytest.raises(DeskRejected, match="migrated deal"):
        confirm_policy(
            store, deal_id, human_confirmed=True, product=limits["product"], quantity=limits["quantity"],
            target_price=limits["target_price"], ceiling_price=limits["ceiling_price"],
            risk_tolerance=limits["risk_tolerance"], currency=limits["currency"],
        )
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    store.conn.execute("DELETE FROM desk_chain WHERE deal_id = ?", (deal_id,))
    store.conn.commit()
    store.migrate_deal(deal_id)
    confirm_policy(
        store, deal_id, human_confirmed=True, product=limits["product"], quantity=limits["quantity"],
        target_price=limits["target_price"], ceiling_price=limits["ceiling_price"],
        risk_tolerance=limits["risk_tolerance"], currency=limits["currency"],
    )
    policy = next(entry for entry in store.verify_links(deal_id)["entries"] if entry["type"] == "policy_confirmed")
    assert policy["payload"]["previous"]["ceiling"] == pytest.approx(limits["ceiling_price"])
    late_id = uuid.uuid4().hex
    store.conn.execute(
        """
        INSERT INTO suppliers (
            id, deal_id, name, address, channel, belief_mean, belief_std, hazard_events, hazard_walks
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, 0)
        """,
        (late_id, deal_id, "Late", "late@example.test", "email"),
    )
    store.conn.commit()
    for row in store.suppliers(deal_id):
        if row["id"] == late_id:
            continue
        confirm_supplier_address(store, deal_id, row["id"], address=row["address"], human_confirmed=True)
    by_name = {row["name"]: row["id"] for row in store.suppliers(deal_id)}
    _quote_all(
        store, deal_id, {name: by_name[name] for name in ("North", "South", "East", "West")},
        {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"},
    )
    ingested = ingest_message(
        store, deal_id, source="paste", supplier_id=late_id, raw_text="unit price 12.00 CNY per piece",
    )
    shown = accept_review(store, deal_id, ingested["event"]["id"], late_id, chosen_token="12.00")
    late = next(row for row in shown["suppliers"] if row["id"] == late_id)
    assert late["needs_keep_confirmation"] is True
    assert late["counter"] is None
    assert "not on the chain before migration" in (late["reserve_note"] or "")
    assert not any(item["supplier_id"] == late_id and item["status"] == "draft" for item in store.drafts(deal_id))
    confirm_supplier_address(store, deal_id, late_id, address="late@example.test", human_confirmed=True)
    still = next(row for row in store.get_snapshot(deal_id)["suppliers"] if row["id"] == late_id)
    assert still["needs_keep_confirmation"] is True
    script = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "desk.js"
    assert "Confirm to keep them" in script.read_text(encoding="utf-8")
    kept = confirm_keep_supplier(
        store, deal_id, late_id, address="late@example.test", human_confirmed=True,
    )
    late = next(row for row in kept["suppliers"] if row["id"] == late_id)
    assert late["needs_keep_confirmation"] is False
    assert late["counter"] is not None
    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == late_id and item["status"] == "draft")
    sent = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert sent["status"] == "approved"
    store.close()
