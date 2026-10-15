"""Pre-push secret scan: make sure no real credential value appears in any
file that is about to be committed.

Reads .env (never committed) and searches the working tree for each value.
Exit code 1 if a leak is found, so this can gate a commit.
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "_frames"}
SKIP_FILES = {".env"}

# Non-secret configuration: a public endpoint URL and a public model name are
# meant to appear in docs, so they are reported separately instead of failing.
NON_SECRET_SUFFIXES = ("_BASE_URL", "_URL", "_MODEL", "_ENDPOINT")


def load_secrets() -> tuple[dict[str, str], dict[str, str]]:
    """Return (secrets, public_config) parsed from .env."""
    secrets: dict[str, str] = {}
    public: dict[str, str] = {}
    if not os.path.exists(ENV):
        print("[warn] .env not found; nothing to scan for.")
        return secrets, public
    for raw in open(ENV, encoding="utf-8", errors="ignore"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if len(val) < 12:
            continue
        if key.endswith(NON_SECRET_SUFFIXES):
            public[key] = val
        else:
            secrets[key] = val
    return secrets, public


def main() -> int:
    secrets, public = load_secrets()
    if not secrets:
        print("[warn] no credential values parsed from .env.")
    print(f"Credential values to scan for: {', '.join(sorted(secrets)) or '(none)'}")
    print(f"Public config (allowed in docs): {', '.join(sorted(public)) or '(none)'}\n")

    leaks: list[tuple[str, str, int]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES:
                continue
            path = os.path.join(dirpath, name)
            if os.path.getsize(path) > 5_000_000:
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            scanned += 1
            for key, val in secrets.items():
                if val and val in text:
                    line_no = text[: text.index(val)].count("\n") + 1
                    leaks.append((os.path.relpath(path, ROOT), key, line_no))

    print(f"Scanned {scanned} files.")
    if leaks:
        print("\n[FAIL] SECRET LEAK(S) FOUND — do not commit:")
        for path, key, line_no in leaks:
            print(f"   {path}:{line_no}  contains {key}")
        return 1
    print("[PASS] No credential value found in any file. Safe to commit.")
    if public:
        print("\n[info] Public config values (URLs/model names) appear in the repo —")
        print("       these are expected and are NOT secrets:")
        for key, val in sorted(public.items()):
            hits = []
            for dirpath, dirnames, filenames in os.walk(ROOT):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    if name in SKIP_FILES:
                        continue
                    p = os.path.join(dirpath, name)
                    if os.path.getsize(p) > 5_000_000:
                        continue
                    try:
                        t = open(p, encoding="utf-8", errors="ignore").read()
                    except OSError:
                        continue
                    if val in t:
                        hits.append(os.path.relpath(p, ROOT))
            print(f"       {key}: {len(hits)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
