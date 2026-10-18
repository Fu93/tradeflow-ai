"""Recorded Comtrade fixtures and the invariant that customs data does not price."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app import main as api_main
from src.engine.multi_solver import simulate_reverse_auction
from src.engine.quote_read import read_supplier_quote
from src.market.trade_data import (
    BadRequest,
    HttpResult,
    Retryable,
    build_market_context,
    fetch_period,
    lookup_market,
    parse_comtrade_payload,
    search_hs,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "comtrade"
SAMPLE = (
    "SIMULATION — not a real supplier screenshot.\n"
    "单价：14.80元。5000個。21天。30%定金。\n"
)


def _meta(hs: str = "660199", year: int = 2024) -> dict:
    return {
        "hs": hs,
        "hs_desc": "fixture",
        "reporter_code": 826,
        "reporter_name": "United Kingdom",
        "partner_code": 156,
        "partner_name": "China",
        "flow": "M",
        "year": year,
        "url": "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
        "params": {},
        "fetched_at": "2026-10-18T00:00:00+01:00",
        "provisional": False,
        "qty_unit": "u",
        "cache": "miss",
        "tried": [year],
    }


def _load(name: str) -> dict:
    path = FIXTURES / name
    assert path.is_file(), f"recorded fixture missing: {name}"
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def _primary(name: str, hs: str) -> Decimal:
    payload = _load(name)
    for row in payload["data"]:
        if str(row.get("cmdCode")) == hs:
            return Decimal(str(row["primaryValue"]))
    raise AssertionError(f"no {hs} row in {name}")


def test_recorded_fixtures_are_labelled():
    labels = list(FIXTURES.glob("*.label.json"))
    assert labels, "no recorded-fixture labels"
    for path in labels:
        label = json.loads(path.read_text(encoding="utf-8"))
        assert label["label"] == "recorded fixture"
        assert "UN Comtrade" in label["source"]
        body = path.with_name(path.name.replace(".label.json", ".json"))
        assert body.is_file()


def test_golden_uk_china_660199_2024():
    value = _primary("826_156_M_660199_2024_200.json", "660199")
    assert value == Decimal("28991563.648")


def test_children_sum_equals_parent_2024():
    parent = _primary("826_156_M_6601_2024_200.json", "6601")
    parts = [
        _primary("826_156_M_660110_2024_200.json", "660110"),
        _primary("826_156_M_660191_2024_200.json", "660191"),
        _primary("826_156_M_660199_2024_200.json", "660199"),
    ]
    assert sum(parts, Decimal("0")) == parent


def test_qty_zero_is_unreported_and_has_no_unit_value():
    payload = _load("826_156_M_6601_2024_200.json")
    parsed = parse_comtrade_payload(payload, _meta("6601"))
    assert parsed["status"] == "ok"
    assert parsed["qty"] == "not available"
    assert parsed["unit_value_usd"] == "not available"


def test_http_400_is_not_retried(tmp_path):
    calls = {"n": 0}

    def client(url, headers, timeout):
        calls["n"] += 1
        raise BadRequest()

    result = fetch_period(
        reporter=826, partner=156, year=2024, hs="660199", flow="M",
        client=client, cache=_cache(tmp_path), pace=_pace(), attempts=3, timeout=1,
    )
    assert calls["n"] == 1
    assert result["status"] == "unavailable"
    assert "not retried" in result["detail"]


def test_http_429_is_retried_without_waiting(tmp_path):
    calls = {"n": 0}

    def client(url, headers, timeout):
        calls["n"] += 1
        raise Retryable(429)

    result = fetch_period(
        reporter=826, partner=156, year=2024, hs="660199", flow="M",
        client=client, cache=_cache(tmp_path), pace=_pace(),
        immediate=True, attempts=3, timeout=1,
    )
    assert calls["n"] == 3
    assert result["reason"] == "rate_limited"


def test_recorded_400_and_429_bodies_exist():
    bad = list(FIXTURES.glob("*660199*2024-2023*_400.json"))
    limited = list(FIXTURES.glob("*_429.json"))
    assert bad, "recorded multi-period 400 fixture missing"
    assert limited, "recorded 429 fixture missing"
    assert json.loads(bad[0].read_text(encoding="utf-8"))
    body = json.loads(limited[0].read_text(encoding="utf-8"))
    assert body.get("statusCode") == 429 or "429" in json.dumps(body)


def test_keyword_search_does_not_call_comtrade(monkeypatch):
    monkeypatch.delenv("TRADEFLOW_MARKET_OFFLINE", raising=False)

    def client(url, headers, timeout):
        raise AssertionError("keyword lookup must not call Comtrade")

    result = lookup_market(keyword="umbrella", client=client)
    assert result["status"] == "needs_confirmation"
    assert result["candidates"][0]["hs"] == "6601"
    assert result["tried"] == []


def test_umbrella_override_and_metal_case_is_not_3926():
    umbrella = search_hs("Custom Carbon Auto Umbrella")
    assert umbrella[0]["hs"] == "6601"
    assert umbrella[0]["source"] == "curated override"
    chinese = search_hs("自動三折防風雨傘")
    assert chinese[0]["hs"] == "6601"
    metal = search_hs("Aerospace CNC Metal Case")
    assert all(not (row["hs"] == "3926" and row["source"] == "curated override") for row in metal)


def test_subscription_key_stays_out_of_the_url(monkeypatch, tmp_path):
    secret = "comtrade-test-key-not-for-logs"
    monkeypatch.setenv("COMTRADE_API_KEY", secret)
    seen = {}

    def client(url, headers, timeout):
        seen["url"] = url
        seen["has_header"] = headers.get("Ocp-Apim-Subscription-Key") == secret
        return HttpResult(200, (FIXTURES / "826_156_M_660199_2024_200.json").read_text(encoding="utf-8"))

    fetch_period(
        reporter=826, partner=156, year=2024, hs="660199", flow="M",
        client=client, cache=_cache(tmp_path), pace=_pace(), attempts=1, timeout=1,
    )
    assert secret not in seen["url"]
    assert seen["has_header"] is True
    assert "Ocp-Apim-Subscription-Key" not in seen["url"]


def test_market_route_is_on_the_authenticated_router(monkeypatch):
    from src.web.routes_web import api_v1

    paths = [getattr(route, "path", "") for route in api_v1.routes]
    assert "/api/v1/market" in paths
    client = TestClient(api_main.app)
    offline = client.get("/api/v1/market", params={"hs": "12"})
    # Sandbox with no API key is fail-open. A configured key returns 401.
    if offline.status_code == 401:
        return
    assert offline.status_code == 200
    assert offline.json()["reason"] == "offline"
    monkeypatch.delenv("TRADEFLOW_MARKET_OFFLINE", raising=False)
    bad = client.get("/api/v1/market", params={"hs": "12345678"})
    assert bad.status_code == 422
    assert bad.json()["reason"] == "bad_request"


def test_recommended_price_ignores_market_context():
    solver = (ROOT / "src" / "engine" / "solver.py").read_text(encoding="utf-8")
    multi = (ROOT / "src" / "engine" / "multi_solver.py").read_text(encoding="utf-8")
    assert "market_context" not in solver
    assert "market_context" not in multi
    client = TestClient(api_main.app)
    plain = {
        "session_id": "mkt-plain",
        "product_name": "Custom Carbon Auto Umbrella",
        "target_price": 12.0,
        "ceiling_price": 16.2,
    }
    wild = dict(plain)
    wild["session_id"] = "mkt-wild"
    wild["market_context"] = {
        "status": "ok",
        "hs": "660199",
        "value_usd": "1",
        "qty": "100",
        "unit_value_usd": "0.01",
        "year": 2024,
    }
    created = client.post("/api/v1/sessions", json=plain)
    injected = client.post("/api/v1/sessions", json=wild)
    assert created.status_code == 200
    assert injected.status_code == 200
    assert created.json()["spec"]["market_context"]["influences_solver"] is False
    assert injected.json()["spec"]["market_context"]["unit_value_usd"] == "0.01"
    assert injected.json()["spec"]["market_context"]["reference_only"] is True
    offer = {"supplier_offer": 14.5, "mode": "copilot"}
    left = client.post("/api/v1/sessions/mkt-plain/round", json=offer)
    right = client.post("/api/v1/sessions/mkt-wild/round", json=offer)
    assert left.status_code == 200
    assert right.status_code == 200
    assert left.json()["recommended_offer_price"] == right.json()["recommended_offer_price"]


def test_llm_cannot_set_the_price():
    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "price": 14.8,
                                    "recommended_price": 1.0,
                                    "buyer_price": 1.0,
                                    "unit_value_usd": 0.01,
                                    "reading": "customs unit value is not our price",
                                }
                            )
                        }
                    }
                ],
            }

    env = {"VLLM_API_KEY": "test-key-not-real", "VLLM_MODEL": "Gemma-4-31B-IT"}
    with patch.dict(os.environ, env, clear=False), patch(
        "src.engine.quote_read.requests.post", return_value=Response()
    ):
        result = read_supplier_quote(SAMPLE, amd_live=True)
    assert result["buyer_price"] is None
    assert result["decides_price"] is False
    assert result["discarded_buyer_price"] is True
    assert result["fields"]["price"] == 14.8
    assert "recommended_price" not in result["fields"]
    assert price_unchanged(result) == 14.8


def price_unchanged(result: dict) -> float:
    from src.engine.quote_fields import price_the_solver_may_use

    return price_the_solver_may_use(None, result["fields"]["price"], 0.01)


def test_auction_attaches_context_without_changing_the_winner():
    import inspect

    assert "market_context" not in inspect.signature(simulate_reverse_auction).parameters
    client = TestClient(api_main.app)
    with_context = {
        "n_samples": 400,
        "n_rounds": 1,
        "product_name": "Custom Carbon Auto Umbrella",
        "hs_code": "6601",
    }
    without_context = {"n_samples": 400, "n_rounds": 1}
    left = client.post("/api/v1/multi-auction/run", json=with_context)
    right = client.post("/api/v1/multi-auction/run", json=without_context)
    assert left.status_code == 200
    assert right.status_code == 200
    assert left.json()["winning_price"] == right.json()["winning_price"]
    assert left.json()["market_context_reference_only"] is True
    assert left.json()["market_context"]["hs"] == "6601"
    assert left.json()["market_context"]["reference_only"] is True
    assert left.json()["market_context"]["influences_solver"] is False
    assert right.json()["market_context"]["influences_solver"] is False


def test_offline_context_is_explicit(monkeypatch):
    monkeypatch.setenv("TRADEFLOW_MARKET_OFFLINE", "1")
    ctx = build_market_context(None, "Custom Carbon Auto Umbrella", fetch=True)
    assert ctx["status"] == "unavailable"
    assert ctx["hs"] == "6601"
    assert ctx["value_usd"] == "not available"
    assert ctx["reference_only"] is True
    assert ctx["influences_solver"] is False


def _cache(tmp_path: Path):
    from src.market.trade_data import MarketCache

    return MarketCache(tmp_path / "responses.sqlite")


def _pace():
    from src.market.trade_data import Pace

    return Pace(0)
