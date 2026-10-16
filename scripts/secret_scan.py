"""Pre-push secret scan: make sure no real credential value appears in any
file that is about to be committed.

Reads .env (never committed) and searches the working tree for each value.
Exit code 1 if a leak is found, so this can gate a commit.

[FAIL-CLOSED — 見 DEEP_DEFECT_AUDIT.md §6.3]
舊版在找不到 .env 時只印 [warn]，`secrets` 為空 dict，於是「掃描 0 個憑證」
後仍印出 `[PASS] No credential value found` 並 return 0。在 CI 上沒有 .env，
這表示這個閘門**永遠通過**、從不可能失敗 —— 是個裝飾品而非安全檢查。

現改為 fail-closed：找不到 .env 且未明確傳入 --allow-missing-env 時，
以非零退出碼結束並說明原因。本機開發者（有 .env）流程不受影響。
"""
from __future__ import annotations
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "_frames"}
SKIP_FILES = {".env"}

# Non-secret configuration: a public endpoint URL and a public model name are
# meant to appear in docs, so they are reported separately instead of failing.
NON_SECRET_SUFFIXES = ("_BASE_URL", "_URL", "_MODEL", "_ENDPOINT")


def load_secrets(env_path: str = ENV) -> tuple[dict[str, str], dict[str, str]]:
    """Return (secrets, public_config) parsed from .env.

    Raises FileNotFoundError if the env file is absent — the caller decides
    whether that is fatal (fail-closed) or tolerated.
    """
    secrets: dict[str, str] = {}
    public: dict[str, str] = {}
    if not os.path.exists(env_path):
        raise FileNotFoundError(env_path)
    for raw in open(env_path, encoding="utf-8", errors="ignore"):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the working tree for credential values read from .env.",
    )
    parser.add_argument(
        "--allow-missing-env",
        action="store_true",
        help=(
            "Explicitly tolerate a missing .env (nothing to scan for). Without "
            "this flag a missing .env is a hard failure, because silently "
            "scanning for zero credentials always reports PASS."
        ),
    )
    parser.add_argument(
        "--env",
        default=ENV,
        help="Path to the .env file to read credential values from.",
    )
    parser.add_argument(
        "--root",
        default=ROOT,
        help=(
            "Directory tree to scan. Defaults to the repository root. Exposed so "
            "the behaviour of this gate can be tested against a synthetic tree."
        ),
    )
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)

    try:
        secrets, public = load_secrets(args.env)
    except FileNotFoundError as exc:
        if not args.allow_missing_env:
            print(f"[FAIL] .env not found at {exc}. Nothing to scan for, so this")
            print("       check cannot prove anything. Refusing to report PASS.")
            print("       If you know there are no credentials to scan for, re-run")
            print("       with --allow-missing-env to accept this explicitly.")
            return 1
        print("[warn] .env not found (--allow-missing-env given); nothing to scan for.")
        print("[warn] This is NOT a security verification — 0 credentials scanned.")
        return 0

    if not secrets:
        print("[FAIL] .env exists but no credential values were parsed from it.")
        print("       A scanner with no needles cannot detect a leak. Refusing to")
        print("       report PASS on an empty needle set.")
        return 1

    print(f"Credential values to scan for: {', '.join(sorted(secrets))}")
    print(f"Public config (allowed in docs): {', '.join(sorted(public)) or '(none)'}\n")

    leaks: list[tuple[str, str, int]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
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
                    leaks.append((os.path.relpath(path, root), key, line_no))

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
            for dirpath, dirnames, filenames in os.walk(root):
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
                        hits.append(os.path.relpath(p, root))
            print(f"       {key}: {len(hits)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
