#!/usr/bin/env python3
"""Print lablab form character counts. Limits: title 5-50, short 50-255, long 600-2000."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, heading: str, nxt: str) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(nxt)
    return text[start:end].strip()


def main() -> int:
    submission = (ROOT / "SUBMISSION.md").read_text(encoding="utf-8")
    form = (ROOT / "docs" / "LABLAB_FORM.md").read_text(encoding="utf-8")
    title = re.search(r"## Title\s+```\s*([^\n`]+)\s*```", form).group(1).strip()
    short = _section(submission, "## Short description", "## Long description").split("\n\n")[0].strip()
    long_text = _section(submission, "## Long description", "## Demo video script")
    fence = re.search(r"## Long description\s+~~~(.*?)~~~", form, re.S).group(1).strip()
    rows = (
        ("title", len(title), 5, 50),
        ("short", len(short), 50, 255),
        ("long", len(long_text), 600, 2000),
        ("form fence equals long", int(fence == long_text), 1, 1),
    )
    print("| field | count | min | max |")
    print("|---|---:|---:|---:|")
    for name, count, low, high in rows:
        print(f"| {name} | {count} | {low} | {high} |")
    return 0 if all(low <= count <= high for _, count, low, high in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
