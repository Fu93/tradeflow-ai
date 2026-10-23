"""Request-size limits, enforced before the body is buffered.

Why this module exists
----------------------
FastAPI/Pydantic validate field sizes *after* the ASGI server has already read
and buffered the whole body. A single 500 MB JSON POST therefore allocates
500 MB of memory before any validator sees it, which is enough to OOM a small
instance. ``Content-Length`` is advisory: a client may omit it or send a
chunked body, so the limit has to be enforced twice:

1. Fast path — reject on the declared ``Content-Length`` without reading.
2. Guarantee — count bytes as they stream and abort the moment the running
   total exceeds the limit.

Both paths answer ``413`` with a JSON body so callers get a parseable error
instead of a dropped connection.

The limit is deliberately not a business rule: it is a resource bound. The
quote text itself is separately capped by ``EXTRACTOR_MAX_BYTES``.
"""
from __future__ import annotations

import json
import os

DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024  # 1 MiB
MAX_REQUEST_BYTES_ENV = "TRADEFLOW_MAX_REQUEST_BYTES"


def max_request_bytes() -> int:
    """Configured ceiling. Invalid or non-positive values fall back to the default."""
    raw = os.getenv(MAX_REQUEST_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REQUEST_BYTES
    return value if value > 0 else DEFAULT_MAX_REQUEST_BYTES


def _too_large(limit: int) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    payload = json.dumps(
        {
            "detail": f"Request body exceeds the {limit}-byte limit.",
            "limit_bytes": limit,
        }
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
        (b"connection", b"close"),
    ]
    return payload, headers


class BodySizeLimitMiddleware:
    """Pure-ASGI middleware. Rejects oversized bodies with 413.

    Pure ASGI (rather than ``BaseHTTPMiddleware``) so the receive channel can be
    wrapped directly; ``BaseHTTPMiddleware`` buffers through a queue and would
    defeat the purpose of the guard.
    """

    def __init__(self, app, limit: int | None = None) -> None:
        self.app = app
        self._fixed_limit = limit

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = self._fixed_limit if self._fixed_limit is not None else max_request_bytes()

        declared = None
        for key, value in scope.get("headers") or ():
            if key == b"content-length":
                try:
                    declared = int(value)
                except (TypeError, ValueError):
                    declared = None
                break
        if declared is not None and declared > limit:
            payload, headers = _too_large(limit)
            await send({"type": "http.response.start", "status": 413, "headers": headers})
            await send({"type": "http.response.body", "body": payload})
            return

        seen = 0
        started = False

        async def tracked_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > limit:
                    raise _BodyTooLarge(limit)
            return message

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyTooLarge:
            if started:
                # The handler already committed a response; it owns the socket.
                # Re-raising would corrupt the protocol, so the stream is simply
                # abandoned — the handler never received the oversized body.
                raise
            payload, headers = _too_large(limit)
            await send({"type": "http.response.start", "status": 413, "headers": headers})
            await send({"type": "http.response.body", "body": payload})


class _BodyTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"request body exceeds {limit} bytes")
        self.limit = limit
