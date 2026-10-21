"""Export human-confirmed price spans as a training-material JSONL.

This is the data flywheel: the intent/price extractor is trained on a
synthetic set (66 templates, 500 items, 63.08% held-out — a synthetic-set
figure). Every time a person confirms a span on the desk, that
confirmation IS one real labelled example: the raw supplier text, the
token the human picked, and the parsed unit price. This script turns
the accumulated confirmations into a JSONL the next training round can
consume, so the model's data becomes real exactly as fast as the desk
is actually used.

Rows: one per accepted quote event —
    {event_id, deal_id, supplier_id, source, received_at,
     raw_text, chosen_token, confirmed_unit_price}

Privacy: raw_text is real supplier correspondence. The default output
path (confirmed_spans.jsonl at the repo root) is git-ignored on
purpose; do not move it into results/ (which is committed) or anywhere
tracked. Export, train, delete.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.desk.store import DeskStore, db_path  # noqa: E402


def collect_rows(store: DeskStore) -> list[dict[str, object]]:
    rows = []
    query = (
        "SELECT id, deal_id, supplier_id, source, raw_text, parsed_json, "
        "provenance_json, received_at FROM quote_events WHERE status = 'accepted' "
        "ORDER BY received_at, id"
    )
    for eid, deal_id, supplier_id, source, raw_text, parsed_json, prov_json, received_at in (
        store.conn.execute(query).fetchall()
    ):
        parsed = json.loads(parsed_json)
        prov = json.loads(prov_json)
        suggested = prov.get("suggested_token") or prov.get("chosen_token")
        rows.append(
            {
                "event_id": eid,
                "deal_id": deal_id,
                "supplier_id": supplier_id,
                "source": source,
                "received_at": received_at,
                "raw_text": raw_text,
                "chosen_token": suggested,
                "confirmed_unit_price": parsed.get("unit_price"),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=db_path())
    parser.add_argument("--out", default=str(ROOT / "confirmed_spans.jsonl"))
    args = parser.parse_args(argv)

    store = DeskStore(args.db)
    try:
        rows = collect_rows(store)
    finally:
        store.close()

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"export_confirmed_spans: {len(rows)} confirmed spans -> {out}")
    print("real supplier text, git-ignored on purpose; train, then delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
