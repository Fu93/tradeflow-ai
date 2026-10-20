"""T8 draft: the number in the body is the issued counter."""
from __future__ import annotations

import os
import re

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def test_draft_body_quotes_the_issued_counter(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    snapshot = create_deal(
        store,
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=4,
        suppliers=SUPPLIERS,
    )
    deal_id = snapshot["deal"]["id"]
    supplier = snapshot["suppliers"][0]["id"]
    opened = ingest_message(store, deal_id, source="paste", raw_text="单价 15.60 元", supplier_id=supplier)
    accept_review(store, deal_id, opened["event"]["id"], supplier, chosen_token="15.60", unit_basis="per_piece")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    card = next(item for item in store.get_snapshot(deal_id)["suppliers"] if item["id"] == supplier)
    found = re.findall(r"\d+\.\d{2}", draft["body"])
    assert f"{float(card['counter']):.2f}" in found
    assert draft["decision_id"]
    store.close()
