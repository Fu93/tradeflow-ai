"""A refused key or a spent balance stops paid calls for a while; the desk says so and keeps working."""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from src.engine import llm_config

ROOT = Path(__file__).resolve().parents[1]


def _refuse(code: int):
    calls = []

    def urlopen(request, *args, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, code, "refused", {}, io.BytesIO(b'{"error":"x"}'))

    return urlopen, calls


@pytest.fixture
def nebius(monkeypatch):
    for name in ("VLLM_API_KEY", "EVOLUS_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "TRADEFLOW_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "40")


@pytest.mark.parametrize("code", [401, 402, 403])
def test_desk_reader_stops_at_the_first_refusal_and_pauses(nebius, monkeypatch, code):
    from src.desk.extract import extract_mentions

    urlopen, calls = _refuse(code)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    first = extract_mentions("单价 18.40 元/件")
    # No retry of the same key, no failover.
    assert len(calls) == 1
    assert first["error"] == "extractor_account"
    assert [a["status"] for a in first["attempts"]] == [f"http_{code}"]
    assert llm_config.model_calls_paused()["http_status"] == code
    # While paused, nothing is sent and the call is not counted.
    second = extract_mentions("单价 18.40 元/件")
    assert len(calls) == 1
    assert second["error"] == "extractor_paused" and second["attempts"] == []
    assert llm_config.model_calls_today()["used"] == 1


def test_a_server_error_still_retries_and_does_not_pause(nebius, monkeypatch):
    from src.desk.extract import extract_mentions

    urlopen, calls = _refuse(503)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = extract_mentions("单价 18.40 元/件")
    assert len(calls) == 2
    assert result["error"] != "extractor_account"
    assert llm_config.model_calls_paused() is None


def test_a_test_transport_never_pauses_the_real_budget():
    from src.desk.extract import extract_mentions

    def send(_body):
        raise urllib.error.HTTPError("http://fake.invalid", 402, "refused", {}, io.BytesIO(b"{}"))

    result = extract_mentions("单价 18.40 元/件", transport=send)
    assert result["error"] == "extractor_account"
    assert llm_config.model_calls_paused() is None


def test_the_pause_ends(nebius, monkeypatch):
    assert llm_config.note_model_refusal(402) is True
    assert llm_config.take_model_call() is False
    now = __import__("time").time()
    monkeypatch.setattr(llm_config.time, "time", lambda: now + 10_000)
    assert llm_config.model_calls_paused() is None
    assert llm_config.take_model_call() is True


def test_other_statuses_do_not_pause():
    for code in (None, 200, 400, 404, 422, 429, 500, 503):
        assert llm_config.note_model_refusal(code) is False
    assert llm_config.model_calls_paused() is None


class _Refused:
    status_code = 402
    text = '{"error":"insufficient balance"}'

    def json(self):
        return {"error": "insufficient balance"}


def test_dashboard_reader_falls_back_pauses_and_says_why(nebius):
    from src.engine.quote_read import read_supplier_quote

    with patch("src.engine.quote_read.requests.post", return_value=_Refused()) as post:
        first = read_supplier_quote("单价 18.40 元/件", amd_live=True)
        second = read_supplier_quote("单价 18.40 元/件", amd_live=True)
    assert post.call_count == 1
    for result in (first, second):
        assert result["status"] == "offline_fallback"
        assert result["model_calls_paused"] is True
        assert "refused this server's key or its credit" in result["display"]
        assert result["buyer_price"] is None
    assert "nebius-key" not in json.dumps(first) + json.dumps(second)


def test_health_reports_the_pause_without_secrets(nebius):
    llm_config.note_model_refusal(402)
    status = llm_config.llm_status()
    paused = status["model_calls_today"]["paused"]
    assert paused["http_status"] == 402 and paused["until"]
    assert "nebius-key" not in json.dumps(status)


def test_desk_page_explains_both_states():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert 'provenance.extractor_error === "extractor_paused"' in script
    assert 'provenance.extractor_error === "extractor_account"' in script
    assert "refused this server's key or its credit is spent" in script
