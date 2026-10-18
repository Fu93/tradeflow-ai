"""In-memory limiter for routes that can call the AMD tokenfactory.

One process, one table. A restart clears it. A free-plan web service is a
single instance, so this is the abuse cap for that process. It does not see
other instances. The response never includes a credential or the quote text.
"""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

PER_MINUTE = 8
PER_HOUR = 30
MINUTE_SECONDS = 60
HOUR_SECONDS = 3600


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


amd_rate_limiter = AmdRateLimiter()


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def arm_amd_call(request: Request) -> None:
    """Count a call only when a usable chat endpoint is actually configured."""
    from src.engine.llm_config import resolve_chat_endpoint

    if resolve_chat_endpoint() is None:
        return
    amd_rate_limiter.check(client_ip(request))
