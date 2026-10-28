"""Tavily web evidence for a product or a supplier. Reference only.

This is the runtime Tavily Search call behind the "Web evidence" card. It
answers two questions a buyer asks before countering:

* ``market``   - what do public pages say about wholesale prices, MOQ and
  lead time for this product?
* ``supplier`` - what do public pages say about this supplier (reviews,
  complaints, red flags)? The supplier is a company name typed by the user.

The result is evidence for a human to read. It is never a solver input:
nothing in ``src/engine``, ``src/desk`` or ``src/legacy`` imports this module,
every payload is stamped ``reference_only`` and ``influences_solver: False``,
and no number found in a snippet is parsed into a price here. Web text is
untrusted third-party content, so it is cleaned (control and bidi characters
removed, length capped), links are limited to http(s), and the API key is
sent only in the Authorization header and never returned or logged.

Every failure (no key, timeout, rejected key, rate limit, malformed body)
returns an explicit ``unavailable`` payload with a reason. A search outage
cannot affect a counter, because the counter never reads this block.
"""
from __future__ import annotations

import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .trade_data import NOT_AVAILABLE

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_KEY_ENV = "TAVILY_API_KEY"
OFFLINE_ENV = "TRADEFLOW_WEB_EVIDENCE_OFFLINE"
KINDS = ("market", "supplier")

MAX_RESULTS = 5
QUERY_CHARS = 120
TITLE_CHARS = 160
SNIPPET_CHARS = 300
URL_CHARS = 500
DATE_CHARS = 40
TIMEOUT_SECONDS = 8
CACHE_TTL_SECONDS = 900
CACHE_MAX_ENTRIES = 64

EVIDENCE_LABEL = (
    "REFERENCE ONLY. Web search snippets are unverified third-party text. "
    "They are not a price quote, not a supplier offer, and they do not set the counter."
)
CAVEATS = (
    "snippets are unverified third-party text, not a price quote",
    "this block does not influence the solver, the recommended price, or the circuit breaker",
    "a search hit about a supplier is not a verdict on that supplier",
)


@dataclass
class PostResult:
    status: int
    text: str


class Upstream(Exception):
    """A transport failure with a stable, key-free reason string."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


PostClient = Callable[[str, dict[str, str], dict[str, Any], int], PostResult]


def default_post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> PostResult:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise Upstream("timeout") from exc
    except httpx.HTTPError as exc:
        raise Upstream("unreachable") from exc
    return PostResult(response.status_code, response.text)


def tavily_key() -> str:
    """The configured key, or an empty string. A placeholder counts as unset."""
    raw = os.environ.get(TAVILY_KEY_ENV, "").strip()
    return "" if raw in {"", "__FILL_ME__"} else raw


def tavily_configured() -> bool:
    """True when a key is configured. The value itself is not returned."""
    return bool(tavily_key())


def _offline() -> bool:
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in {"1", "true", "yes"}


def _clean(value: Any, limit: int) -> str:
    """One printable line. Control, format (bidi, zero-width) and unassigned code points become spaces."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    printable = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)
    return " ".join(printable.split())[:limit]


def _safe_url(value: Any) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw or any(ord(ch) < 32 or ch.isspace() for ch in raw):
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return raw[:URL_CHARS], parsed.hostname.lower()


def build_query(kind: str, product: str, supplier: str | None = None) -> str:
    if kind == "supplier":
        name = _clean(supplier, QUERY_CHARS).replace('"', "")
        return _clean(f'"{name}" {product} supplier reviews complaints', QUERY_CHARS)
    return _clean(f"{product} wholesale unit price China factory MOQ", QUERY_CHARS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamp(ctx: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(ctx)
    stamped["reference_only"] = True
    stamped["influences_solver"] = False
    stamped["label"] = EVIDENCE_LABEL
    provenance = dict(stamped.get("provenance") or {})
    provenance["caveats"] = list(CAVEATS)
    stamped["provenance"] = provenance
    return stamped


def _unavailable(kind: str, query: str, reason: str) -> dict[str, Any]:
    return _stamp({
        "status": "unavailable",
        "reason": reason,
        "kind": kind,
        "query": query,
        "results": [],
        "provenance": {
            "source": "Tavily Search API",
            "url": NOT_AVAILABLE,
            "fetched_at": NOT_AVAILABLE,
            "cache": "miss",
        },
    })


_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(key: tuple[str, str]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            return None
        if now - hit[0] >= CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        return json.loads(json.dumps(hit[1]))


def _cache_put(key: tuple[str, str], payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda name: _CACHE[name][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.monotonic(), json.loads(json.dumps(payload)))


def _status_reason(status: int) -> str | None:
    if status == 200:
        return None
    if status in (401, 403):
        return "auth_rejected"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    return f"http_{status}"


def _score(value: Any) -> float | None:
    """Tavily's relevance score, shown as a hint. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3)


def _parse_results(body: str) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in data["results"]:
        if not isinstance(item, dict):
            continue
        link = _safe_url(item.get("url"))
        if link is None or link[0] in seen:
            continue
        seen.add(link[0])
        rows.append({
            "title": _clean(item.get("title"), TITLE_CHARS) or link[1],
            "url": link[0],
            "domain": link[1],
            "snippet": _clean(item.get("content"), SNIPPET_CHARS),
            "score": _score(item.get("score")),
            "published": _clean(item.get("published_date"), DATE_CHARS) or None,
        })
        if len(rows) >= MAX_RESULTS:
            break
    return rows


def build_web_evidence(
    kind: str = "market",
    product: str = "",
    supplier: str | None = None,
    *,
    client: PostClient | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """Search the public web through Tavily. Reference only, never a solver input."""
    cleaned_product = _clean(product, QUERY_CHARS)
    cleaned_supplier = _clean(supplier, QUERY_CHARS) if supplier else ""
    if kind not in KINDS or not cleaned_product or (kind == "supplier" and not cleaned_supplier):
        return _unavailable(kind if kind in KINDS else "market", "", "bad_request")
    query = build_query(kind, cleaned_product, cleaned_supplier)
    if _offline():
        return _unavailable(kind, query, "offline")
    api_key = tavily_key() if key is None else key.strip()
    if not api_key:
        return _unavailable(kind, query, "no_key")

    cache_key = (kind, query.lower())
    cached = _cache_get(cache_key)
    if cached is not None:
        cached["provenance"]["cache"] = "hit"
        return cached

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "TradeFlow-WebEvidence/1.0",
    }
    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        result = (client or default_post)(TAVILY_SEARCH_URL, headers, payload, TIMEOUT_SECONDS)
    except Upstream as exc:
        return _unavailable(kind, query, exc.reason)
    reason = _status_reason(result.status)
    if reason:
        return _unavailable(kind, query, reason)
    rows = _parse_results(result.text)
    if rows is None:
        return _unavailable(kind, query, "malformed")
    built = _stamp({
        "status": "ok",
        "reason": None,
        "kind": kind,
        "query": query,
        "results": rows,
        "provenance": {
            "source": "Tavily Search API",
            "url": TAVILY_SEARCH_URL,
            "fetched_at": _now_iso(),
            "cache": "miss",
        },
    })
    _cache_put(cache_key, built)
    return built
