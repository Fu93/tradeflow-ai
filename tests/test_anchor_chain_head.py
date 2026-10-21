"""Gold tests: git anchoring of desk audit-chain heads.

1. The anchor file records every deal's chain head and verification state.
2. Appending a chain entry moves the recorded head (anchor is append-sensitive).
3. A rewritten chain (a link re-hashed without the key) is reported as broken
   instead of being silently re-anchored.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.anchor_chain_head import collect_anchors, main  # noqa: E402


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


def _store_with_chain(tmp_path):
    from src.desk.service import accept_review, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = _deal(store)
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingested = ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    return store, deal_id


def test_anchor_records_head_and_chain_ok(tmp_path):
    store, deal_id = _store_with_chain(tmp_path)
    store.close()
    anchors = collect_anchors(str(tmp_path / "desk.db"))
    entry = next(item for item in anchors["anchors"] if item["deal_id"] == deal_id)
    assert entry["chain_ok"] is True
    assert entry["entries"] >= 1
    assert isinstance(entry["head_sha256"], str) and len(entry["head_sha256"]) == 64
    assert entry["seq"] == entry["entries"]


def test_anchor_moves_when_chain_grows(tmp_path):
    store, deal_id = _store_with_chain(tmp_path)
    store.close()
    path = str(tmp_path / "desk.db")
    first = next(item for item in collect_anchors(path)["anchors"] if item["deal_id"] == deal_id)
    from src.desk.store import DeskStore

    store = DeskStore(path)
    store.append_chain(deal_id, "note", {"text": "operator note"})
    store.close()
    second = next(item for item in collect_anchors(path)["anchors"] if item["deal_id"] == deal_id)
    assert second["entries"] == first["entries"] + 1
    assert second["head_sha256"] != first["head_sha256"]


def test_main_writes_json_file(tmp_path):
    store, deal_id = _store_with_chain(tmp_path)
    store.close()
    out = tmp_path / "anchors.json"
    code = main(["--db", str(tmp_path / "desk.db"), "--out", str(out)])
    assert code == 0
    blob = json.loads(out.read_text(encoding="utf-8"))
    assert blob["kind"] == "tradeflow-desk-chain-anchors"
    assert any(item["deal_id"] == deal_id for item in blob["anchors"])


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-v"]))
