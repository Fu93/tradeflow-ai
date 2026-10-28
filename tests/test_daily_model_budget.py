"""Paid model calls stop at a daily cap; the desk keeps working on the rule reader."""
from __future__ import annotations

import json
import urllib.request
from unittest.mock import patch

from src.engine import llm_config


class _Body:
    def read(self):
        content = json.dumps({"mentions": []})
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_cap_counts_per_day_and_env_overrides(monkeypatch):
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "2")
    assert llm_config.take_model_call() is True
    assert llm_config.take_model_call() is True
    assert llm_config.take_model_call() is False
    assert llm_config.model_calls_today() == {"used": 2, "cap": 2}
    monkeypatch.setattr(llm_config, "_today", lambda: "2099-01-01")
    assert llm_config.take_model_call() is True


def test_zero_turns_paid_calls_off_and_bad_values_use_the_default(monkeypatch):
    from src.config.heuristics import LLM_DAILY_CALL_CAP

    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "0")
    assert llm_config.take_model_call() is False
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "lots")
    assert llm_config.daily_call_cap() == int(LLM_DAILY_CALL_CAP)


def test_desk_reader_stops_calling_once_the_cap_is_spent(monkeypatch):
    from src.desk.extract import extract_mentions

    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "1")
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(1) or _Body())
    first = extract_mentions("单价 18.40 元/件")
    assert first.get("error") is None and len(calls) == 1
    second = extract_mentions("单价 18.40 元/件")
    assert second["error"] == "extractor_daily_cap"
    assert second["mentions"] == [] and second["attempts"] == []
    assert len(calls) == 1


def test_dashboard_reader_falls_back_and_says_why(monkeypatch):
    from src.engine.quote_read import read_supplier_quote

    for name in ("VLLM_API_KEY", "EVOLUS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.setenv("TRADEFLOW_LLM_DAILY_CALLS", "0")
    with patch("src.engine.quote_read.requests.post") as post:
        result = read_supplier_quote("单价 18.40 元/件", amd_live=True)
    post.assert_not_called()
    assert result["status"] == "offline_fallback"
    assert result["daily_cap_reached"] is True
    assert "budget" in result["display"]
    assert result["buyer_price"] is None


def test_health_shows_the_counter_without_secrets(monkeypatch):
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    status = llm_config.llm_status()
    assert status["model_calls_today"]["cap"] == llm_config.daily_call_cap()
    assert "nebius-key" not in json.dumps(status)


def test_desk_page_explains_the_cap():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert 'provenance.extractor_error === "extractor_daily_cap"' in script
