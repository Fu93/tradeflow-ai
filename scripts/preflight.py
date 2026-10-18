#!/usr/bin/env python3
"""Check a local checkout before recording the demo.

Prints one FIX line per failure. Reads .env only to classify each key as
missing, placeholder, or set. Values are never printed.

Exit 0 when Python, imports, .env, the result files named by README.md, and
GET /health on the listen port all look ready. A free port is a failure with
the start command, because the recording needs the dashboard.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {"", "__fill_me__", "changeme", "todo", "your-key-here", "xxx"}
RUNTIME_IMPORTS = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("numpy", "numpy"),
    ("dotenv", "python-dotenv"),
    ("onnxruntime", "onnxruntime"),
    ("regex", "regex"),
    ("multipart", "python-multipart"),
)


def check_python(version: tuple[int, int] | None = None) -> list[str]:
    """The image is Python 3.12. 3.11 and newer are accepted locally."""
    found = version or sys.version_info[:2]
    if found >= (3, 11):
        return []
    return [
        f"Python {found[0]}.{found[1]} is too old.",
        "FIX: install Python 3.12 (the Dockerfile pin) and re-run this script.",
    ]


def check_imports(find=None) -> list[str]:
    import importlib.util

    probe = find or importlib.util.find_spec
    missing = [package for module, package in RUNTIME_IMPORTS if probe(module) is None]
    if not missing:
        return []
    joined = " ".join(missing)
    return [
        f"Missing packages: {joined}.",
        "FIX: python -m pip install -r requirements.txt",
    ]


def classify_env(text: str) -> list[tuple[str, str]]:
    """Return (key, status) pairs. status is placeholder or set. No values."""
    rows: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        status = "placeholder" if value.strip().lower() in PLACEHOLDERS else "set"
        rows.append((key, status))
    return rows


def check_env(path: Path) -> list[str]:
    if not path.is_file():
        return [
            f"{path.name} is not in the working directory.",
            "FIX: cp .env.example .env",
            "Placeholder keys are ignored. The offline demo does not need a real key.",
        ]
    rows = classify_env(path.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        return [
            f"{path.name} has no KEY=value lines.",
            "FIX: cp .env.example .env",
        ]
    set_count = sum(1 for _, status in rows if status == "set")
    placeholder_count = sum(1 for _, status in rows if status == "placeholder")
    return [
        (
            f"{path.name} is present ({len(rows)} keys, {set_count} set, "
            f"{placeholder_count} still placeholders). Values are not printed."
        )
    ]


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """True when a connect succeeds, so something is already listening."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def fetch_health(port: int, host: str = "127.0.0.1") -> tuple[int, str]:
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            body = response.read(4000).decode("utf-8", errors="replace")
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(500).decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, type(exc).__name__


def check_server(port: int = 8000) -> list[str]:
    if not port_is_open(port):
        return [
            f"Nothing is listening on port {port}.",
            "FIX: python -m src.app.main",
            f"FIX: if {port} is the wrong port, PORT=<free-port> python -m src.app.main",
        ]
    status, body = fetch_health(port)
    if status != 200 or "offline_demo" not in body:
        return [
            f"Port {port} is taken, and GET /health did not look like TradeFlow (HTTP {status}).",
            f"FIX: stop the other process (ss -ltnp 'sport = :{port}'), then start python -m src.app.main.",
        ]
    return [f"GET /health on port {port} returned 200 and includes offline_demo."]


def readme_result_paths(readme: str) -> list[str]:
    """Markdown link targets plus the files the recording and the form cite.

    Backticked paths are not included: README mentions
    results/amd_endpoint_evidence.json as a file a later command writes, and
    that file is gitignored until a real key is used.
    """
    found: list[str] = []
    for match in re.finditer(r"\]\(([^)]+)\)", readme):
        target = match.group(1).split("#", 1)[0].strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target:
            found.append(target)
    found.extend(
        [
            "results/cover_1280x720.png",
            "results/pitch.pptx",
            "results/multi_supplier_auction.md",
            "results/bench.json",
            "results/mi300x_roofline.json",
            "results/real_llm_benchmark_100.json",
            "results/self_play_benchmark.md",
            "extension/mock_chat.html",
            "SUBMISSION.md",
            "docs/DEMO_RUNBOOK.md",
            "docs/LABLAB_FORM.md",
        ]
    )
    ordered: list[str] = []
    for item in found:
        if item not in ordered:
            ordered.append(item)
    return ordered


def check_files(root: Path, relative_paths: list[str]) -> list[str]:
    missing = [rel for rel in relative_paths if not (root / rel).is_file()]
    if not missing:
        return [f"{len(relative_paths)} referenced files are on disk."]
    lines = [f"Missing {len(missing)} file(s) named by the docs:"]
    lines.extend(f"  {rel}" for rel in missing)
    lines.append("FIX: you are not at the repo root, or a results file was not checked out.")
    return lines


def _ok(lines: list[str]) -> bool:
    return not any(line.startswith("FIX:") for line in lines)


def main(argv: list[str] | None = None) -> int:
    _ = argv
    os.chdir(ROOT)
    sections = [
        ("Python", check_python()),
        ("Dependencies", check_imports()),
        ("Environment", check_env(ROOT / ".env")),
        ("Server", check_server(int(os.environ.get("TRADEFLOW_PREFLIGHT_PORT", "8000")))),
        ("Files", check_files(ROOT, readme_result_paths((ROOT / "README.md").read_text(encoding="utf-8")))),
    ]
    failed = False
    print("TradeFlow preflight")
    for title, lines in sections:
        status = "ok" if _ok(lines) else "fix"
        if status == "fix":
            failed = True
        print(f"\n[{status}] {title}")
        for line in lines:
            print(f"  {line}")
    print()
    if failed:
        print("Preflight failed. Apply the FIX lines above and run this script again.")
        return 1
    print("Preflight passed. Recording notes: docs/DEMO_RUNBOOK.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
