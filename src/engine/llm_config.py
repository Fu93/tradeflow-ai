"""Resolve optional LLM credentials without treating placeholders as real.

``.env.example`` ships ``__FILL_ME__`` so ``cp .env.example .env`` can boot the
offline demo. Those placeholders are truthy strings. Callers must not send
them as bearer tokens, and must not send an Evolus key to the AMD host.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

_PLACEHOLDER_EXACT = {
    "none",
    "null",
    "changeme",
    "todo",
    "xxx",
    "replace_me",
    "your_key_here",
    "your-key-here",
}

AMD_TOKENFACTORY_BASE = "https://developer.amd.com.cn/radeon/api/v1"
NEBIUS_PROVIDER = "nebius_token_factory"
AMD_PROVIDER = "amd_vllm"
# What the live quote reader says it used. The UI trusts a reading as live only
# when its display line starts with one of these.
PROVIDER_LABELS = {NEBIUS_PROVIDER: "Nebius Token Factory", AMD_PROVIDER: "AMD GPU Cloud"}
EVOLUS_CHAT_BASE = "https://api.evolus.ai/v1"

# New Stage-2 calls use Gemma. Qwen3.8-Flash-Next stays available by setting
# VLLM_MODEL, and it is the model named in results/real_llm_benchmark_100.json.
# That log is not a Gemma measurement.
DEFAULT_STAGE2_MODEL = "Gemma-4-31B-IT"
ALTERNATIVE_STAGE2_MODEL = "Qwen3.8-Flash-Next"


def real_secret(value: str | None) -> str | None:
    """Return a usable credential, or None for empty values and placeholders."""
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    if text.startswith("__FILL") or text.startswith("__CHANGEME"):
        return None
    if text.lower() in _PLACEHOLDER_EXACT:
        return None
    return text


def _real_url(value: str | None) -> str | None:
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return None
    if text.startswith("__FILL") or text.lower() in _PLACEHOLDER_EXACT:
        return None
    return text.rstrip("/")


def nebius_endpoint() -> dict | None:
    """Nebius Token Factory with a Nemotron model, when its key is set.

    The same key the desk quote reader uses (``TRADEFLOW_LLM_API_KEY``, or
    ``OPENAI_API_KEY``). The model is the Super model, the desk reader's second
    hop, because this path answers a person waiting on the dashboard.
    """
    from src.config.heuristics import EXTRACTOR_BASE_URL, EXTRACTOR_FALLBACK_MODEL

    key = real_secret(os.getenv("TRADEFLOW_LLM_API_KEY")) or real_secret(os.getenv("OPENAI_API_KEY"))
    if not key:
        return None
    base = _real_url(os.getenv("TRADEFLOW_LLM_BASE_URL")) or EXTRACTOR_BASE_URL.rstrip("/")
    model = (os.getenv("TRADEFLOW_DASHBOARD_MODEL") or EXTRACTOR_FALLBACK_MODEL).strip() or EXTRACTOR_FALLBACK_MODEL
    return {"base_url": base, "api_key": key, "model": model, "provider": NEBIUS_PROVIDER}


_budget_lock = threading.Lock()
_budget = {"day": "", "used": 0}


def daily_call_cap() -> int:
    """Paid model calls allowed per UTC day in this process."""
    from src.config.heuristics import LLM_DAILY_CALL_CAP

    raw = (os.getenv("TRADEFLOW_LLM_DAILY_CALLS") or "").strip()
    if raw.isdigit():
        return int(raw)
    return int(LLM_DAILY_CALL_CAP)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


_pause_lock = threading.Lock()
_pause: dict = {"until": 0.0, "http_status": None, "since": None}


def note_model_refusal(http_status: int | None) -> bool:
    """Pause paid calls when the service refuses the account (no credit, key refused).

    Returns True when ``http_status`` is such a refusal. Calls stay paused for
    ``LLM_ACCOUNT_PAUSE_SECONDS``; a restart clears the pause.
    """
    from src.config.heuristics import LLM_ACCOUNT_PAUSE_SECONDS, LLM_ACCOUNT_REFUSAL_STATUSES

    if http_status is None or int(http_status) not in {int(code) for code in LLM_ACCOUNT_REFUSAL_STATUSES}:
        return False
    with _pause_lock:
        _pause["until"] = time.time() + float(LLM_ACCOUNT_PAUSE_SECONDS)
        _pause["http_status"] = int(http_status)
        _pause["since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return True


def model_calls_paused() -> dict | None:
    """The active pause after an account refusal, or None. Non-secret."""
    with _pause_lock:
        if _pause["until"] <= time.time():
            return None
        until = datetime.fromtimestamp(_pause["until"], timezone.utc).isoformat(timespec="seconds")
        return {"http_status": _pause["http_status"], "since": _pause["since"], "until": until}


def take_model_call() -> bool:
    """Count one paid call. False while paused after an account refusal, or once
    today's cap is spent; the caller stays offline. A paused call is not counted."""
    if model_calls_paused() is not None:
        return False
    cap = daily_call_cap()
    with _budget_lock:
        day = _today()
        if _budget["day"] != day:
            _budget["day"] = day
            _budget["used"] = 0
        if _budget["used"] >= cap:
            return False
        _budget["used"] += 1
        return True


def model_calls_today() -> dict:
    """Non-secret counter for /health: calls used today and the cap."""
    with _budget_lock:
        used = _budget["used"] if _budget["day"] == _today() else 0
    return {"used": used, "cap": daily_call_cap(), "paused": model_calls_paused()}


def reset_model_budget() -> None:
    """Tests only."""
    with _budget_lock:
        _budget["day"] = ""
        _budget["used"] = 0
    with _pause_lock:
        _pause.update({"until": 0.0, "http_status": None, "since": None})


def body_overrides(endpoint: dict | None) -> dict:
    """Request-body fields a provider needs on top of the caller's own.

    Nemotron reasons before it answers, and the reasoning shares the output
    budget, so a small ``max_tokens`` truncates the JSON. On Nebius the desk
    reader's output floor and reasoning cap apply. Other providers: nothing.
    """
    if not endpoint or endpoint.get("provider") != NEBIUS_PROVIDER:
        return {}
    from src.config.heuristics import EXTRACTOR_MAX_OUTPUT_TOKENS, EXTRACTOR_REASONING_BUDGET

    return {
        "max_tokens": int(EXTRACTOR_MAX_OUTPUT_TOKENS),
        "reasoning_budget": int(EXTRACTOR_REASONING_BUDGET),
    }


def resolve_chat_endpoint(default_model: str = DEFAULT_STAGE2_MODEL) -> dict | None:
    """OpenAI-compatible chat target, or None when nothing usable is configured.

    Nebius Token Factory wins when its key is set (this is the hackathon path).

    A real ``VLLM_API_KEY`` wins and is paired with ``VLLM_BASE_URL`` (the AMD
    tokenfactory default when the URL is unset). An Evolus key is used only
    when the configured base URL is absent or is an evolus.ai host. An Evolus
    key is never sent to the AMD host.
    """
    nebius = nebius_endpoint()
    if nebius is not None:
        return nebius
    vllm_key = real_secret(os.getenv("VLLM_API_KEY"))
    vllm_base = _real_url(os.getenv("VLLM_BASE_URL"))
    evolus_key = real_secret(os.getenv("EVOLUS_API_KEY"))
    model = (os.getenv("VLLM_MODEL") or default_model).strip() or default_model

    if vllm_key:
        return {
            "base_url": vllm_base or AMD_TOKENFACTORY_BASE,
            "api_key": vllm_key,
            "model": model,
            "provider": AMD_PROVIDER,
        }
    if evolus_key and (vllm_base is None or "evolus.ai" in vllm_base):
        return {
            "base_url": vllm_base or EVOLUS_CHAT_BASE,
            "api_key": evolus_key,
            "model": model,
            "provider": "evolus",
        }
    return None


def evolus_credentials() -> tuple[str, str] | None:
    key = real_secret(os.getenv("EVOLUS_API_KEY"))
    app_id = real_secret(os.getenv("EVOLUS_APP_ID"))
    if key and app_id:
        return key, app_id
    return None


def configured_model() -> str:
    """Model name a new call would send. Not a measurement, and not a secret."""
    return (os.getenv("VLLM_MODEL") or DEFAULT_STAGE2_MODEL).strip() or DEFAULT_STAGE2_MODEL


def llm_status() -> dict:
    """Non-secret status for /health. Never includes credential values."""
    chat = resolve_chat_endpoint()
    return {
        "configured": chat is not None,
        "provider": None if chat is None else chat["provider"],
        "model": None if chat is None else chat["model"],
        "default_model": DEFAULT_STAGE2_MODEL,
        "offline_demo": chat is None,
        "amd_live_available": chat is not None and chat["provider"] == AMD_PROVIDER,
        "live_available": chat is not None,
        "model_calls_today": model_calls_today(),
        "provider_label": None if chat is None else PROVIDER_LABELS.get(chat["provider"], chat["provider"]),
    }
