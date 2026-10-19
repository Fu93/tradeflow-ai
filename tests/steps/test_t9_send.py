"""T9 send: approval is required, and a broken chain refuses the send."""
from __future__ import annotations

import asyncio
import os

import pytest

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def test_send_without_approval_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    from src.desk.service import SendRefused, accept_review, create_deal, draft_digest, ingest_message, send_draft
    from src.desk.store import DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=5,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(store, deal_id, source="paste", raw_text="单价 15.60 元", supplier_id=supplier)
    accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    with pytest.raises(SendRefused):
        asyncio.run(
            send_draft(
                store,
                draft["id"],
                human_approved=False,
                body_sha256=draft_digest(draft["body"]),
            )
        )
    store.close()
