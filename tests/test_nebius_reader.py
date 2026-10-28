"""The dashboard's live quote reader runs on Nebius Token Factory when its key is set.

The reader may name fields. It never sets the buyer's price, whichever provider serves it.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from src.config.heuristics import (
    EXTRACTOR_BASE_URL,
    EXTRACTOR_FALLBACK_MODEL,
    EXTRACTOR_MAX_OUTPUT_TOKENS,
    EXTRACTOR_REASONING_BUDGET,
)
from src.engine import llm_config
from src.engine.quote_read import read_supplier_quote

SAMPLE = "SIMULATION — not a real supplier screenshot.\n单价：14.80元。5000個。21天。30%定金。\n"
KEYS = ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "TRADEFLOW_LLM_BASE_URL", "TRADEFLOW_DASHBOARD_MODEL",
        "VLLM_API_KEY", "VLLM_BASE_URL", "VLLM_MODEL", "EVOLUS_API_KEY")


@pytest.fixture
def clean_env():
    saved = {key: os.environ.pop(key) for key in KEYS if key in os.environ}
    yield
    for key in KEYS:
        os.environ.pop(key, None)
    os.environ.update(saved)


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {
            "usage": {"prompt_tokens": 40, "completion_tokens": 60, "total_tokens": 100},
            "choices": [{"message": {"content": json.dumps(
                {"price": 14.8, "moq": 5000, "lead_time_days": 21, "deposit_pct": 30, "our_price": 9.0}
            )}}],
        }


def test_nebius_key_selects_token_factory_and_nemotron(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "nebius-test-key"
    endpoint = llm_config.resolve_chat_endpoint()
    assert endpoint["provider"] == llm_config.NEBIUS_PROVIDER
    assert endpoint["base_url"] == EXTRACTOR_BASE_URL.rstrip("/")
    assert endpoint["model"] == EXTRACTOR_FALLBACK_MODEL
    assert endpoint["model"].startswith("nvidia/")


def test_nebius_wins_over_an_amd_key(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "nebius-test-key"
    os.environ["VLLM_API_KEY"] = "amd-test-key"
    assert llm_config.resolve_chat_endpoint()["provider"] == llm_config.NEBIUS_PROVIDER


def test_placeholder_nebius_key_is_not_a_key(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "__FILL_ME__"
    assert llm_config.nebius_endpoint() is None
    assert llm_config.resolve_chat_endpoint() is None


def test_amd_path_is_unchanged_without_a_nebius_key(clean_env):
    os.environ["VLLM_API_KEY"] = "amd-test-key"
    endpoint = llm_config.resolve_chat_endpoint()
    assert endpoint["provider"] == llm_config.AMD_PROVIDER
    assert llm_config.body_overrides(endpoint) == {}


def test_health_names_the_provider_without_the_key(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "nebius-test-key"
    status = llm_config.llm_status()
    assert status["live_available"] is True
    assert status["amd_live_available"] is False
    assert status["provider_label"] == "Nebius Token Factory"
    assert "nebius-test-key" not in json.dumps(status)


def test_live_reading_on_nebius_sends_the_reasoning_budget_and_keeps_the_price_out(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "nebius-test-key"
    with patch("src.engine.quote_read.requests.post", return_value=_Response()) as post:
        result = read_supplier_quote(SAMPLE, amd_live=True)
    post.assert_called_once()
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    headers = post.call_args.kwargs["headers"]
    assert url == EXTRACTOR_BASE_URL.rstrip("/") + "/chat/completions"
    assert headers["Authorization"] == "Bearer nebius-test-key"
    assert body["model"] == EXTRACTOR_FALLBACK_MODEL
    assert body["max_tokens"] == int(EXTRACTOR_MAX_OUTPUT_TOKENS)
    assert body["reasoning_budget"] == int(EXTRACTOR_REASONING_BUDGET)
    assert result["status"] == "ok"
    assert result["provider"] == llm_config.NEBIUS_PROVIDER
    assert result["display"].startswith("Nebius Token Factory · " + EXTRACTOR_FALLBACK_MODEL + " · ")
    assert result["buyer_price"] is None
    assert result["decides_price"] is False
    assert "nebius-test-key" not in json.dumps(result)


def test_live_reading_failure_on_nebius_falls_back_without_naming_amd(clean_env):
    os.environ["TRADEFLOW_LLM_API_KEY"] = "nebius-test-key"

    class _Down(_Response):
        status_code = 503

        def json(self):
            return {}

    with patch("src.engine.quote_read.requests.post", return_value=_Down()):
        result = read_supplier_quote(SAMPLE, amd_live=True)
    assert result["status"] == "failed"
    assert "AMD" not in result["display"]
    assert result["buyer_price"] is None


def test_dashboard_copy_does_not_name_amd_for_the_live_switch():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    page = (root / "src/web/templates/index.html").read_text(encoding="utf-8")
    script = (root / "src/web/static/js/app.js").read_text(encoding="utf-8")
    assert "AMD live mode" not in page
    assert "Asking AMD GPU Cloud" not in script
    assert 'text.indexOf("Nebius Token Factory · ") === 0' in script


def test_reader_prompt_defines_every_class():
    """The model gets a meaning for each class, not just its name (a live call
    labelled the supplier's own offer as our_price_echo with names only)."""
    from src.desk.extract import _SYSTEM, CLASSES

    for name in CLASSES:
        assert f"{name} is " in _SYSTEM, name
    assert "including one stated right after another seller's price" in _SYSTEM
    assert "Do not include a price, amount, value, or number field." in _SYSTEM


def test_extractor_budget_is_the_sum_of_its_hops():
    from src.config.heuristics import (
        EXTRACTOR_TIMEOUT_BUDGET_SECONDS,
        EXTRACTOR_TIMEOUT_NVIDIA_SECONDS,
        EXTRACTOR_TIMEOUT_SUPER_SECONDS,
        EXTRACTOR_TIMEOUT_ULTRA_SECONDS,
    )

    hops = EXTRACTOR_TIMEOUT_ULTRA_SECONDS + EXTRACTOR_TIMEOUT_SUPER_SECONDS + EXTRACTOR_TIMEOUT_NVIDIA_SECONDS
    assert EXTRACTOR_TIMEOUT_BUDGET_SECONDS == hops
    # The first hop is longer than the 11.2 s the first live Super read took.
    assert EXTRACTOR_TIMEOUT_ULTRA_SECONDS >= 2 * 12
