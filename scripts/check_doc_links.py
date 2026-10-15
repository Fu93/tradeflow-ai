"""Check that every relative Markdown link in the repo resolves to a real file.

Resolves each target relative to the *containing document*, which is how GitHub
renders it. Also reports `file:///` links, which are always a bug in a public
repo (they point at the author's local disk and are dead for everyone else).
"""
from __future__ import annotations
import os
import re
import sys
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    broken: list[tuple[str, str]] = []
    local_path_leaks: list[tuple[str, str]] = []
    checked = 0

    docs = glob.glob(os.path.join(ROOT, "*.md"))
    docs += glob.glob(os.path.join(ROOT, "docs", "**", "*.md"), recursive=True)

    for md in sorted(docs):
        rel_md = os.path.relpath(md, ROOT)
        text = open(md, encoding="utf-8", errors="ignore").read()
        for m in LINK_RE.finditer(text):
            raw = m.group(1).strip()
            if not raw or raw.startswith(("http://", "https://", "mailto:")):
                continue
            if raw.startswith("file:"):
                local_path_leaks.append((rel_md, raw))
                continue
            target = raw.split("#", 1)[0].strip()
            if not target:
                continue
            checked += 1
            # Resolve relative to the document's own directory, like GitHub does.
            resolved = os.path.normpath(os.path.join(os.path.dirname(md), target))
            if not os.path.exists(resolved):
                broken.append((rel_md, raw))

    print(f"Checked {checked} relative links across {len(docs)} Markdown files.\n")

    if local_path_leaks:
        print("[FAIL] file:/// links (point at the author's local disk):")
        for rel_md, raw in local_path_leaks:
            print(f"   {rel_md} -> {raw}")
    else:
        print("[PASS] No file:/// links.")

    if broken:
        print(f"\n[FAIL] {len(broken)} broken relative link(s):")
        for rel_md, raw in broken:
            print(f"   {rel_md} -> {raw}")
    else:
        print("[PASS] No broken relative links.")

    return 1 if (broken or local_path_leaks) else 0


if __name__ == "__main__":
    sys.exit(main())
