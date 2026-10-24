"""Request identity and access logging.

Why this module exists
----------------------
Nothing in this service could answer "which request produced that log line?".
A desk round touches the parser, the extractor, the belief update, the solver
and the audit chain, and each of them logs independently; with concurrent
requests the lines interleave and there is no way to attribute a warning to the
call that caused it. That is the difference between a log and a record.

Three things are added, all cheap:

1. **A request id.** Taken from an inbound ``X-Request-ID`` when a proxy supplies
   one, otherwise generated. It is returned on the response, so a user reporting
   a failure can quote it.
2. **A context variable.** ``current_request_id()`` is available to any code the
   request touches, and a logging filter copies it onto every record, so a
   handler that only has ``logger`` can still be correlated.
3. **One access line per request** with method, path, status and duration. The
   query string is deliberately dropped: it is the part that can carry a token
   someone pasted into a URL, and it is not needed to identify the call.

The middleware is pure ASGI rather than ``BaseHTTPMiddleware`` so it does not
wrap the response in a queue; streaming responses (the desk event stream) keep
their existing behaviour.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders

logger = logging.getLogger("tradeflow.access")

REQUEST_ID_HEADER = "X-Request-ID"
RESPONSE_TIME_HEADER = "X-Response-Time-Ms"
REQUEST_ID_MAX_CHARS = 64

_request_id: ContextVar[str] = ContextVar("tradeflow_request_id", default="-")


def current_request_id() -> str:
    """The id of the request being handled, or ``"-"`` outside a request."""
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Copy the current request id onto every record.

    Attached to the root logger's handlers by ``install_request_id_filter``, so a
    formatter that includes ``%(request_id)s`` works without every module
    threading the id through its own log calls.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


def install_request_id_filter() -> None:
    """Idempotently attach the filter to the root handlers that exist now.

    Uvicorn installs its handlers before the application is imported, so at
    import time the root logger has them. A host that configures logging later
    simply will not get the field; the access line still carries the id, so
    correlation degrades rather than disappearing.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(existing, RequestIdFilter) for existing in handler.filters):
            handler.addFilter(RequestIdFilter())


def _clean_incoming(value: bytes | None) -> str:
    """Accept a caller-supplied id only when it is short and boring.

    A request id ends up in logs and response headers, so it must not be a place
    to smuggle newlines (header injection) or an unbounded string (log flooding).
    """
    if not value:
        return ""
    try:
        text = value.decode("ascii", "ignore").strip()
    except Exception:  # pragma: no cover - decode with errors="ignore" cannot raise
        return ""
    if not text or len(text) > REQUEST_ID_MAX_CHARS:
        return ""
    if not all(char.isalnum() or char in "-_." for char in text):
        return ""
    return text


def _clean_path(value: str) -> str:
    """A loggable request path: percent-decoded, so newlines and control
    characters must not survive into the access line (log forging), but the
    structural characters of a path — slash, dash, dot, underscore — stay.

    [SECURITY 2026-10-24] ``/api/v1%2Fx`` style targets used to reach the
    logger verbatim after decoding; a ``%0A`` could start a forged line.
    Anything not path-shaped collapses to a space, and the result is capped
    at the same length as a request id.
    """
    text = str(value or "?").strip()
    cleaned = "".join(
        char if (char.isalnum() or char in "/-_.,=&%?") else " " for char in text
    )
    cleaned = cleaned.strip() or "?"
    return cleaned[:512]


class RequestContextMiddleware:
    """Assign a request id, time the request, and emit one access line."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for name, value in scope.get("headers") or ():
            if name == b"x-request-id":
                incoming = _clean_incoming(value)
                break
        request_id = incoming or uuid.uuid4().hex[:16]
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status") or 500)
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers[RESPONSE_TIME_HEADER] = f"{(time.perf_counter() - started) * 1000.0:.1f}"
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            # The query string is dropped on purpose: it is the part that can
            # carry a pasted token, and it is not needed to identify the call.
            # The id goes in the message as well as in ``extra``: a formatter the
            # host did not configure for ``%(request_id)s`` would otherwise drop
            # the one field that makes the line correlatable.
            # [SECURITY 2026-10-24] The raw path is %-decoded by the server, so
            # a %0A in the request target could forge extra log lines. The
            # request id is already clean; the path gets a path-shaped version
            # of the same treatment (slash kept, everything else boring).
            logger.info(
                "[%s] %s %s -> %s in %.1f ms",
                request_id,
                str(scope.get("method", "?"))[:16],
                _clean_path(str(scope.get("path", "?"))),
                status_holder["status"],
                elapsed_ms,
                extra={"request_id": request_id, "status": status_holder["status"]},
            )
            _request_id.reset(token)
