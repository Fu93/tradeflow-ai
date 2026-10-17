#!/usr/bin/env python3
"""Probe the AMD tokenfactory / vLLM-compatible endpoint and write evidence.

Reads VLLM_BASE_URL, VLLM_API_KEY and VLLM_MODEL. Placeholder values from
.env.example (``__FILL_ME__``) count as "not configured".

The API key is never printed and never written to the evidence file.

Exit status:
  0  skipped (no usable key) or the probe succeeded
  1  a key was configured and the endpoint did not return HTTP 200

A skip is not evidence that the endpoint works. Only ``status: ok`` is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.engine.llm_config import AMD_TOKENFACTORY_BASE, real_secret  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(url: str, key: str, payload: dict | None, timeout: float) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(65536).decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", errors="replace")
        return int(exc.code), body


def probe(chat: bool, timeout: float) -> dict:
    key = real_secret(os.getenv("VLLM_API_KEY"))
    base = (os.getenv("VLLM_BASE_URL") or AMD_TOKENFACTORY_BASE).strip().rstrip("/")
    model = (os.getenv("VLLM_MODEL") or "Qwen3.8-Flash-Next").strip()
    evidence = {
        "generated_at_utc": _utc_now(),
        "status": "skipped_no_credentials",
        "base_url": base,
        "model": model,
        "key_present": bool(key),
        "models_http_status": None,
        "model_count": None,
        "model_ids_sample": [],
        "chat_attempted": bool(chat),
        "chat_http_status": None,
        "chat_latency_ms": None,
        "error": None,
        "disclaimer": (
            "Produced by scripts/verify_amd_endpoint.py. "
            "status=skipped_no_credentials is not a successful AMD check. "
            "status=ok means this process received HTTP 200 from the configured "
            "OpenAI-compatible endpoint. It does not by itself prove which GPU "
            "served the request."
        ),
    }
    if not key:
        evidence["error"] = "VLLM_API_KEY is unset or still a placeholder."
        return evidence

    status, body = _request(f"{base}/models", key, None, timeout)
    evidence["models_http_status"] = status
    if status != 200:
        evidence["status"] = "failed"
        evidence["error"] = f"GET /models returned HTTP {status}: {body[:300]}"
        return evidence
    try:
        parsed = json.loads(body)
        rows = parsed.get("data") if isinstance(parsed, dict) else None
        if isinstance(rows, list):
            evidence["model_count"] = len(rows)
            ids = []
            for row in rows[:12]:
                if isinstance(row, dict) and row.get("id"):
                    ids.append(str(row["id"]))
            evidence["model_ids_sample"] = ids
    except json.JSONDecodeError:
        evidence["error"] = "GET /models returned HTTP 200 but the body was not JSON."

    if chat:
        import time

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word ok."}],
            "temperature": 0,
            "max_tokens": 16,
        }
        started = time.perf_counter()
        chat_status, chat_body = _request(f"{base}/chat/completions", key, payload, timeout)
        evidence["chat_latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        evidence["chat_http_status"] = chat_status
        if chat_status != 200:
            evidence["status"] = "failed"
            evidence["error"] = f"POST /chat/completions returned HTTP {chat_status}: {chat_body[:300]}"
            return evidence

    evidence["status"] = "ok"
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        default=os.path.join(ROOT, "results", "amd_endpoint_evidence.json"),
        help="Where to write the evidence JSON. The API key is not included.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Also send one short chat completion. This spends tokens and can take tens of seconds.",
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("VLLM_TIMEOUT_SECONDS", "90")))
    args = parser.parse_args(argv)

    # Load .env when present so `cp .env.example .env` is enough. override=False
    # keeps a real environment variable ahead of the file.
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(ROOT, ".env"), override=False)
    except Exception:
        pass

    evidence = probe(chat=args.chat, timeout=args.timeout)
    os.makedirs(os.path.dirname(os.path.abspath(args.evidence)), exist_ok=True)
    with open(args.evidence, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2)
        fh.write("\n")

    print(f"status: {evidence['status']}")
    print(f"base_url: {evidence['base_url']}")
    print(f"key_present: {evidence['key_present']}")
    if evidence.get("models_http_status") is not None:
        print(f"models_http_status: {evidence['models_http_status']}")
    if evidence.get("model_count") is not None:
        print(f"model_count: {evidence['model_count']}")
    if evidence.get("error"):
        print(f"error: {evidence['error']}")
    print(f"evidence: {args.evidence}")
    print(evidence["disclaimer"])
    return 0 if evidence["status"] in ("ok", "skipped_no_credentials") else 1


if __name__ == "__main__":
    sys.exit(main())
