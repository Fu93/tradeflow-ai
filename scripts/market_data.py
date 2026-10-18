#!/usr/bin/env python3
"""Fetch the committed HS 6601 snapshot from the UN Comtrade preview.

    python scripts/market_data.py

Writes results/market_snapshot_6601.json and saves raw preview bodies under
tests/fixtures/comtrade/. Those bodies are recorded fixtures, not hand-typed
numbers. One period per call. Quantity 0 stays "not available".
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market.trade_data import (  # noqa: E402
    PREVIEW_URL,
    BadRequest,
    HttpResult,
    Pace,
    Retryable,
    derive_snapshot,
    fetch_period,
    now_iso,
    preview_url,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "comtrade"
SNAPSHOT_PATH = ROOT / "results" / "market_snapshot_6601.json"

# reporter, partner, flow, hs, year. Years are integers. One period each.
QUERIES = (
    (826, 156, "M", "6601", 2024),
    (826, 156, "M", "6601", 2025),
    (826, 156, "M", "660110", 2024),
    (826, 156, "M", "660191", 2024),
    (826, 156, "M", "660199", 2024),
    (826, 0, "M", "6601", 2024),
    (842, 156, "M", "6601", 2024),
    (97, 156, "M", "6601", 2024),
    (156, 0, "X", "6601", 2024),
    (826, 156, "M", "660199", 1990),
)


def _slug(url: str, status: int) -> str:
    query = parse_qs(urlparse(url).query)
    parts = [
        (query.get("reporterCode") or ["x"])[0],
        (query.get("partnerCode") or ["x"])[0],
        (query.get("flowCode") or ["x"])[0],
        (query.get("cmdCode") or ["x"])[0],
        (query.get("period") or ["x"])[0].replace(",", "-"),
        str(status),
    ]
    return "_".join(parts)


def _save_body(url: str, status: int, text: str) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(url, status)
    (FIXTURE_DIR / f"{slug}.json").write_text(text, encoding="utf-8")
    label = {
        "label": "recorded fixture",
        "source": "UN Comtrade public preview",
        "url": url,
        "http_status": status,
        "note": "Raw response body saved by scripts/market_data.py. Not hand-typed.",
    }
    (FIXTURE_DIR / f"{slug}.label.json").write_text(
        json.dumps(label, indent=2) + "\n",
        encoding="utf-8",
    )


def recording_get(url: str, headers: dict[str, str], timeout: int) -> HttpResult:
    """Same status rules as the library client, plus a saved raw body."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise Retryable(0) from exc
    except httpx.HTTPError as exc:
        raise Retryable(0) from exc
    if response.status_code in (200, 400, 429):
        _save_body(url, response.status_code, response.text)
    if response.status_code == 400:
        raise BadRequest()
    if response.status_code == 429 or response.status_code >= 500:
        raise Retryable(response.status_code)
    return HttpResult(response.status_code, response.text, response.headers.get("Retry-After"))


def capture_multi_period_400() -> None:
    """Preview rejects more than one period. The body is a recorded fixture.

    A 429 is saved by the client and retried. A 400 is not retried by the
    library; this helper just waits out the rate limit and tries the bad
    query again so the recorded body exists.
    """
    url = (
        f"{PREVIEW_URL}?reporterCode=826&partnerCode=156"
        "&period=2024,2023&cmdCode=660199&flowCode=M"
    )
    headers = {"Accept": "application/json", "User-Agent": "TradeFlow-MarketData/1.0"}
    for _attempt in range(4):
        try:
            recording_get(url, headers, 20)
            return
        except BadRequest:
            return
        except Retryable:
            time.sleep(4)


def main() -> int:
    pace = Pace(1)
    rows = []
    for reporter, partner, flow, hs, year in QUERIES:
        print(f"fetch {reporter} {partner} {flow} {hs} {year}", flush=True)
        row = fetch_period(
            reporter=reporter,
            partner=partner,
            year=year,
            hs=hs,
            flow=flow,
            client=recording_get,
            pace=pace,
            timeout=20,
            attempts=3,
        )
        rows.append(row)
        print(f"  status={row.get('status')} reason={row.get('reason')}", flush=True)
    capture_multi_period_400()
    snapshot = {
        "label": "live UN Comtrade preview fetch",
        "source_url": PREVIEW_URL,
        "fetched_at": now_iso(),
        "example_query": preview_url(826, 156, 2024, "660199", "M"),
        "rows": rows,
        "derived": derive_snapshot(rows),
        "notes": [
            "Recorded by scripts/market_data.py from the public preview. Not hand-typed.",
            "Quantity 0 on a preview row is stored as not available.",
            "WITS was not called.",
            "HMRC GBP figures are not merged into these USD rows.",
        ],
    }
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
