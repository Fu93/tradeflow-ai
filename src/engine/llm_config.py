"""Resolve optional LLM credentials without treating placeholders as real.

``.env.example`` ships ``__FILL_ME__`` so ``cp .env.example .env`` can boot the
offline demo. Those placeholders are truthy strings. Callers must not send
them as bearer tokens, and must not send an Evolus key to the AMD host.
"""
from __future__ import annotations

import os

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


def resolve_chat_endpoint(default_model: str = DEFAULT_STAGE2_MODEL) -> dict | None:
    """OpenAI-compatible chat target, or None when nothing usable is configured.

    A real ``VLLM_API_KEY`` wins and is paired with ``VLLM_BASE_URL`` (the AMD
    tokenfactory default when the URL is unset). An Evolus key is used only
    when the configured base URL is absent or is an evolus.ai host. An Evolus
    key is never sent to the AMD host.
    """
    vllm_key = real_secret(os.getenv("VLLM_API_KEY"))
    vllm_base = _real_url(os.getenv("VLLM_BASE_URL"))
    evolus_key = real_secret(os.getenv("EVOLUS_API_KEY"))
    model = (os.getenv("VLLM_MODEL") or default_model).strip() or default_model

    if vllm_key:
        return {
            "base_url": vllm_base or AMD_TOKENFACTORY_BASE,
            "api_key": vllm_key,
            "model": model,
            "provider": "amd_vllm",
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
        "amd_live_available": chat is not None and chat["provider"] == "amd_vllm",
    }
