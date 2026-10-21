"""Anchor the desk audit-chain heads into git (external tamper evidence).

The HMAC chain proves the entries were not modified, but only against a
head hash that is known outside the database. The anchor file in
`src/desk/audit_chain.py` is one such outside record; this script is the
zero-infrastructure second one: **git itself**.

For every deal in the desk database it records:

    {"deal_id", "seq", "head_sha256", "entries", "chain_ok"}

into a JSON file you commit (default `results/chain_anchors.json`), or
into an annotated git tag with `--tag NAME`. A git commit carries its
own hash, author, and timestamp, so a database holder who rewrites the
whole chain consistently cannot also rewrite the committed anchor
without rewriting public git history.

Operational use (one line a day, or from cron):

    python scripts/anchor_chain_head.py --tag anchor-$(date +%F)

A rewound or truncated chain then shows up as `chain_ok: false`, or as a
head hash that no longer matches the previously committed anchor.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.desk.store import DeskStore, db_path  # noqa: E402


def collect_anchors(path: str) -> dict[str, object]:
    store = DeskStore(path)
    try:
        rows = store.conn.execute("SELECT DISTINCT deal_id FROM desk_chain").fetchall()
        deal_ids = [row[0] for row in rows]
        results = [(deal_id, store.verify_chain(deal_id)) for deal_id in deal_ids]
    finally:
        store.close()
    anchors = []
    for deal_id, result in results:
        entries = list(result.get("entries") or [])
        head = entries[-1] if entries else None
        anchors.append(
            {
                "deal_id": deal_id,
                "seq": (head or {}).get("seq"),
                "head_sha256": (head or {}).get("entry_hash"),
                "entries": len(entries),
                "chain_ok": bool(result.get("ok")),
            }
        )
    return {
        "kind": "tradeflow-desk-chain-anchors",
        "db": os.path.abspath(path),
        "count": len(anchors),
        "anchors": sorted(anchors, key=lambda item: item["deal_id"]),
    }


def _git_tag(tag: str, blob: str) -> int:
    proc = subprocess.run(
        ["git", "tag", "-a", tag, "-m", blob],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"anchor_chain_head: git tag failed: {proc.stderr.strip()}", file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=db_path(), help="Desk SQLite database (default: env/data/desk.db)")
    parser.add_argument("--out", default="results/chain_anchors.json", help="Output JSON file ('-' for stdout)")
    parser.add_argument("--tag", default=None, help="Also write the anchors into an annotated git tag")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        print(f"anchor_chain_head: no desk database at {args.db}", file=sys.stderr)
        return 1
    anchors = collect_anchors(args.db)
    blob = json.dumps(anchors, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out == "-":
        print(blob)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(blob + "\n", encoding="utf-8")
        print(f"anchor_chain_head: {anchors['count']} deal heads written to {args.out}")
    bad = [item["deal_id"] for item in anchors["anchors"] if not item["chain_ok"]]  # type: ignore[union-attr]
    if bad:
        print(f"anchor_chain_head: WARNING chains failing verification: {', '.join(bad)}", file=sys.stderr)
    if args.tag:
        return _git_tag(args.tag, blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
