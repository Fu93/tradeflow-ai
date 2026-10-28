"""Optional Tavily web search for fresh trade or market background.

The key is read only from ``TAVILY_API_KEY``. A missing key, a failed call,
or a used-up allowance returns no pages and logs a warning. It does not
raise, and nothing in the quote, extractor, or solver path calls it.

The request stays on basic search (one credit) with at most five results.
``auto_parameters`` stays off so Tavily cannot upgrade the call to an
advanced search (two credits). ``include_answer`` and raw page text stay
off so the reply is a short list of pages, not a second model answer.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.engine.llm_config import real_secret

logger = logging.getLogger("tradeflow.desk.web_search")

SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_MAX_RESULTS = 5
RESULT_CAP = 5
DEFAULT_TIMEOUT_SECONDS = 8
_TOPICS = frozenset({"general", "news", "finance"})
_CREDIT_LIMIT = frozenset({432, 433})


def _api_key() -> str | None:
    return real_secret(os.environ.get("TAVILY_API_KEY"))


def _empty(query: str, *, configured: bool, error: str | None) -> dict[str, Any]:
    return {"configured": configured, "query": query, "results": [], "error": error}


def _max_results(value: int) -> int:
    count = value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_MAX_RESULTS
    if count < 1:
        return 1
    if count > RESULT_CAP:
        return RESULT_CAP
    return count


def _topic(value: str) -> str:
    name = value.strip().casefold() if isinstance(value, str) else ""
    if name in _TOPICS:
        return name
    return "general"


def _page(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    title = row.get("title")
    content = row.get("content")
    page: dict[str, Any] = {
        "title": title.strip() if isinstance(title, str) else "",
        "url": url.strip(),
        "content": content.strip() if isinstance(content, str) else "",
    }
    score = row.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return page
    page["score"] = float(score)
    return page


def _pages(payload: Any, limit: int) -> list[dict[str, Any]] | None:
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    pages: list[dict[str, Any]] = []
    for row in rows:
        page = _page(row)
        if page is None:
            continue
        pages.append(page)
        if len(pages) >= limit:
            break
    return pages


def _post(
    client: httpx.Client,
    query: str,
    key: str,
    limit: int,
    topic: str,
    timeout: float,
) -> dict[str, Any]:
    body = {
        "query": query,
        "search_depth": "basic",
        "topic": topic,
        "max_results": limit,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
    }
    headers = {"Authorization": "Bearer " + key}
    try:
        response = client.post(SEARCH_URL, json=body, headers=headers, timeout=timeout)
        status = response.status_code
        if status in _CREDIT_LIMIT:
            logger.warning("tavily web search skipped: credit limit")
            return _empty(query, configured=True, error="credit_limit")
        if status == 429:
            logger.warning("tavily web search skipped: rate limit")
            return _empty(query, configured=True, error="rate_limited")
        if status != 200:
            logger.warning("tavily web search failed: http %s", status)
            return _empty(query, configured=True, error="request_failed")
        pages = _pages(response.json(), limit)
    except Exception as exc:
        logger.warning("tavily web search failed: %s", type(exc).__name__)
        return _empty(query, configured=True, error="request_failed")
    if pages is None:
        logger.warning("tavily web search failed: malformed body")
        return _empty(query, configured=True, error="request_failed")
    return {"configured": True, "query": query, "results": pages, "error": None}


def web_search(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    topic: str = "general",
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Search Tavily and return a short page list.

    ``client`` is a test double. A normal call opens its own client and
    closes it. The return value always has ``configured``, ``query``,
    ``results``, and ``error``. ``results`` is empty when the key is
    missing or the call does not succeed.
    """
    text = " ".join(str(query).split())
    if not text:
        return _empty("", configured=_api_key() is not None, error="empty_query")
    key = _api_key()
    if key is None:
        logger.warning("tavily web search skipped: TAVILY_API_KEY is not set")
        return _empty(text, configured=False, error="not_configured")
    limit = _max_results(max_results)
    topic_name = _topic(topic)
    if client is None:
        # ``Client.__exit__`` closes the transport without calling ``close``.
        # Call ``close`` directly so a failed search still releases the socket.
        owned = httpx.Client(timeout=timeout)
        try:
            return _post(owned, text, key, limit, topic_name, timeout)
        finally:
            owned.close()
    return _post(client, text, key, limit, topic_name, timeout)
