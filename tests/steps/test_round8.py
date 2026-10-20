"""Round-8 regressions: anchored approvals, keep, pending, seek, rebate asides."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.steps.test_operator_chain import _deal
from tests.steps.test_round7 import _quote_all, _truncate_from


def test_recorded_approval_is_not_retried_after_later_rows_are_deleted(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import SendRefused, draft_digest, reanchor_deal, send_draft

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = next(
        item for item in store.drafts(deal_id)
        if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
    )
    real = chain._export_anchor
    state = {"n": 0}

    def _second_fails(*args, **kwargs):
        state["n"] += 1
        if state["n"] > 1:
            raise OSError("anchor down")
        return real(*args, **kwargs)

    monkeypatch.setattr(chain, "_export_anchor", _second_fails)
    asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert any(json.loads(line)["type"] == "send_approved" for line in anchor.read_text(encoding="utf-8").splitlines())
    _truncate_from(store, deal_id, "send_result")
    monkeypatch.setattr(chain, "_export_anchor", real)
    reanchor_deal(store, deal_id, human_confirmed=True)
    with pytest.raises(SendRefused, match="already approved"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    approvals = [
        json.loads(line) for line in anchor.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert sum(1 for item in approvals if item.get("type") == "send_approved") == 1
    store.close()


def test_recorded_approval_is_not_retried_on_a_url_anchor(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import src.desk.audit_chain as chain
    from src.desk.service import SendRefused, draft_digest, reanchor_deal, send_draft

    class _Anchor(BaseHTTPRequestHandler):
        """Keeps only the latest head, the way a replace-in-place anchor does."""

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
        real = chain._export_anchor
        state = {"n": 0}

        def _second_fails(*args, **kwargs):
            state["n"] += 1
            if state["n"] > 1:
                raise OSError("anchor down")
            return real(*args, **kwargs)

        monkeypatch.setattr(chain, "_export_anchor", _second_fails)
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        _truncate_from(store, deal_id, "send_result")
        monkeypatch.setattr(chain, "_export_anchor", real)
        reanchor_deal(store, deal_id, human_confirmed=True)
        with pytest.raises(SendRefused, match="already approved"):
            asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        assert _Anchor.heads[deal_id]["type"] != "send_approved"
        approvals = list(_Anchor.heads[deal_id]["approvals"])
        assert sum(1 for item in approvals if item.get("type") == "send_approved") == 1
        assert not Path(str(tmp_path / "desk.db") + ".anchor-history.jsonl").exists()
        store.close()
    finally:
        server.shutdown()
        server.server_close()


def test_keep_uses_the_human_address_and_only_a_flagged_supplier(tmp_path, monkeypatch):
    from src.desk.service import DeskRejected, confirm_keep_supplier

    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    original = snapshot["suppliers"][0]
    with pytest.raises(DeskRejected, match="migrated deal"):
        confirm_keep_supplier(
            store, deal_id, original["id"], address="human@example.test", human_confirmed=True,
        )
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_delete")
    store.conn.execute("DELETE FROM desk_chain WHERE deal_id = ?", (deal_id,))
    store.conn.commit()
    store.migrate_deal(deal_id)
    with pytest.raises(DeskRejected, match="not waiting"):
        confirm_keep_supplier(
            store, deal_id, original["id"], address="human@example.test", human_confirmed=True,
        )
    late_id = uuid.uuid4().hex
    store.conn.execute(
        """
        INSERT INTO suppliers (
            id, deal_id, name, address, channel, belief_mean, belief_std, hazard_events, hazard_walks
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 0, 0)
        """,
        (late_id, deal_id, "Late", "db-row@example.test", "email"),
    )
    store.conn.commit()
    kept = confirm_keep_supplier(
        store, deal_id, late_id, address="human@example.test", human_confirmed=True,
    )
    event = next(entry for entry in store.verify_links(deal_id)["entries"] if entry["type"] == "supplier_kept")
    assert event["payload"]["address"] == "human@example.test"
    assert event["payload"]["address"] != "db-row@example.test"
    assert event["payload"]["confirmed_by"] == "human"
    late = next(row for row in kept["suppliers"] if row["id"] == late_id)
    assert late["needs_keep_confirmation"] is False
    with pytest.raises(DeskRejected, match="not waiting"):
        confirm_keep_supplier(
            store, deal_id, late_id, address="human@example.test", human_confirmed=True,
        )
    script = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "desk.js"
    source = script.read_text(encoding="utf-8")
    assert "/keep" in source
    assert "address: keptAddress.value" in source
    store.close()


def test_reanchor_required_survives_another_saved_event(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import draft_digest, reanchor_deal, send_draft, set_assumptions

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = next(
        item for item in store.drafts(deal_id)
        if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
    )
    real = chain._export_anchor

    def _down(*_args, **_kwargs):
        raise OSError("anchor down")

    monkeypatch.setattr(chain, "_export_anchor", _down)
    with pytest.raises(Exception, match="anchor did not record"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    monkeypatch.setattr(chain, "_export_anchor", real)
    saved = set_assumptions(store, deal_id, {"include_unverified": True})
    assert saved["anchor_pending"] is True
    pending = next(item for item in saved["drafts"] if item["id"] == draft["id"])
    assert pending["anchor_pending"] is True
    script = Path(__file__).resolve().parents[2] / "src" / "web" / "static" / "js" / "desk.js"
    assert "snapshot.anchor_pending" in script.read_text(encoding="utf-8")
    reanchor_deal(store, deal_id, human_confirmed=True)
    cleared = store.get_snapshot(deal_id)
    assert cleared["anchor_pending"] is False
    sent = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert sent["status"] == "approved"
    store.close()


def test_file_anchor_verify_does_not_scan_a_long_log(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import draft_digest, send_draft

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    padding = b'{"deal_id":"cccccccccccccccccccccccccccccccc","seq":1,"entry_hash":"aa","type":"note"}\n'
    with anchor.open("ab") as handle:
        handle.write(padding * 300_000)
    index_path = Path(str(anchor) + ".index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["_end"] = anchor.stat().st_size
    index_path.write_text(json.dumps(index), encoding="utf-8")

    def _scanned(*_args, **_kwargs):
        raise AssertionError("anchor log was scanned")

    monkeypatch.setattr(chain, "_heads_from_log", _scanned)
    monkeypatch.setattr(chain, "_scan_anchor_log", _scanned)
    started = time.perf_counter()
    checked = store.verify_chain(deal_id)
    elapsed = time.perf_counter() - started
    assert checked["ok"] is True
    assert elapsed < 1.0
    draft = next(
        item for item in store.drafts(deal_id)
        if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
    )
    started = time.perf_counter()
    sent = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert sent["status"] == "approved"
    assert time.perf_counter() - started < 2.0
    store.close()


def test_same_currency_parenthetical_stays_unconfirmed():
    from src.desk.parse import parse_quote, price_from_token

    rebate = "Unit price 33元（约28元）."
    reading = parse_quote(rebate)
    assert reading["status"] != "accepted"
    assert reading["parsed"]["unit_price"] is None
    assert price_from_token(rebate, "33", deal_currency="CNY") is None
    assert price_from_token(rebate, "28", deal_currency="CNY") is None
    english = "unit price 15.60 CNY (rebate about 2.00 CNY)"
    assert parse_quote(english)["status"] != "accepted"
    assert price_from_token(english, "15.60", deal_currency="CNY") is None
    converted = "Unit price 33元（约4.6美元）."
    kept = parse_quote(converted)
    assert kept["status"] == "accepted"
    assert kept["parsed"]["unit_price"] == pytest.approx(33)


def test_parser_warmup_keeps_the_deadline_fail_closed(monkeypatch):
    import regex

    import src.engine.safe_regex as safe

    calls: list[float | None] = []
    real = regex.compile

    class _Proxy:
        def __init__(self, inner):
            self._inner = inner

        def search(self, text, *args, **kwargs):
            timeout = kwargs.get("timeout")
            calls.append(timeout)
            if timeout is not None:
                raise TimeoutError("deadline")
            return self._inner.search(text, *args, **kwargs)

        def findall(self, text, *args, **kwargs):
            return self._inner.findall(text, *args, **kwargs)

        def finditer(self, text, *args, **kwargs):
            return self._inner.finditer(text, *args, **kwargs)

    def _compiling(pattern, flags=0):
        return _Proxy(real(pattern, flags))

    monkeypatch.setattr(safe, "HAS_RE2", False)
    monkeypatch.setattr(regex, "compile", _compiling)
    pattern = safe.SafeLinearPattern(r"price\s+(\d+)", 0, timeout_sec=0.01)
    assert pattern.backend == "regex"
    assert None in calls
    assert pattern.search("price 12") is None
    code = "from src.desk.parse import parse_quote\nreading = parse_quote('unit price 15.60 CNY per piece')\nassert reading['status'] == 'accepted'\nassert reading['parsed']['unit_price'] == 15.6\n"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_smtp_stays_off_unless_asked(monkeypatch):
    monkeypatch.delenv("DESK_SMTP_SEND", raising=False)
    from src.desk.service import smtp_enabled

    assert smtp_enabled() is False
