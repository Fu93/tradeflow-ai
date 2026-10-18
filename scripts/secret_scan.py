"""Pre-push secret scan.

When a .env file is present, its credential values are the needles and a
hit is a leak. When it is absent, the scan still runs: it reads the git
index (tracked files) and looks for private-key and token patterns, then
says exactly what it scanned. It does not print a credential-value PASS
for a scan that had no needles.
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "_frames"}
SKIP_FILES = {".env"}

# Non-secret configuration: a public endpoint URL and a public model name are
# meant to appear in docs, so they are reported separately instead of failing.
NON_SECRET_SUFFIXES = ("_BASE_URL", "_URL", "_MODEL", "_ENDPOINT")

# Patterns a tracked file should not contain. The test suite builds these
# strings by concatenation so this file's own source is not a hit.
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("secret token", re.compile(r"\bsk-(?:live|test|proj-)?[A-Za-z0-9]{20,}\b")),
)


def load_secrets(env_path: str = ENV) -> tuple[dict[str, str], dict[str, str]]:
    """Return (secrets, public_config) parsed from .env.

    Raises FileNotFoundError if the env file is absent. The caller then
    scans tracked files for secret patterns and says that credential
    values were not compared.
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


def _git_tracked(root: str) -> list[str] | None:
    """Paths in the git index, relative to ``root``. None when this is not a checkout."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.decode("utf-8", "surrogateescape")
    return [item for item in raw.split("\0") if item]


def _walk_files(root: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES:
                continue
            found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return found


def files_to_scan(root: str) -> tuple[list[str], str]:
    """Tracked files in a git checkout, otherwise every file under ``root``."""
    tracked = _git_tracked(root)
    if tracked is not None:
        return [item for item in tracked if os.path.basename(item) not in SKIP_FILES], "tracked"
    return _walk_files(root), "tree"


def _read_text(path: str) -> str | None:
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > 5_000_000:
        return None
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return None
    if b"\0" in blob[:1024]:
        return None
    return blob.decode("utf-8", "ignore")


def pattern_hits(root: str, relative_paths: list[str]) -> list[tuple[str, str, int]]:
    hits = []
    for relative in relative_paths:
        text = _read_text(os.path.join(root, relative))
        if not text:
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            line_no = text[: match.start()].count("\n") + 1
            hits.append((relative, label, line_no))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the working tree for credential values read from .env.",
    )
    parser.add_argument(
        "--allow-missing-env",
        action="store_true",
        help=(
            "Accepted when .env is absent. The scan still checks tracked files "
            "for secret patterns and reports that credential values were not "
            "compared. It does not turn the run into a silent PASS."
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

    relative, kind = files_to_scan(root)
    print(f"Scanning {len(relative)} {kind} file(s) under {root}.")

    try:
        secrets, public = load_secrets(args.env)
    except FileNotFoundError as exc:
        print(f"No .env at {exc}. Credential values were not compared.")
        if args.allow_missing_env:
            print("(--allow-missing-env is set; the pattern scan still runs.)")
        hits = pattern_hits(root, relative)
        if hits:
            print("[FAIL] Secret pattern found:")
            for path, label, line_no in hits:
                print(f"   {path}:{line_no}  {label}")
            return 1
        print(
            f"[PASS] No secret pattern in {len(relative)} {kind} file(s). "
            "This is not a credential-value verification."
        )
        return 0

    if not secrets:
        print("[FAIL] .env exists but no credential values were parsed from it.")
        print("       A scanner with no needles cannot detect a leak. Refusing to")
        print("       report PASS on an empty needle set.")
        return 1

    print(f"Credential values to scan for: {', '.join(sorted(secrets))}")
    print(f"Public config (allowed in docs): {', '.join(sorted(public)) or '(none)'}")

    leaks: list[tuple[str, str, int]] = []
    texts: dict[str, str] = {}
    for relative_path in relative:
        text = _read_text(os.path.join(root, relative_path))
        if text is None:
            continue
        texts[relative_path] = text
        for key, val in secrets.items():
            if val and val in text:
                line_no = text[: text.index(val)].count("\n") + 1
                leaks.append((relative_path, key, line_no))
    leaks.extend(pattern_hits(root, relative))

    print(f"Scanned {len(texts)} file(s).")
    if leaks:
        print("\n[FAIL] SECRET LEAK(S) FOUND — do not commit:")
        for path, key, line_no in leaks:
            print(f"   {path}:{line_no}  contains {key}")
        return 1
    print("[PASS] No credential value or secret pattern found. Safe to commit.")
    if public:
        print("\n[info] Public config values (URLs/model names) appear in the repo —")
        print("       these are expected and are NOT secrets:")
        for key, val in sorted(public.items()):
            count = sum(1 for text in texts.values() if val in text)
            print(f"       {key}: {count} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
