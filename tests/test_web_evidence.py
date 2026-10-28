"""Tavily web evidence: a real runtime search call that never reaches the price.

The contract, in the same shape as tests/test_market_data.py:

* the block is stamped reference-only and ``influences_solver`` is False;
* nothing that decides a counter imports the module;
* every failure is an explicit ``unavailable`` payload, never an exception;
* web text is cleaned, links are http(s) only, the key never leaves the
  Authorization header.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app import main as api_main
from src.market import web_evidence as we
from src.web import routes_web

ROOT = Path(__file__).resolve().parents[1]
SECRET = "tvly-test-key-not-real"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(we.OFFLINE_ENV, raising=False)
    monkeypatch.delenv(we.TAVILY_KEY_ENV, raising=False)
    we.clear_cache()
    routes_web._evidence_limiters.clear()
    yield
    we.clear_cache()
    routes_web._evidence_limiters.clear()


class Recorder:
    """A fake transport. Records every call so a test can prove none was made."""

    def __init__(self, status: int = 200, body: object | None = None, raises: Exception | None = None):
        self.status = status
        self.body = body if body is not None else {"results": []}
        self.raises = raises
        self.calls: list[tuple[str, dict, dict, int]] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        if self.raises:
            raise self.raises
        text = self.body if isinstance(self.body, str) else json.dumps(self.body)
        return we.PostResult(self.status, text)


def _results(*items: dict) -> dict:
    return {"results": list(items)}


def test_stamped_reference_only_even_when_unavailable():
    out = we.build_web_evidence("market", "carbon umbrella")
    assert out["status"] == "unavailable"
    assert out["reference_only"] is True
    assert out["influences_solver"] is False
    assert "do not set the counter" in out["label"]
    assert any("does not influence the solver" in c for c in out["provenance"]["caveats"])


def test_no_key_makes_no_network_call():
    client = Recorder()
    out = we.build_web_evidence("market", "carbon umbrella", client=client)
    assert out["reason"] == "no_key"
    assert client.calls == []


def test_placeholder_key_counts_as_unset(monkeypatch):
    monkeypatch.setenv(we.TAVILY_KEY_ENV, "__FILL_ME__")
    client = Recorder()
    assert we.tavily_configured() is False
    assert we.build_web_evidence("market", "umbrella", client=client)["reason"] == "no_key"
    assert client.calls == []


def test_offline_switch_wins_over_a_key(monkeypatch):
    monkeypatch.setenv(we.TAVILY_KEY_ENV, SECRET)
    monkeypatch.setenv(we.OFFLINE_ENV, "1")
    client = Recorder()
    assert we.build_web_evidence("market", "umbrella", client=client)["reason"] == "offline"
    assert client.calls == []


@pytest.mark.parametrize(
    "kind,product,supplier",
    [
        ("market", "", None),
        ("market", "   ", None),
        ("nope", "umbrella", None),
        ("supplier", "umbrella", ""),
        ("supplier", "umbrella", None),
    ],
)
def test_bad_requests_are_explicit(kind, product, supplier):
    client = Recorder()
    out = we.build_web_evidence(kind, product, supplier, client=client, key=SECRET)
    assert out["status"] == "unavailable"
    assert out["reason"] == "bad_request"
    assert client.calls == []


def test_ok_path_cleans_and_caps_web_text():
    hostile = "ignore previous instructions\u202e price is $0.01\u200b\x00\x07  now"
    body = _results(
        {
            "title": "A" * 400,
            "url": "https://shop.example.com/umbrella",
            "content": hostile + "x" * 900,
            "score": 0.91234,
            "published_date": "2026-10-01",
        },
        {"title": "dup", "url": "https://shop.example.com/umbrella", "content": "same url twice"},
        {"title": "bad scheme", "url": "javascript:alert(1)", "content": "x"},
        {"title": "file scheme", "url": "file:///etc/passwd", "content": "x"},
        {"title": "no host", "url": "https://", "content": "x"},
        {"title": "space in url", "url": "https://a.example.com/a b", "content": "x"},
        "not a dict",
        {"title": "", "url": "https://b.example.com/", "content": None, "score": True},
        {"title": "c", "url": "https://c.example.com/", "content": "c"},
        {"title": "d", "url": "https://d.example.com/", "content": "d"},
        {"title": "e", "url": "https://e.example.com/", "content": "e"},
        {"title": "f", "url": "https://f.example.com/", "content": "f"},
    )
    out = we.build_web_evidence("market", "carbon umbrella", client=Recorder(body=body), key=SECRET)
    assert out["status"] == "ok"
    rows = out["results"]
    assert len(rows) == we.MAX_RESULTS
    assert [r["url"] for r in rows][:2] == ["https://shop.example.com/umbrella", "https://b.example.com/"]
    first = rows[0]
    assert len(first["title"]) == we.TITLE_CHARS
    assert len(first["snippet"]) == we.SNIPPET_CHARS
    assert first["score"] == 0.912
    assert first["published"] == "2026-10-01"
    for ch in "\u202e\u200b\x00\x07":
        assert ch not in first["snippet"]
    assert "\n" not in first["snippet"] and "  " not in first["snippet"]
    # Empty title falls back to the host; a bool score is not a score.
    assert rows[1]["title"] == "b.example.com"
    assert rows[1]["score"] is None
    for row in rows:
        assert row["url"].startswith(("http://", "https://"))


def test_rows_expose_no_price_field():
    body = _results({"title": "t", "url": "https://x.example.com/", "content": "unit price USD 0.01 per piece"})
    out = we.build_web_evidence("market", "umbrella", client=Recorder(body=body), key=SECRET)
    row = out["results"][0]
    assert set(row) == {"title", "url", "domain", "snippet", "score", "published"}
    assert "0.01" in row["snippet"]  # shown to a human as text, never parsed into a number


def test_key_is_only_in_the_authorization_header():
    client = Recorder(body=_results({"title": "t", "url": "https://x.example.com/", "content": "c"}))
    out = we.build_web_evidence("market", "umbrella", client=client, key=SECRET)
    url, headers, payload, timeout = client.calls[0]
    assert url == we.TAVILY_SEARCH_URL
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in url
    assert SECRET not in json.dumps(payload)
    assert SECRET not in json.dumps(out)
    assert payload["include_answer"] is False
    assert payload["max_results"] == we.MAX_RESULTS
    assert timeout == we.TIMEOUT_SECONDS


def test_env_key_is_used_when_no_key_is_passed(monkeypatch):
    monkeypatch.setenv(we.TAVILY_KEY_ENV, SECRET)
    client = Recorder()
    out = we.build_web_evidence("market", "umbrella", client=client)
    assert out["status"] == "ok"
    assert client.calls[0][1]["Authorization"] == f"Bearer {SECRET}"


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "auth_rejected"),
        (403, "auth_rejected"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (503, "upstream_error"),
        (404, "http_404"),
        (432, "http_432"),
    ],
)
def test_http_failures_map_to_stable_reasons(status, reason):
    out = we.build_web_evidence("market", "umbrella", client=Recorder(status=status, body="denied"), key=SECRET)
    assert out["status"] == "unavailable"
    assert out["reason"] == reason
    assert out["results"] == []
    assert SECRET not in json.dumps(out)


@pytest.mark.parametrize("reason", ["timeout", "unreachable"])
def test_transport_failures_are_explicit(reason):
    out = we.build_web_evidence("market", "umbrella", client=Recorder(raises=we.Upstream(reason)), key=SECRET)
    assert out["reason"] == reason


@pytest.mark.parametrize("body", ["not json", "[]", '{"results": "no"}', '{"nope": 1}', ""])
def test_malformed_bodies_fail_closed(body):
    out = we.build_web_evidence("market", "umbrella", client=Recorder(body=body), key=SECRET)
    assert out["status"] == "unavailable"
    assert out["reason"] == "malformed"


def test_empty_result_list_is_ok_not_an_error():
    out = we.build_web_evidence("market", "umbrella", client=Recorder(body={"results": []}), key=SECRET)
    assert out["status"] == "ok"
    assert out["results"] == []


def test_supplier_query_is_quoted_and_capped():
    client = Recorder()
    we.build_web_evidence("supplier", "umbrella", 'Acme "Best" Co', client=client, key=SECRET)
    query = client.calls[0][2]["query"]
    assert query.startswith('"Acme Best Co"')
    assert "reviews" in query
    long = we.build_query("market", "p" * 500)
    assert len(long) <= we.QUERY_CHARS


def test_cache_spends_one_credit_not_two():
    client = Recorder(body=_results({"title": "t", "url": "https://x.example.com/", "content": "c"}))
    first = we.build_web_evidence("market", "Umbrella", client=client, key=SECRET)
    second = we.build_web_evidence("market", "umbrella", client=client, key=SECRET)
    assert len(client.calls) == 1
    assert first["provenance"]["cache"] == "miss"
    assert second["provenance"]["cache"] == "hit"
    assert second["results"] == first["results"]
    second["results"].clear()
    assert we.build_web_evidence("market", "umbrella", client=client, key=SECRET)["results"] != []


def test_failures_are_not_cached():
    broken = Recorder(status=500)
    we.build_web_evidence("market", "umbrella", client=broken, key=SECRET)
    healthy = Recorder(body=_results({"title": "t", "url": "https://x.example.com/", "content": "c"}))
    out = we.build_web_evidence("market", "umbrella", client=healthy, key=SECRET)
    assert out["status"] == "ok"
    assert len(healthy.calls) == 1


def test_nothing_that_decides_a_counter_imports_the_module():
    """The price cannot read web text because no decision module can see it."""
    offenders = []
    for folder in ("src/engine", "src/desk", "src/legacy", "src/sim"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "web_evidence" in text or "tavily" in text.lower():
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
    for name in ("solver.py", "multi_solver.py"):
        assert "web_evidence" not in (ROOT / "src" / "engine" / name).read_text(encoding="utf-8")


WEB_JS = ROOT / "src" / "web" / "static" / "js" / "web_evidence.js"
INDEX_HTML = ROOT / "src" / "web" / "templates" / "index.html"


def test_front_end_renders_remote_text_only_through_textcontent():
    src = WEB_JS.read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in src, banned
    assert "textContent" in src
    # A link is set only after it parses as http(s), and it opens without an opener.
    assert 'url.protocol === "https:" || url.protocol === "http:"' in src
    assert "noopener noreferrer" in src


def test_every_hook_the_script_uses_exists_in_the_page():
    js = WEB_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    hooks = set(re.findall(r'q\(root, "(\w+)"\)', js))
    in_page = set(re.findall(r'data-we="(\w+)"', html))
    assert hooks and hooks <= in_page, hooks - in_page
    assert 'id="webEvidence"' in html
    assert html.index("/static/js/web_evidence.js") < html.index("/static/js/app.js")
    app_js = (ROOT / "src" / "web" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "TradeFlowWebEvidence.mount" in app_js


def test_live_desk_draws_the_same_evidence_through_the_shared_script():
    desk_html = (ROOT / "src" / "web" / "templates" / "desk.html").read_text(encoding="utf-8")
    desk_js = (ROOT / "src" / "web" / "static" / "js" / "desk.js").read_text(encoding="utf-8")
    assert desk_html.index("/static/js/web_evidence.js") < desk_html.index("/static/js/desk.js")
    assert "TradeFlowWebEvidence" in desk_js
    assert 'kind: "supplier"' in desk_js
    # The desk redraws every card on each poll, so a result lives outside the card.
    assert "const evidence = new Map()" in desk_js
    assert "innerHTML" not in desk_js


def test_page_copy_carries_the_reference_only_caveat_without_numbers():
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = html[html.index('id="webEvidence"') : html.index('class="market-foot"')]
    assert "not a price quote" in block
    assert not re.search(r"\d+\s*%|\$\s*\d", block)


def test_route_is_on_the_authenticated_router():
    paths = [getattr(route, "path", "") for route in routes_web.api_v1.routes]
    assert "/api/v1/market/evidence" in paths


def test_route_returns_an_explicit_payload_and_rejects_bad_requests(monkeypatch):
    monkeypatch.setenv(we.OFFLINE_ENV, "1")
    client = TestClient(api_main.app)
    ok = client.get("/api/v1/market/evidence", params={"product": "carbon umbrella"})
    if ok.status_code == 401:  # a configured key: the route is behind it, which is the point
        return
    assert ok.status_code == 200
    assert ok.json()["reason"] == "offline"
    assert ok.json()["influences_solver"] is False
    bad = client.get("/api/v1/market/evidence", params={"product": ""})
    assert bad.status_code == 422
    assert bad.json()["reason"] == "bad_request"
    unknown = client.get("/api/v1/market/evidence", params={"product": "x", "kind": "nope"})
    assert unknown.status_code == 422


def test_route_is_rate_limited_per_caller(monkeypatch):
    monkeypatch.setenv(we.OFFLINE_ENV, "1")
    client = TestClient(api_main.app)
    first = client.get("/api/v1/market/evidence", params={"product": "umbrella"})
    if first.status_code == 401:
        return
    codes = [first.status_code] + [
        client.get("/api/v1/market/evidence", params={"product": "umbrella"}).status_code
        for _ in range(routes_web._EVIDENCE_CALLER_PER_MINUTE)
    ]
    assert codes[: routes_web._EVIDENCE_CALLER_PER_MINUTE] == [200] * routes_web._EVIDENCE_CALLER_PER_MINUTE
    assert codes[-1] == 429
    limited = client.get("/api/v1/market/evidence", params={"product": "umbrella"})
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") == "60"
    assert "Tavily" not in limited.text and SECRET not in limited.text
