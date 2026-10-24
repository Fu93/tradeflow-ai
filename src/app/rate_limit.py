"""Write-path rate limiting.

Two limiters live here, deliberately separate:

``AmdRateLimiter``
    The original: a per-IP per-minute cap plus a global per-hour cap, applied
    only to the routes that can spend AMD token-factory credit. Its job is cost
    control on a metered upstream, and its semantics are pinned by
    ``tests/test_amd_live.py``.

``SlidingWindowLimiter`` / ``write_rate_limiter``
    Added 2026-10-23. The rest of the API had no limit at all: a single client
    could loop ``POST /api/v1/desk/deals/{id}/quotes`` and each call runs the
    extractor, the belief update and the solver. The limiter is keyed on the API
    key when one is presented and on the client address otherwise, so one tenant
    cannot starve another, and it covers every mutating request under ``/api``.

Both are in-process and therefore per-instance. That is stated rather than
hidden: a restart clears the window, and a second replica has its own window.
A shared counter needs a shared store, and adding Redis to a single-instance
deployment to bound abuse would be a larger operational commitment than the
problem warrants today. The honest description is "abuse cap for this process",
not "distributed rate limit".

The window is read from the environment on every check, not at import, so a test
or an operator can change it without restarting and without patching a module
global.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Iterable

from fastapi import HTTPException, Request

PER_MINUTE = 8
PER_HOUR = 30
MINUTE_SECONDS = 60
HOUR_SECONDS = 3600

# [MEMORY 2026-10-24] Cap on tracked limiter keys (see SlidingWindowLimiter):
# once the table runs past this, fully-aged keys are swept and the
# least-recently-active keys are evicted on the next check.
_MAX_TRACKED_KEYS = 10_000

# Defaults for the write limiter. Chosen to be far above anything a human desk
# produces (a busy buyer confirms a handful of quotes a minute) and far below
# what a loop can generate, so it never fires on real use.
WRITES_PER_MINUTE_DEFAULT = 600
WRITES_PER_HOUR_DEFAULT = 6000
RATE_LIMIT_ENV = "TRADEFLOW_RATE_LIMIT"
WRITES_PER_MINUTE_ENV = "TRADEFLOW_WRITES_PER_MINUTE"
WRITES_PER_HOUR_ENV = "TRADEFLOW_WRITES_PER_HOUR"

# Methods that do not change state. Everything else under /api is counted.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths that are never counted: probes and the browser surfaces.
EXEMPT_PREFIXES = ("/health", "/ready", "/favicon", "/desk", "/static", "/web")


class AmdRateLimiter:
    def __init__(self, per_minute: int = PER_MINUTE, per_hour: int = PER_HOUR) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._lock = threading.Lock()
        self._by_ip: dict[str, list[float]] = {}
        self._global: list[float] = []

    def reset(self, per_minute: int = PER_MINUTE, per_hour: int = PER_HOUR) -> None:
        with self._lock:
            self.per_minute = per_minute
            self.per_hour = per_hour
            self._by_ip.clear()
            self._global.clear()

    def check(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent_ip = [stamp for stamp in self._by_ip.get(ip, []) if now - stamp < MINUTE_SECONDS]
            recent_all = [stamp for stamp in self._global if now - stamp < HOUR_SECONDS]
            if len(recent_ip) >= self.per_minute or len(recent_all) >= self.per_hour:
                raise HTTPException(
                    status_code=429,
                    detail="Too many AMD live requests. Wait and retry.",
                )
            recent_ip.append(now)
            recent_all.append(now)
            self._by_ip[ip] = recent_ip
            self._global = recent_all
            # [MEMORY 2026-10-24] The socket IP is caller-chosen only up to the
            # internet's patience, but a proxy farm can still mint many of
            # them; sweep fully-aged entries once the table runs past its cap
            # so the table cannot grow without limit.
            if len(self._by_ip) > _MAX_TRACKED_KEYS:
                aged = {
                    tracked: stamps
                    for tracked, stamps in self._by_ip.items()
                    if stamps and now - stamps[-1] < HOUR_SECONDS
                }
                while len(aged) > _MAX_TRACKED_KEYS:
                    victim = min(
                        (name for name in aged if name != ip),
                        key=lambda name: aged[name][-1],
                        default=None,
                    )
                    if victim is None:
                        break
                    aged.pop(victim)
                self._by_ip = aged


amd_rate_limiter = AmdRateLimiter()


class SlidingWindowLimiter:
    """A sliding window over two horizons, keyed by an opaque string.

    Memory is bounded: per-key stamps are pruned on every check, and once the
    key table itself runs past its cap, keys whose every stamp has aged out
    of the hour window are swept. That matters because the key for an
    unauthenticated caller is client-chosen (a hashed header value), so
    without the sweep one random header per request would grow the table
    without limit — a memory DoS that no rate limit ever fires to stop.
    The sweep is the reason this is a sliding window rather than a fixed
    bucket: a fixed bucket lets a caller send ``limit`` requests at the end
    of one window and ``limit`` again at the start of the next, which is
    twice the intended rate at the boundary.
    """

    _MAX_TRACKED_KEYS = 10_000

    def __init__(self, per_minute: int, per_hour: int) -> None:
        self._per_minute = int(per_minute)
        self._per_hour = int(per_hour)
        self._lock = threading.Lock()
        self._by_key: dict[str, list[float]] = {}

    @property
    def per_minute(self) -> int:
        return self._per_minute

    @property
    def per_hour(self) -> int:
        return self._per_hour

    def reset(self) -> None:
        with self._lock:
            self._by_key.clear()

    def remaining(self, key: str) -> tuple[int, int]:
        """(minute_remaining, hour_remaining). Never raises; used by headers."""
        now = time.monotonic()
        with self._lock:
            stamps = [stamp for stamp in self._by_key.get(key, []) if now - stamp < HOUR_SECONDS]
            self._by_key[key] = stamps
            minute = sum(1 for stamp in stamps if now - stamp < MINUTE_SECONDS)
            return max(0, self._per_minute - minute), max(0, self._per_hour - len(stamps))

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            stamps = [stamp for stamp in self._by_key.get(key, []) if now - stamp < HOUR_SECONDS]
            minute = sum(1 for stamp in stamps if now - stamp < MINUTE_SECONDS)
            if minute >= self._per_minute:
                self._by_key[key] = stamps
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Too many write requests: {self._per_minute} per minute is the cap. "
                        "Wait and retry."
                    ),
                )
            if len(stamps) >= self._per_hour:
                self._by_key[key] = stamps
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Too many write requests: {self._per_hour} per hour is the cap. "
                        "Wait and retry."
                    ),
                )
            stamps.append(now)
            self._by_key[key] = stamps
            if len(self._by_key) > self._MAX_TRACKED_KEYS:
                # First sweep fully-aged keys; then — a burst can mint more
                # fresh keys than the age sweep frees — trim the table back
                # to the cap. The one hard invariant: the key that just
                # checked in is never the one trimmed in its own call. Which
                # OTHER fresh key loses its entry is not a fairness question:
                # a trimmed key simply restarts its window if it returns, and
                # an attacker rotating keys loses entries it was never going
                # to reuse. A long-lived quiet key can still be trimmed by a
                # sustained burst — that is the documented cost of bounding
                # memory, and it costs that key one window, not its access.
                aged = {
                    tracked: entries
                    for tracked, entries in self._by_key.items()
                    if entries and now - entries[-1] < HOUR_SECONDS
                }
                while len(aged) > self._MAX_TRACKED_KEYS:
                    # Drop the oldest last-seen entry that is not the caller.
                    victim = min(
                        (name for name in aged if name != key),
                        key=lambda name: aged[name][-1],
                        default=None,
                    )
                    if victim is None:
                        break
                    aged.pop(victim)
                self._by_key = aged


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def writes_enabled() -> bool:
    """Rate limiting is on unless explicitly switched off.

    Fail-safe direction: a missing variable means "limited", not "unlimited".
    """
    return os.environ.get(RATE_LIMIT_ENV, "").strip().lower() not in {"0", "false", "off", "no"}


def writes_per_minute() -> int:
    return _env_int(WRITES_PER_MINUTE_ENV, WRITES_PER_MINUTE_DEFAULT)


def writes_per_hour() -> int:
    return _env_int(WRITES_PER_HOUR_ENV, WRITES_PER_HOUR_DEFAULT)


class _ReloadingLimiter:
    """A limiter whose budget follows the environment between checks.

    The window size is operational configuration, not a compile-time decision;
    reading it per call means a test can tighten it and an operator can raise it
    without a restart. When the limits change the table is cleared, because the
    old timestamps were counted against a different budget.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inner = SlidingWindowLimiter(writes_per_minute(), writes_per_hour())

    def _current(self) -> SlidingWindowLimiter:
        per_minute, per_hour = writes_per_minute(), writes_per_hour()
        with self._lock:
            if (self._inner.per_minute, self._inner.per_hour) != (per_minute, per_hour):
                self._inner = SlidingWindowLimiter(per_minute, per_hour)
            return self._inner

    def check(self, key: str) -> None:
        self._current().check(key)

    def remaining(self, key: str) -> tuple[int, int]:
        return self._current().remaining(key)

    def reset(self) -> None:
        with self._lock:
            self._inner = SlidingWindowLimiter(writes_per_minute(), writes_per_hour())


write_rate_limiter = _ReloadingLimiter()


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def caller_key(headers: Iterable[tuple[bytes, bytes]], client: str) -> str:
    """Identify the caller for limiting purposes.

    The API key, when present, is hashed rather than stored: the limiter table is
    process memory, but a raw key sitting in a dict is still a key sitting in a
    dict. Eight bytes of a SHA-256 is plenty to distinguish callers and useless
    to anyone reading a heap dump for the key itself.
    """
    for name, value in headers:
        if name.lower() == b"x-api-key" and value:
            digest = hashlib.sha256(value).hexdigest()[:16]
            return f"key:{digest}"
    return f"ip:{client}"


def is_counted(method: str, path: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return False
    if not path.startswith("/api"):
        return False
    return not any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def arm_amd_call(request: Request) -> None:
    """Count a call only when a usable chat endpoint is actually configured."""
    from src.engine.llm_config import resolve_chat_endpoint

    if resolve_chat_endpoint() is None:
        return
    amd_rate_limiter.check(client_ip(request))


class WriteRateLimitMiddleware:
    """Count mutating API requests before they reach a handler.

    Pure ASGI so the rejection happens without building a request object, and so
    an oversized or malformed body never reaches the parser when the caller is
    already over budget. ``Retry-After`` is set because a 429 without it invites
    an immediate retry storm.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not writes_enabled():
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        if not is_counted(method, path):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        address = client[0] if client else "unknown"
        key = caller_key(scope.get("headers") or (), address)
        try:
            write_rate_limiter.check(key)
        except HTTPException as exc:
            payload = json.dumps({"detail": exc.detail, "limit_scope": "writes"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": exc.status_code,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                        (b"retry-after", str(MINUTE_SECONDS).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        await self.app(scope, receive, send)

