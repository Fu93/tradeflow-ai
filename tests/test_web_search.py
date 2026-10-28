"""Optional Tavily search. The HTTP call is mocked; no key and no network."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from src.desk.web_search import SEARCH_URL, web_search

KEY = "tvly-unit-test-key"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_payload() -> dict:
    return {
        "query": "umbrella fabric news",
        "answer": "should not be copied",
        "results": [
            {
                "title": " Fabric news ",
                "url": " https://example.test/fabric ",
                "content": " Spot prices moved. ",
                "score": 0.81,
                "raw_content": "full page that must stay out",
            },
            {"title": "No url"},
            "not-a-row",
            {
                "title": "Second",
                "url": "https://example.test/two",
                "content": "More context",
                "score": True,
            },
        ],
    }


def test_search_posts_a_small_basic_query_and_keeps_page_fields(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_payload())

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        result = web_search("  umbrella   fabric news  ", max_results=50, topic="news", client=client)

    assert seen["url"] == SEARCH_URL
    assert seen["method"] == "POST"
    assert seen["authorization"] == f"Bearer {KEY}"
    assert seen["body"] == {
        "query": "umbrella fabric news",
        "search_depth": "basic",
        "topic": "news",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
    }
    assert KEY not in json.dumps(seen["body"])
    assert result["configured"] is True
    assert result["error"] is None
    assert result["query"] == "umbrella fabric news"
    assert result["results"] == [
        {
            "title": "Fabric news",
            "url": "https://example.test/fabric",
            "content": "Spot prices moved.",
            "score": 0.81,
        },
        {
            "title": "Second",
            "url": "https://example.test/two",
            "content": "More context",
        },
    ]


def test_missing_or_placeholder_key_returns_no_results_and_does_not_call(monkeypatch, caplog):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Tavily was called")

    def _refuse_client(*_args, **_kwargs):
        raise AssertionError("client opened")

    client = _client(handler)
    monkeypatch.setattr("src.desk.web_search.httpx.Client", _refuse_client)
    with client:
        for value in (None, "", "   ", "__FILL_ME__", "changeme", "your_key_here"):
            if value is None:
                monkeypatch.delenv("TAVILY_API_KEY", raising=False)
            else:
                monkeypatch.setenv("TAVILY_API_KEY", value)
            with caplog.at_level(logging.WARNING, logger="tradeflow.desk.web_search"):
                result = web_search("steel coil news", client=client)
            assert result == {
                "configured": False,
                "query": "steel coil news",
                "results": [],
                "error": "not_configured",
            }
    assert "TAVILY_API_KEY" in caplog.text
    assert "not set" in caplog.text.casefold() or "not configured" in caplog.text.casefold()


def test_empty_query_does_not_call(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Tavily was called")

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        result = web_search("   ", client=client)
    assert result["results"] == []
    assert result["configured"] is True
    assert result["error"] == "empty_query"


def test_credit_limit_and_rate_limit_return_no_results(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        status = 432 if "credit" in body["query"] else 429
        return httpx.Response(status, json={"detail": {"error": f"plan limit {KEY}"}})

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        with caplog.at_level(logging.WARNING, logger="tradeflow.desk.web_search"):
            credit = web_search("credit check", client=client)
            limited = web_search("rate check", client=client)
    assert credit["results"] == []
    assert credit["error"] == "credit_limit"
    assert credit["configured"] is True
    assert limited["results"] == []
    assert limited["error"] == "rate_limited"
    assert KEY not in caplog.text
    assert "credit limit" in caplog.text.casefold()


def test_paygo_limit_is_a_credit_limit(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(433, json={"detail": {"error": "paygo"}})

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        result = web_search("paygo check", client=client)
    assert result["results"] == []
    assert result["error"] == "credit_limit"


@pytest.mark.parametrize(
    "problem",
    [
        httpx.TimeoutException("timed out"),
        httpx.ConnectError(f"dial failed {KEY}"),
        "bad-json",
        "not-an-object",
    ],
)
def test_transport_and_body_failures_return_no_results(monkeypatch, caplog, problem):
    def handler(_request: httpx.Request) -> httpx.Response:
        if problem == "bad-json":
            return httpx.Response(200, content=b"not-json")
        if problem == "not-an-object":
            return httpx.Response(200, json=["nope"])
        if isinstance(problem, Exception):
            raise problem
        raise AssertionError(problem)

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        with caplog.at_level(logging.WARNING, logger="tradeflow.desk.web_search"):
            result = web_search("background", client=client)
    assert result["results"] == []
    assert result["error"] == "request_failed"
    assert result["configured"] is True
    assert KEY not in caplog.text


def test_http_error_status_returns_no_results(monkeypatch, caplog):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"error": f"Unauthorized {KEY}"}})

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        with caplog.at_level(logging.WARNING, logger="tradeflow.desk.web_search"):
            result = web_search("who quoted this", client=client)
    assert result["results"] == []
    assert result["error"] == "request_failed"
    assert KEY not in caplog.text


def test_zero_max_results_is_sent_as_one_page(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"results": []})

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        result = web_search("one page", max_results=0, client=client)
    assert seen["body"]["max_results"] == 1
    assert result["error"] is None


def test_unknown_topic_stays_general_and_one_result_is_honoured(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Only", "url": "https://example.test/only", "content": "one"},
                    {"title": "Extra", "url": "https://example.test/extra", "content": "two"},
                ]
            },
        )

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    with _client(handler) as client:
        result = web_search("market background", max_results=1, topic="sports", client=client)
    assert seen["body"]["topic"] == "general"
    assert seen["body"]["search_depth"] == "basic"
    assert seen["body"]["max_results"] == 1
    assert [row["url"] for row in result["results"]] == ["https://example.test/only"]


def test_owned_client_closes_when_the_call_fails(monkeypatch):
    closed = {"value": False}

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    class _Closing(httpx.Client):
        def close(self) -> None:
            closed["value"] = True
            super().close()

    def factory(*_args, **kwargs):
        return _Closing(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    monkeypatch.setattr("src.desk.web_search.httpx.Client", factory)
    result = web_search("owned client")
    assert result["results"] == []
    assert result["error"] == "request_failed"
    assert closed["value"] is True


def test_quoted_env_value_is_stripped_before_the_header(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"results": []})

    monkeypatch.setenv("TAVILY_API_KEY", f'"{KEY}"')
    with _client(handler) as client:
        result = web_search("clean key", client=client)
    assert result["error"] is None
    assert result["results"] == []
