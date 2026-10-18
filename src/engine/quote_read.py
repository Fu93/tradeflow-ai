"""Read a supplier quote. The solver is not called from here.

AMD live mode asks the configured OpenAI-compatible model for fields and a
short reading. Offline mode uses the deterministic reader. Either way
``buyer_price`` stays null: this module does not choose what we pay.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

from src.engine.llm_config import resolve_chat_endpoint
from src.engine.quote_fields import ACCURACY_NOTE, extract_stage1, validate_fields

OFFLINE_LABEL = (
    "Offline fallback — AMD live mode is off or VLLM_API_KEY is not a real secret. "
    "Fields below are the deterministic reader, not a model call."
)
TIMEOUT_LABEL = (
    "Offline fallback — the AMD call timed out. "
    "Fields below are the deterministic reader, not a model call."
)
FAILED_LABEL = (
    "Offline fallback — the AMD call did not return a usable reading. "
    "Fields below are the deterministic reader, not a model call."
)
NEEDS_TEXT_LABEL = (
    "This desk does not OCR pixels offline. Paste the chat text, or pick a labeled "
    "sample. The sample transcript is what the picture shows; it is a simulation, "
    "not a vision read."
)

_PROMPT = (
    "You extract supplier quote fields. Reply with one JSON object and no other text. "
    "Keys: price (number or null), moq (integer or null), lead_time_days (integer or null), "
    "deposit_pct (percent from 0 to 100, or null), reading (one sentence about what the "
    "supplier said). Do not propose the buyer's counter-offer. Do not invent a field you "
    "cannot see.\n"
    "Message:\n"
)


def live_timeout_seconds() -> int:
    raw = os.getenv("AMD_LIVE_TIMEOUT_SECONDS", "20")
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 20
    if value < 1:
        return 1
    if value > 90:
        return 90
    return value


def _redact(text: str) -> str:
    cleaned = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted]", cleaned)
    return cleaned[:180]


def _tokens(usage: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    prompt_n = prompt if isinstance(prompt, int) else None
    completion_n = completion if isinstance(completion, int) else None
    if isinstance(total, int):
        total_n = total
    elif prompt_n is not None and completion_n is not None:
        total_n = prompt_n + completion_n
    else:
        total_n = None
    return prompt_n, completion_n, total_n


def display_line(model: str, latency_ms: int | None, total_tokens: int | None) -> str:
    latency = "latency unavailable" if latency_ms is None else f"{latency_ms} ms"
    tokens = "tokens unavailable" if total_tokens is None else f"{total_tokens} tokens"
    return f"AMD GPU Cloud · {model} · {latency} · {tokens}"


def _currency_view(stage1: dict) -> dict:
    return {
        "quoted_amount": stage1.get("quoted_amount"),
        "currency": stage1.get("currency"),
        "currency_note": stage1.get("currency_note"),
    }


def _base_result(text: str, *, status: str, label: str) -> dict:
    stage1 = extract_stage1(text)
    fields = validate_fields(stage1)
    shown = {key: fields[key] for key in ("price", "moq", "lead_time_days", "deposit_pct")}
    shown.update(_currency_view(stage1))
    return {
        "status": status,
        "display": label,
        "model": None,
        "provider": None,
        "latency_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "fields": shown,
        "rejected": fields["rejected"],
        "reading": None,
        "buyer_price": None,
        "decides_price": False,
        "image_used": False,
        "accuracy_note": ACCURACY_NOTE,
    }


def _parse_model_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _apply_model_fields(result: dict, parsed: dict) -> None:
    # A model key that names our price is discarded. It is not returned.
    for banned in ("our_price", "buyer_price", "recommended_price", "counter_price"):
        if banned in parsed:
            result["discarded_buyer_price"] = True
            parsed.pop(banned, None)
    validated = validate_fields(parsed)
    stage = result["fields"]
    # Stage-1 already refused to treat this number as CNY. A model price
    # must not put it back.
    if stage.get("currency") not in (None, "CNY") or stage.get("currency_note"):
        validated["price"] = stage.get("price")
    shown = {key: validated[key] for key in ("price", "moq", "lead_time_days", "deposit_pct")}
    shown.update(_currency_view(stage))
    result["fields"] = shown
    result["rejected"] = validated["rejected"]
    reading = parsed.get("reading") or parsed.get("tactical_rationale")
    if isinstance(reading, str) and reading.strip():
        result["reading"] = reading.strip()[:500]


def _post_chat(endpoint: dict, content: list | str, timeout: int) -> tuple[int, dict | None, str]:
    response = requests.post(
        f"{endpoint['base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {endpoint['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": endpoint["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": int(os.getenv("VLLM_MAX_TOKENS", "500")),
        },
        timeout=timeout,
    )
    body_text = response.text or ""
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body if isinstance(body, dict) else None, body_text


def read_supplier_quote(
    text: str,
    *,
    amd_live: bool,
    image_base64: str | None = None,
) -> dict:
    """Extract fields. Never returns a buyer price."""
    transcript = (text or "").strip()
    image = (image_base64 or "").strip()
    if image.startswith("data:"):
        comma = image.find(",")
        image = image[comma + 1 :] if comma != -1 else ""
    if len(image) > 1_500_000:
        image = ""

    if not transcript and not image:
        result = _base_result("", status="needs_text", label=NEEDS_TEXT_LABEL)
        return result

    if not transcript and image and not amd_live:
        result = _base_result("", status="needs_text", label=NEEDS_TEXT_LABEL)
        return result

    if not amd_live:
        result = _base_result(transcript, status="offline_fallback", label=OFFLINE_LABEL)
        return result

    endpoint = resolve_chat_endpoint()
    if endpoint is None:
        if not transcript:
            return _base_result("", status="needs_text", label=NEEDS_TEXT_LABEL)
        result = _base_result(transcript, status="offline_fallback", label=OFFLINE_LABEL)
        return result

    timeout = live_timeout_seconds()
    if transcript:
        content: list | str = _PROMPT + transcript
        image_used = False
    else:
        content = [
            {"type": "text", "text": _PROMPT + "(no transcript; read the image if you can)"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}},
        ]
        image_used = True

    started = time.perf_counter()
    try:
        status, body, body_text = _post_chat(endpoint, content, timeout)
    except requests.Timeout:
        result = _base_result(transcript, status="timeout", label=TIMEOUT_LABEL)
        result["model"] = endpoint["model"]
        result["provider"] = endpoint["provider"]
        result["image_used"] = image_used
        result["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
        return result
    except requests.RequestException as exc:
        result = _base_result(transcript, status="failed", label=FAILED_LABEL)
        result["model"] = endpoint["model"]
        result["provider"] = endpoint["provider"]
        result["image_used"] = image_used
        result["error"] = _redact(f"{type(exc).__name__}")
        result["latency_ms"] = int(round((time.perf_counter() - started) * 1000))
        return result

    latency_ms = int(round((time.perf_counter() - started) * 1000))
    if not transcript:
        result = _base_result("", status="failed", label=FAILED_LABEL)
    else:
        result = _base_result(transcript, status="failed", label=FAILED_LABEL)
    result["model"] = endpoint["model"]
    result["provider"] = endpoint["provider"]
    result["latency_ms"] = latency_ms
    result["image_used"] = image_used

    if status != 200 or not isinstance(body, dict):
        result["error"] = _redact(f"HTTP {status}")
        return result

    usage_prompt, usage_completion, usage_total = _tokens(body.get("usage"))
    result["prompt_tokens"] = usage_prompt
    result["completion_tokens"] = usage_completion
    result["total_tokens"] = usage_total
    try:
        message = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        result["error"] = "no message content"
        return result
    parsed = _parse_model_json(message if isinstance(message, str) else "")
    if parsed is None:
        result["error"] = "no parsable JSON"
        return result
    _apply_model_fields(result, parsed)
    result["status"] = "ok"
    result["display"] = display_line(endpoint["model"], latency_ms, usage_total)
    result["buyer_price"] = None
    result["decides_price"] = False
    return result
