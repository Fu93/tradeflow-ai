#!/usr/bin/env python3
"""Fail if docs/MARKET_ANALYSIS.md drifts from the committed Comtrade snapshot."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market.trade_data import render_snapshot_markdown  # noqa: E402

SNAPSHOT = ROOT / "results" / "market_snapshot_6601.json"
DOC = ROOT / "docs" / "MARKET_ANALYSIS.md"
FILENAME = "results/market_snapshot_6601.json"


def main() -> int:
    problems: list[str] = []
    if not SNAPSHOT.is_file():
        problems.append(f"missing {SNAPSHOT}")
        snapshot = {}
    else:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if not DOC.is_file():
        problems.append(f"missing {DOC}")
        text = ""
    else:
        text = DOC.read_text(encoding="utf-8")
    if snapshot:
        block = render_snapshot_markdown(snapshot, FILENAME)
        if block not in text:
            problems.append("docs/MARKET_ANALYSIS.md does not contain the exact generated snapshot block")
    for phrase in (
        "HMRC",
        "18.66",
        "UN Comtrade",
        "not available",
        "ASSUMPTION",
        "ESTIMATE",
        "third-party",
        "unverified",
    ):
        if phrase not in text:
            problems.append(f"docs/MARKET_ANALYSIS.md is missing {phrase!r}")
    if re.search(r"WITS is the primary|primary source is WITS", text, re.I):
        problems.append("WITS must not be named as the primary source")
    if "cross-check" not in text.lower() and "cross check" not in text.lower():
        problems.append("the research note must be labelled a cross-check")
    if problems:
        print("check_market_doc failed:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("check_market_doc: MARKET_ANALYSIS.md matches the committed snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
