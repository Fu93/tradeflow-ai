"""Round-9 regressions: signed export history, and a bare primary beside a conversion."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.steps.test_operator_chain import _deal
from tests.steps.test_round7 import _quote_all, _truncate_from


def _north_draft(store, deal_id: str, by_name: dict[str, str]) -> dict:
    return next(
        item for item in store.drafts(deal_id)
        if item["supplier_id"] == by_name["North"] and item["status"] == "draft"
    )


def test_recorded_approval_survives_without_a_local_history_file(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import src.desk.audit_chain as chain
    from src.desk.service import DeskRejected, SendRefused, draft_digest, reanchor_deal, recover_anchor_history, send_draft

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
    monkeypatch.setenv("TRADEFLOW_API_KEY", "round9-key")
    try:
        store, snapshot = _deal(tmp_path, monkeypatch)
        deal_id = snapshot["deal"]["id"]
        by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
        _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
        draft = _north_draft(store, deal_id, by_name)
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
        sidecar = Path(str(tmp_path / "desk.db") + ".anchor-history.jsonl")
        sidecar.write_text('{"type":"send_approved","approvals":[]}\n', encoding="utf-8")
        with pytest.raises(DeskRejected, match="still verifies"):
            recover_anchor_history(store, deal_id, human_confirmed=True)
        with pytest.raises(SendRefused, match="already approved"):
            asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        kept = dict(_Anchor.heads[deal_id])
        unsigned = {
            "deal_id": deal_id,
            "seq": kept["seq"],
            "entry_hash": kept["entry_hash"],
            "type": kept["type"],
        }
        _Anchor.heads[deal_id] = unsigned
        with pytest.raises(SendRefused, match="history does not verify"):
            asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
        with pytest.raises(DeskRejected, match="history does not verify"):
            reanchor_deal(store, deal_id, human_confirmed=True)
        from fastapi.testclient import TestClient

        from src.app.main import app
        from src.desk.store import reset_stores

        store.close()
        reset_stores()
        client = TestClient(app)
        recovered = client.post(
            f"/api/v1/desk/deals/{deal_id}/anchor-history/recover",
            headers={"X-API-Key": "round9-key"},
            json={"human_confirmed": True},
        )
        assert recovered.status_code == 200
        from src.desk.store import get_store

        fresh = get_store()
        recovered_chain = fresh.verify_links(deal_id)["entries"]
        assert any(entry["type"] == "export_recovery" for entry in recovered_chain)
        assert _Anchor.heads[deal_id]["approvals"] == []
        assert _Anchor.heads[deal_id]["export_mac"]
        sent = asyncio.run(
            send_draft(fresh, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
        )
        assert sent["status"] == "approved"
        approvals = list(_Anchor.heads[deal_id]["approvals"])
        assert sum(1 for item in approvals if item.get("type") == "send_approved") == 1
        fresh.close()
    finally:
        server.shutdown()
        server.server_close()


def test_rewritten_file_anchor_does_not_open_a_retry(tmp_path, monkeypatch):
    import src.desk.audit_chain as chain
    from src.desk.service import DeskRejected, SendRefused, draft_digest, reanchor_deal, recover_anchor_history, send_draft

    anchor = tmp_path / "heads.jsonl"
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(anchor))
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    by_name = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    _quote_all(store, deal_id, by_name, {"North": "15.60", "South": "14.00", "East": "16.00", "West": "17.00"})
    draft = _north_draft(store, deal_id, by_name)
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
    lines = anchor.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    last["export_mac"] = "ab" * 32
    last["approvals"] = []
    lines[-1] = json.dumps(last, sort_keys=True)
    anchor.write_text("\n".join(lines) + "\n", encoding="utf-8")
    index_path = Path(str(anchor) + ".index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["_end"] = anchor.stat().st_size
    slot = index[deal_id]
    slot["offset"] = anchor.read_bytes().rfind(lines[-1].encode("utf-8"))
    slot["heads"] = [{"seq": last["seq"], "entry_hash": last["entry_hash"], "type": last["type"]}]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(SendRefused, match="history does not verify"):
        asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    note = store.append_chain(deal_id, "note", {"text": "still pending"})
    assert note["anchor_ok"] is False
    with pytest.raises(DeskRejected, match="history does not verify"):
        reanchor_deal(store, deal_id, human_confirmed=True)
    recover_anchor_history(store, deal_id, human_confirmed=True)
    sent = asyncio.run(send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"])))
    assert sent["status"] == "approved"
    store.close()


def test_recovery_is_refused_until_the_history_itself_is_unusable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.app.main import app
    from src.desk.service import DeskRejected, recover_anchor_history
    from src.desk.store import reset_stores

    monkeypatch.setenv("TRADEFLOW_API_KEY", "round9-key")
    store, snapshot = _deal(tmp_path, monkeypatch)
    deal_id = snapshot["deal"]["id"]
    with pytest.raises(DeskRejected, match="person has to recover"):
        recover_anchor_history(store, deal_id, human_confirmed=False)
    monkeypatch.delenv("TRADEFLOW_CHAIN_KEY")
    with pytest.raises(DeskRejected, match="TRADEFLOW_CHAIN_KEY"):
        recover_anchor_history(store, deal_id, human_confirmed=True)
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "operator-chain-key")
    with pytest.raises(KeyError):
        recover_anchor_history(store, "0" * 32, human_confirmed=True)
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", "http://127.0.0.1:9/anchor")
    with pytest.raises(DeskRejected, match="anchor store was down"):
        recover_anchor_history(store, deal_id, human_confirmed=True)
    monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "fresh.jsonl"))
    import src.desk.audit_chain as chain

    real_export = chain._export_anchor

    def _down(*_args, **_kwargs):
        raise OSError("anchor down")

    monkeypatch.setattr(chain, "_export_anchor", _down)
    with pytest.raises(DeskRejected, match="did not record this recovery"):
        recover_anchor_history(store, deal_id, human_confirmed=True)
    monkeypatch.setattr(chain, "_export_anchor", real_export)
    store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
    store.conn.execute(
        "UPDATE desk_chain SET entry_hash = ? WHERE deal_id = ? AND seq = 1",
        ("0" * 64, deal_id),
    )
    store.conn.commit()
    with pytest.raises(DeskRejected, match="does not verify"):
        recover_anchor_history(store, deal_id, human_confirmed=True)
    store.close()
    reset_stores()
    client = TestClient(app)
    headers = {"X-API-Key": "round9-key"}
    missing = client.post(
        "/api/v1/desk/deals/" + ("0" * 32) + "/anchor-history/recover",
        headers=headers,
        json={"human_confirmed": True},
    )
    assert missing.status_code == 404
    refused = client.post(
        f"/api/v1/desk/deals/{deal_id}/anchor-history/recover",
        headers=headers,
        json={"human_confirmed": False},
    )
    assert refused.status_code == 422


def test_bare_primary_beside_a_conversion_stays_unconfirmed():
    from src.desk.parse import parse_quote, price_from_token

    usd = "unit price 33 (about USD 4.60)"
    reading = parse_quote(usd, deal_currency="USD")
    assert reading["status"] != "accepted"
    assert reading["parsed"]["unit_price"] is None
    assert reading["provenance"]["candidates"] == ["33"]
    assert price_from_token(usd, "4.60", deal_currency="USD") is None
    assert price_from_token(usd, "4.60", deal_currency="CNY") is None
    yuan = "单价33（约4.6元）"
    yuan_reading = parse_quote(yuan, deal_currency="CNY")
    assert yuan_reading["status"] != "accepted"
    assert yuan_reading["parsed"]["unit_price"] is None
    assert yuan_reading["provenance"]["candidates"] == ["33"]
    assert price_from_token(yuan, "4.6", deal_currency="CNY") is None
    assert price_from_token(yuan, "33", deal_currency="CNY") == pytest.approx(33)
    labelled = "Unit price 33元（约4.6美元）."
    kept = parse_quote(labelled)
    assert kept["status"] == "accepted"
    assert kept["parsed"]["unit_price"] == pytest.approx(33)
    assert price_from_token(labelled, "4.6", deal_currency="CNY") is None
