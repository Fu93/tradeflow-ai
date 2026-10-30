"""Read-only deployment gate. A green health check on an old build is a failure."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import urllib.error
import urllib.request


def check(base: str, expected_sha: str) -> dict:
    results = {}
    for path in ("version", "health", "ready"):
        try:
            try:
                response = urllib.request.urlopen(base.rstrip("/") + "/" + path, timeout=25)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                body = json.load(response)
                results[path] = {"http_status": response.status, "status": body.get("status")}
                if path == "version":
                    results[path]["git_sha"] = body.get("git_sha", "unknown")
                if path == "ready":
                    results[path]["blocking"] = body.get("blocking", [])
        except (OSError, ValueError) as exc:
            results[path] = {"error": type(exc).__name__}
    actual = results.get("version", {}).get("git_sha", "unknown")
    version_match = isinstance(actual, str) and actual.lower().startswith(expected_sha.lower())
    ok = (
        version_match
        and results["version"].get("http_status") == 200
        and results["health"].get("http_status") == 200
        and results["health"].get("status") == "ok"
        and results["ready"].get("http_status") == 200
        and results["ready"].get("status") == "ready"
    )
    return {
        "ok": bool(ok),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "expected_sha": expected_sha,
        "version_match": version_match,
        "probes": results,
        "scope": "read-only probes; does not prove persistence, live providers or supplier delivery",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.expected_sha):
        parser.error("--expected-sha must be a 7 to 40 digit hexadecimal git commit")
    report = check(args.base_url, args.expected_sha)
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")
    raise SystemExit(0 if report["ok"] else 1)
