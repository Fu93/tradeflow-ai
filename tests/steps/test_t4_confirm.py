"""T4 confirmation: an accepted quote cannot be accepted again."""
from __future__ import annotations

import os

import pytest

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def test_reaccept_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskRejected, DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=2,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(store, deal_id, source="paste", raw_text="单价 15.60 元", supplier_id=supplier)
    accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60", human_confirmed=True)
    with pytest.raises(DeskRejected):
        accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60", human_confirmed=True)
    store.close()
