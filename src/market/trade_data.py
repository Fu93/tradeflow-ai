"""UN Comtrade public-preview trade figures.

Primary source, no API key required:

    GET https://comtradeapi.un.org/public/v1/preview/C/A/HS
        ?reporterCode=826&partnerCode=156&period=<year>&cmdCode=<hs>&flowCode=M

One period per call. `qty` of 0 means the quantity was not reported.
Missing fields stay the string ``not available`` — they are never filled
with zero. WITS is not called.

An optional Comtrade subscription key is read from the environment variable
``COMTRADE_API_KEY`` and sent as ``Ocp-Apim-Subscription-Key``. The value is
never written into URLs, provenance, logs, or exceptions raised here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_none

ROOT = Path(__file__).resolve().parents[2]
HS_PATH = ROOT / "data" / "hs_comtrade.json"
REPORTERS_PATH = ROOT / "data" / "comtrade_reporters.json"
OVERRIDES_PATH = ROOT / "data" / "hs_keyword_overrides.json"
PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
NOT_AVAILABLE = "not available"
LONDON = ZoneInfo("Europe/London")
HS_RE = re.compile(r"^\d{2,6}$")
FLOW_IMPORT = "M"
FLOW_EXPORT = "X"
# World as a partner is Comtrade's documented code 0. It is not a reporter row.
WORLD_CODE = 0
WORLD_NAME = "World"

# Closed years are cached for 30 days. The newest year and the year before it
# are still inside the usual revision window, so they expire after 24 hours.
# A confirmed empty result expires after 1 hour. 429 and transport errors are
# not cached.
HISTORIC_TTL = 30 * 24 * 3600
PROVISIONAL_TTL = 24 * 3600
NEGATIVE_TTL = 3600
DEFAULT_TIMEOUT = 8
DEFAULT_PACE_SECONDS = 1
CALL_BUDGET_SECONDS = 18

_fts_lock = threading.Lock()
_name_lock = threading.Lock()
_names: dict[int, str] | None = None


class Retryable(Exception):
    """429, 5xx, or a transport timeout. Tenacity may try again."""

    def __init__(self, status: int = 0) -> None:
        self.status = status


class BadRequest(Exception):
    """HTTP 400. Do not retry. A multi-period preview call is the usual cause."""


@dataclass
class HttpResult:
    status: int
    text: str
    retry_after: str | None = None


def now_iso() -> str:
    return datetime.now(LONDON).isoformat(timespec="seconds")


def cache_dir() -> Path:
    raw = os.environ.get("TRADEFLOW_MARKET_CACHE", "").strip()
    path = Path(raw) if raw else ROOT / ".cache" / "market"
    path.mkdir(parents=True, exist_ok=True)
    return path


def comtrade_key_present() -> bool:
    """True when a key is configured. The value itself is not returned."""
    return bool(os.environ.get("COMTRADE_API_KEY", "").strip())


def _auth_headers() -> dict[str, str]:
    headers = {"User-Agent": "TradeFlow-MarketData/1.0", "Accept": "application/json"}
    key = os.environ.get("COMTRADE_API_KEY", "").strip()
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    return headers


def is_provisional(year: int, today: date | None = None) -> bool:
    """The current calendar year and the year before it are still revised."""
    current = (today or datetime.now(LONDON).date()).year
    return year >= current - 1


def years_to_try(today: date | None = None) -> list[int]:
    """Newest year first. Preview allows one period per call, so the caller loops."""
    current = (today or datetime.now(LONDON).date()).year
    return [current, current - 1, current - 2]


def dec_str(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def preview_url(reporter: int, partner: int, year: int, hs: str, flow: str) -> str:
    return (
        f"{PREVIEW_URL}?reporterCode={reporter}&partnerCode={partner}"
        f"&period={year}&cmdCode={hs}&flowCode={flow}"
    )


def _load_names() -> dict[int, str]:
    global _names
    with _name_lock:
        if _names is not None:
            return _names
        mapping: dict[int, str] = {WORLD_CODE: WORLD_NAME}
        if REPORTERS_PATH.is_file():
            payload = json.loads(REPORTERS_PATH.read_text(encoding="utf-8"))
            for row in payload.get("rows") or []:
                code = row.get("reporterCode")
                text = (row.get("text") or "").strip()
                if isinstance(code, int) and text:
                    mapping[code] = text
        _names = mapping
        return mapping


def reporter_name(code: int) -> str:
    return _load_names().get(code, NOT_AVAILABLE)


def _hs_rows() -> list[dict[str, Any]]:
    if not HS_PATH.is_file():
        return []
    payload = json.loads(HS_PATH.read_text(encoding="utf-8"))
    return list(payload.get("rows") or [])


def hs_record(hs: str) -> dict[str, Any] | None:
    for row in _hs_rows():
        if str(row.get("id")) == hs:
            return row
    return None


def hs_description(hs: str) -> str:
    row = hs_record(hs)
    if not row:
        return NOT_AVAILABLE
    text = str(row.get("text") or "")
    prefix = f"{hs} - "
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text.strip() or NOT_AVAILABLE


def qty_unit_hint(hs: str) -> str:
    row = hs_record(hs)
    if not row:
        return NOT_AVAILABLE
    unit = str(row.get("standardUnitAbbr") or "").strip()
    if not unit or unit.lower() in {"n/a", "na", "none"}:
        return NOT_AVAILABLE
    return unit


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except Exception:
            return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _truthy_flag(value: Any) -> bool | str:
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, str):
        if value.strip() in {"1", "true", "True"}:
            return True
        if value.strip() in {"0", "false", "False"}:
            return False
        return NOT_AVAILABLE
    return bool(value)


def parse_comtrade_payload(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Turn one preview envelope into a provenance-labelled result.

    ``meta`` carries the query we sent (URL, codes, names). Numbers come only
    from ``payload``. Quantity 0 is unreported, not a real zero.
    """
    base = {
        "status": "unavailable",
        "reason": "no_data",
        "hs": meta.get("hs") or NOT_AVAILABLE,
        "hs_desc": meta.get("hs_desc") or NOT_AVAILABLE,
        "reporter": {"code": meta.get("reporter_code"), "name": meta.get("reporter_name") or NOT_AVAILABLE},
        "partner": {"code": meta.get("partner_code"), "name": meta.get("partner_name") or NOT_AVAILABLE},
        "flow": "import" if meta.get("flow") == FLOW_IMPORT else "export" if meta.get("flow") == FLOW_EXPORT else NOT_AVAILABLE,
        "flow_code": meta.get("flow") or NOT_AVAILABLE,
        "year": meta.get("year"),
        "value_usd": NOT_AVAILABLE,
        "net_weight_kg": NOT_AVAILABLE,
        "qty": NOT_AVAILABLE,
        "qty_unit": NOT_AVAILABLE,
        "unit_value_usd": NOT_AVAILABLE,
        "valuation": NOT_AVAILABLE,
        "is_aggregate": NOT_AVAILABLE,
        "provisional": bool(meta.get("provisional")),
        "provenance": {
            "source": "UN Comtrade preview",
            "url": meta.get("url") or NOT_AVAILABLE,
            "params": meta.get("params") or {},
            "fetched_at": meta.get("fetched_at") or NOT_AVAILABLE,
            "reporter_basis": "reporter-declared",
            "cache": meta.get("cache") or "miss",
            "currency": "USD",
            "caveats": [
                "mirror data not used",
                "qty 0 means unreported, not a zero quantity",
                "customs data cannot separate automatic, carbon, or custom goods",
                "HMRC GBP figures are a different source and are not merged into this value",
            ],
        },
        "stale_value": None,
        "tried": meta.get("tried") or [],
    }
    if meta.get("provisional"):
        base["provenance"]["caveats"].append(
            "this period is inside the usual revision window and is flagged provisional"
        )
    error = payload.get("error") if isinstance(payload, dict) else "unreadable"
    if error:
        base["reason"] = "upstream_error"
        base["detail"] = str(error)[:240]
        return base
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows:
        base["reason"] = "no_data"
        return base
    hs = str(meta.get("hs") or "")
    chosen = None
    for row in rows:
        if str(row.get("cmdCode") or "") == hs:
            chosen = row
            break
    if chosen is None:
        chosen = rows[0]
    if len(rows) > 1:
        base["provenance"]["caveats"].append(
            f"response contained {len(rows)} rows; the row whose cmdCode matches the query was used"
        )
    primary = _as_decimal(chosen.get("primaryValue"))
    cif = _as_decimal(chosen.get("cifvalue"))
    fob = _as_decimal(chosen.get("fobvalue"))
    if primary is None:
        base["reason"] = "no_data"
        return base
    base["status"] = "ok"
    base["reason"] = None
    base["value_usd"] = dec_str(primary)
    weight = _as_decimal(chosen.get("netWgt"))
    if weight is not None:
        base["net_weight_kg"] = dec_str(weight)
    qty = _as_decimal(chosen.get("qty"))
    # 0.0 is the preview's way of saying "not reported".
    if qty is not None and qty != 0:
        base["qty"] = dec_str(qty)
        abbr = chosen.get("qtyUnitAbbr")
        if isinstance(abbr, str) and abbr.strip():
            base["qty_unit"] = abbr.strip()
        else:
            base["qty_unit"] = meta.get("qty_unit") or NOT_AVAILABLE
        unit = (primary / qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        base["unit_value_usd"] = dec_str(unit)
        base["provenance"]["unit_value_formula"] = "primaryValue / qty, rounded half-up to 0.01 USD"
    else:
        base["provenance"]["caveats"].append("quantity was unreported, so no unit value was computed")
    if cif is not None and cif == primary:
        base["valuation"] = "CIF"
    elif fob is not None and fob == primary:
        base["valuation"] = "FOB"
    else:
        base["valuation"] = NOT_AVAILABLE
    base["is_aggregate"] = _truthy_flag(chosen.get("isAggregate"))
    if chosen.get("isQtyEstimated") in (True, 1, "1"):
        base["provenance"]["caveats"].append("isQtyEstimated was true on the preview row")
    if chosen.get("isNetWgtEstimated") in (True, 1, "1"):
        base["provenance"]["caveats"].append("isNetWgtEstimated was true on the preview row")
    return base


class MarketCache:
    """SQLite TTL cache. Expired rows are kept so a later outage can serve them."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (cache_dir() / "responses.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    cache_key TEXT PRIMARY KEY,
                    stored_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=DELETE")
        return conn

    @staticmethod
    def make_key(reporter: int, partner: int, flow: str, hs: str, year: int) -> str:
        return f"comtrade|{reporter}|{partner}|{flow}|{hs}|{year}"

    def lookup_meta(self, key: str, now: float | None = None) -> tuple[dict[str, Any] | None, str, float | None]:
        clock = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT stored_at, ttl_seconds, payload FROM entries WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None, "miss", None
        stored_at, ttl, payload = row
        state = "fresh" if clock - float(stored_at) <= int(ttl) else "stale"
        return json.loads(payload), state, float(stored_at)

    def lookup(self, key: str, now: float | None = None) -> tuple[dict[str, Any] | None, str]:
        payload, state, _stored = self.lookup_meta(key, now)
        return payload, state

    def store(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO entries (cache_key, stored_at, ttl_seconds, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    stored_at = excluded.stored_at,
                    ttl_seconds = excluded.ttl_seconds,
                    payload = excluded.payload
                """,
                (key, time.time(), int(ttl_seconds), text),
            )


class Pace:
    """At most one preview call per ``interval`` seconds, including retries."""

    def __init__(self, interval: int = DEFAULT_PACE_SECONDS) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                time.sleep(delay)
            self._next = time.monotonic() + self.interval


class Breaker:
    """Stop calling upstream after repeated failures. Stale cache can still answer."""

    def __init__(self, limit: int = 4, cooldown: int = 60) -> None:
        self.limit = limit
        self.cooldown = cooldown
        self.failures = 0
        self.open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return time.monotonic() >= self.open_until

    def ok(self) -> None:
        with self._lock:
            self.failures = 0
            self.open_until = 0.0

    def fail(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.limit:
                self.open_until = time.monotonic() + self.cooldown


def default_http_get(url: str, headers: dict[str, str], timeout: int) -> HttpResult:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise Retryable(0) from exc
    except httpx.HTTPError as exc:
        raise Retryable(0) from exc
    retry_after = response.headers.get("Retry-After")
    if response.status_code == 400:
        raise BadRequest()
    if response.status_code == 429 or response.status_code >= 500:
        raise Retryable(response.status_code)
    return HttpResult(response.status_code, response.text, retry_after)


def _mark_cache(result: dict[str, Any], state: str, stored_at: float | None = None) -> dict[str, Any]:
    result = json.loads(json.dumps(result))
    provenance = result.setdefault("provenance", {})
    provenance["cache"] = state
    if stored_at is not None and state in {"fresh", "stale"}:
        age = int(time.time() - float(stored_at))
        provenance["cache_age_seconds"] = age if age > 0 else 0
    return result


def fetch_period(
    *,
    reporter: int,
    partner: int,
    year: int,
    hs: str,
    flow: str,
    client: Callable[[str, dict[str, str], int], HttpResult] | None = None,
    cache: MarketCache | None = None,
    pace: Pace | None = None,
    breaker: Breaker | None = None,
    today: date | None = None,
    immediate: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    deadline: float | None = None,
    attempts: int = 3,
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch one reporter/partner/year/HS/flow. Serves a fresh cache hit first."""
    url = preview_url(reporter, partner, year, hs, flow)
    meta = {
        "hs": hs,
        "hs_desc": hs_description(hs),
        "reporter_code": reporter,
        "reporter_name": reporter_name(reporter),
        "partner_code": partner,
        "partner_name": reporter_name(partner),
        "flow": flow,
        "year": year,
        "url": url,
        "params": {
            "reporterCode": reporter,
            "partnerCode": partner,
            "period": year,
            "cmdCode": hs,
            "flowCode": flow,
        },
        "fetched_at": now_iso(),
        "provisional": is_provisional(year, today),
        "qty_unit": qty_unit_hint(hs),
        "cache": "miss",
        "tried": [year],
    }
    store = cache if cache is not None else MarketCache()
    key = MarketCache.make_key(reporter, partner, flow, hs, year)
    cached, state, stored_at = store.lookup_meta(key)
    if cached is not None and state == "fresh" and not refresh:
        return _mark_cache(cached, "fresh", stored_at)

    gate = breaker if breaker is not None else Breaker()
    if not gate.allow():
        if cached is not None:
            return _mark_cache(cached, "stale", stored_at)
        meta["cache"] = "miss"
        blocked = parse_comtrade_payload({"error": "circuit open"}, meta)
        blocked["reason"] = "upstream_error"
        blocked["stale_value"] = None
        return blocked

    getter = client or default_http_get
    headers = _auth_headers()
    clock = pace if pace is not None else Pace(0 if immediate else DEFAULT_PACE_SECONDS)
    retrying = Retrying(
        retry=retry_if_exception_type(Retryable),
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_none() if immediate else wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )

    def _call() -> HttpResult:
        if deadline is not None and time.monotonic() > deadline:
            raise Retryable()
        clock.wait()
        return getter(url, headers, timeout)

    try:
        result = retrying(_call)
    except BadRequest:
        gate.fail()
        failed = parse_comtrade_payload({"error": "HTTP 400"}, meta)
        failed["reason"] = "upstream_error"
        failed["detail"] = "HTTP 400 (not retried). Preview accepts one period per call."
        if cached is not None:
            failed["stale_value"] = _mark_cache(cached, "stale", stored_at)
        return failed
    except Retryable as exc:
        gate.fail()
        if cached is not None:
            return _mark_cache(cached, "stale", stored_at)
        failed = parse_comtrade_payload({"error": "upstream failed"}, meta)
        failed["reason"] = "rate_limited" if getattr(exc, "status", 0) == 429 else "upstream_error"
        failed["stale_value"] = None
        return failed

    if result.status != 200:
        gate.fail()
        if cached is not None:
            return _mark_cache(cached, "stale", stored_at)
        failed = parse_comtrade_payload({"error": f"HTTP {result.status}"}, meta)
        failed["reason"] = "upstream_error"
        failed["stale_value"] = None
        return failed
    try:
        payload = json.loads(result.text, parse_float=Decimal)
    except json.JSONDecodeError:
        gate.fail()
        if cached is not None:
            return _mark_cache(cached, "stale", stored_at)
        failed = parse_comtrade_payload({"error": "response was not JSON"}, meta)
        failed["reason"] = "upstream_error"
        return failed
    if not isinstance(payload, dict):
        payload = {"error": "response was not an object"}
    parsed = parse_comtrade_payload(payload, meta)
    gate.ok()
    if parsed["status"] == "ok":
        ttl = PROVISIONAL_TTL if meta["provisional"] else HISTORIC_TTL
        store.store(key, parsed, ttl)
    elif parsed.get("reason") == "no_data":
        store.store(key, parsed, NEGATIVE_TTL)
    return parsed


def fetch_latest(
    *,
    reporter: int,
    partner: int,
    hs: str,
    flow: str = FLOW_IMPORT,
    year: int | None = None,
    client: Callable[[str, dict[str, str], int], HttpResult] | None = None,
    cache: MarketCache | None = None,
    pace: Pace | None = None,
    breaker: Breaker | None = None,
    today: date | None = None,
    immediate: bool = False,
    budget_seconds: int = CALL_BUDGET_SECONDS,
    attempts: int = 3,
    timeout: int = DEFAULT_TIMEOUT,
    refresh: bool = False,
) -> dict[str, Any]:
    """Try the requested year, or walk newest-to-older until a row exists."""
    if year is not None:
        return fetch_period(
            reporter=reporter, partner=partner, year=year, hs=hs, flow=flow,
            client=client, cache=cache, pace=pace, breaker=breaker, today=today,
            immediate=immediate, attempts=attempts, timeout=timeout, refresh=refresh,
        )
    deadline = time.monotonic() + budget_seconds
    tried: list[int] = []
    last: dict[str, Any] | None = None
    for candidate in years_to_try(today):
        tried.append(candidate)
        last = fetch_period(
            reporter=reporter, partner=partner, year=candidate, hs=hs, flow=flow,
            client=client, cache=cache, pace=pace, breaker=breaker, today=today,
            immediate=immediate, deadline=deadline, attempts=attempts, timeout=timeout,
            refresh=refresh,
        )
        last["tried"] = list(tried)
        if last.get("status") == "ok":
            return last
        if last.get("reason") in {"rate_limited", "upstream_error"} and last.get("provenance", {}).get("cache") != "stale":
            # A transport failure is not evidence that the year is empty.
            # Keep walking only while the budget remains.
            if time.monotonic() > deadline:
                last["tried"] = list(tried)
                return last
            continue
        if last.get("reason") == "no_data":
            continue
    assert last is not None
    last["tried"] = tried
    return last


def _overrides() -> list[dict[str, Any]]:
    if not OVERRIDES_PATH.is_file():
        return []
    payload = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return list(payload.get("overrides") or [])


def _ensure_fts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS hs_fts USING fts5(hs UNINDEXED, body, aggr UNINDEXED, tokenize='porter unicode61')"
    )
    count = conn.execute("SELECT count(*) FROM hs_fts").fetchone()[0]
    if count:
        return
    for row in _hs_rows():
        conn.execute(
            "INSERT INTO hs_fts (hs, body, aggr) VALUES (?, ?, ?)",
            (str(row.get("id")), str(row.get("text") or ""), str(row.get("aggrLevel") or "")),
        )
    conn.commit()


def search_hs(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    """Curated overrides first, then SQLite FTS5 BM25. Never selects a winner."""
    text = keyword.strip()
    words = re.findall(r"[A-Za-z0-9]+", text)[:8]
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    if HS_RE.fullmatch(text):
        seen.add(text)
        found.append({
            "hs": text,
            "text": hs_description(text),
            "score": NOT_AVAILABLE,
            "score_note": "the typed code; not a BM25 score",
            "source": "typed code",
            "note": "Confirm this code to fetch. Typing it does not fetch.",
        })
    hay = text.lower()
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in _overrides():
        score = 0
        for key in entry.get("keywords") or []:
            token = str(key).strip().lower()
            if token and token in hay:
                score += len(token)
        if score:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: item[0], reverse=True)
    for _score, entry in ranked:
            hs = str(entry.get("hs") or "")
            if hs and hs not in seen:
                seen.add(hs)
                found.append({
                    "hs": hs,
                    "text": hs_description(hs),
                    "score": NOT_AVAILABLE,
                    "score_note": "curated override; not a BM25 score",
                    "source": "curated override",
                    "note": entry.get("note") or NOT_AVAILABLE,
                })
    if not words:
        return found[:limit]
    match = " OR ".join(f'"{word}"' for word in words)
    db_path = cache_dir() / "hs_fts.sqlite"
    with _fts_lock:
        conn = sqlite3.connect(db_path)
        try:
            _ensure_fts(conn)
            try:
                rows = conn.execute(
                    "SELECT hs, body, bm25(hs_fts) FROM hs_fts WHERE hs_fts MATCH ? ORDER BY bm25(hs_fts) LIMIT ?",
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        finally:
            conn.close()
    for hs, body, score in rows:
        if hs in seen:
            continue
        seen.add(hs)
        found.append({
            "hs": hs,
            "text": body,
            "score": dec_str(Decimal(str(score))),
            "score_note": "sqlite FTS5 bm25; a lower number is a stronger match",
            "source": "hs_comtrade.json FTS",
            "note": NOT_AVAILABLE,
        })
        if len(found) >= limit:
            break
    return found[:limit]


def lookup_market(
    *,
    hs: str | None = None,
    keyword: str | None = None,
    year: int | None = None,
    reporter: int = 826,
    partner: int = 156,
    flow: str = FLOW_IMPORT,
    client: Callable[[str, dict[str, str], int], HttpResult] | None = None,
    cache: MarketCache | None = None,
    pace: Pace | None = None,
    today: date | None = None,
    immediate: bool = False,
) -> dict[str, Any]:
    """Resolve an HS code, or return keyword candidates without calling Comtrade."""
    offline = os.environ.get("TRADEFLOW_MARKET_OFFLINE", "").strip().lower()
    if offline in {"1", "true", "yes"}:
        return {
            "status": "unavailable",
            "reason": "offline",
            "hs": hs or NOT_AVAILABLE,
            "value_usd": NOT_AVAILABLE,
            "qty": NOT_AVAILABLE,
            "unit_value_usd": NOT_AVAILABLE,
            "net_weight_kg": NOT_AVAILABLE,
            "stale_value": None,
            "tried": [],
            "provenance": {
                "source": "UN Comtrade preview",
                "url": NOT_AVAILABLE,
                "fetched_at": NOT_AVAILABLE,
                "cache": "miss",
                "caveats": ["TRADEFLOW_MARKET_OFFLINE is set; no upstream call was made"],
            },
        }
    if flow not in {FLOW_IMPORT, FLOW_EXPORT}:
        return {"status": "unavailable", "reason": "bad_request", "detail": "flow must be M or X", "stale_value": None}
    code = (hs or "").strip()
    if code:
        if not HS_RE.fullmatch(code):
            return {
                "status": "unavailable",
                "reason": "bad_request",
                "detail": "hs must be 2 to 6 digits. National 8-digit and 10-digit codes are not in the Comtrade preview.",
                "stale_value": None,
                "tried": [],
            }
        return fetch_latest(
            reporter=reporter, partner=partner, hs=code, flow=flow, year=year,
            client=client, cache=cache, pace=pace, today=today, immediate=immediate,
        )
    text = (keyword or "").strip()
    if not text:
        return {
            "status": "unavailable",
            "reason": "bad_request",
            "detail": "pass hs (2-6 digits) or keyword",
            "stale_value": None,
            "tried": [],
        }
    candidates = search_hs(text)
    if not candidates:
        return {
            "status": "unavailable",
            "reason": "unmapped_keyword",
            "keyword": text,
            "candidates": [],
            "stale_value": None,
            "tried": [],
            "detail": "No curated override or HS text matched. Pass an explicit hs code.",
        }
    return {
        "status": "needs_confirmation",
        "reason": None,
        "keyword": text,
        "candidates": candidates,
        "detail": "Keyword search does not pick an HS code. Confirm one candidate and call again with hs.",
        "stale_value": None,
        "tried": [],
    }


def _series_point(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("status") != "ok":
        return None
    value = row.get("value_usd")
    if value in (None, NOT_AVAILABLE):
        return None
    return {"year": row.get("year"), "value_usd": value}


def lookup_market_card(
    *,
    hs: str | None = None,
    keyword: str | None = None,
    reporter: int = 826,
    partner: int = 156,
    flow: str = FLOW_IMPORT,
    client: Callable[[str, dict[str, str], int], HttpResult] | None = None,
    cache: MarketCache | None = None,
    pace: Pace | None = None,
    today: date | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Card payload. A keyword still does not fetch. An HS code does.

    The hero is UK-from-China (or the requested reporter/partner) for the
    latest year that has a row. World total is the same reporter and year
    with partner World. The series keeps only years that actually returned
    a value. This payload is reference data. It is not a solver input.
    """
    code = (hs or "").strip()
    if not code:
        listed = lookup_market(keyword=keyword, client=client, cache=cache, pace=pace, today=today)
        listed["view"] = "card"
        return listed
    offline = os.environ.get("TRADEFLOW_MARKET_OFFLINE", "").strip().lower() in {"1", "true", "yes"}
    if offline or not HS_RE.fullmatch(code):
        screened = lookup_market(
            hs=code, reporter=reporter, partner=partner, flow=flow,
            client=client, cache=cache, pace=pace, today=today,
        )
        screened["view"] = "card"
        screened["series"] = []
        screened["hero"] = {
            "value_usd": screened.get("value_usd") or NOT_AVAILABLE,
            "year": screened.get("year"),
        }
        screened["world"] = {
            "value_usd": NOT_AVAILABLE,
            "status": "unavailable",
            "reason": screened.get("reason"),
        }
        screened["reference_only"] = True
        screened["influences_solver"] = False
        screened["caveat"] = "Customs codes can't separate automatic/carbon/custom items"
        return screened
    latest = fetch_latest(
        reporter=reporter, partner=partner, hs=code, flow=flow,
        client=client, cache=cache, pace=pace, today=today, refresh=refresh,
    )
    year = latest.get("year") if latest.get("status") == "ok" else None
    world: dict[str, Any] = {"value_usd": NOT_AVAILABLE, "status": "unavailable", "reason": "not_fetched", "year": year}
    series: list[dict[str, Any]] = []
    if isinstance(year, int):
        world_row = fetch_period(
            reporter=reporter, partner=WORLD_CODE, year=year, hs=code, flow=flow,
            client=client, cache=cache, pace=pace, today=today, refresh=refresh,
        )
        if world_row.get("status") == "ok":
            world = {
                "value_usd": world_row.get("value_usd"),
                "year": world_row.get("year"),
                "status": "ok",
                "reason": None,
            }
        else:
            world = {
                "value_usd": NOT_AVAILABLE,
                "year": year,
                "status": world_row.get("status"),
                "reason": world_row.get("reason"),
            }
        # The latest year plus three earlier years. Only real rows are kept.
        for offset in range(0, 4):
            point_year = year - offset
            if offset == 0:
                row = latest
            else:
                row = fetch_period(
                    reporter=reporter, partner=partner, year=point_year, hs=code, flow=flow,
                    client=client, cache=cache, pace=pace, today=today, refresh=refresh,
                )
            point = _series_point(row)
            if point is not None:
                series.append(point)
        series.sort(key=lambda item: int(item["year"]))
    provenance = dict(latest.get("provenance") or {})
    return {
        "view": "card",
        "status": latest.get("status") or "unavailable",
        "reason": latest.get("reason"),
        "reference_only": True,
        "influences_solver": False,
        "label": REFERENCE_LABEL,
        "hs": code,
        "hs_desc": latest.get("hs_desc") or NOT_AVAILABLE,
        "hero": {"value_usd": latest.get("value_usd") or NOT_AVAILABLE, "year": latest.get("year")},
        "world": world,
        "qty": latest.get("qty") or NOT_AVAILABLE,
        "qty_unit": latest.get("qty_unit") or NOT_AVAILABLE,
        "unit_value_usd": latest.get("unit_value_usd") or NOT_AVAILABLE,
        "valuation": latest.get("valuation") or NOT_AVAILABLE,
        "series": series,
        "reporter": latest.get("reporter") or {"code": reporter, "name": reporter_name(reporter)},
        "partner": latest.get("partner") or {"code": partner, "name": reporter_name(partner)},
        "provenance": provenance,
        "caveat": "Customs codes can't separate automatic/carbon/custom items",
    }


def _find_row(rows: list[dict[str, Any]], *, hs: str, year: int, reporter: int, partner: int, flow: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("status") != "ok":
            continue
        if str(row.get("hs")) != hs or int(row.get("year") or 0) != year:
            continue
        reporter_code = (row.get("reporter") or {}).get("code")
        partner_code = (row.get("partner") or {}).get("code")
        if reporter_code != reporter or partner_code != partner:
            continue
        if row.get("flow_code") != flow:
            continue
        return row
    return None


def _money(row: dict[str, Any] | None) -> Decimal | None:
    if not row:
        return None
    value = row.get("value_usd")
    if value in (None, NOT_AVAILABLE):
        return None
    return _as_decimal(value)


REFERENCE_LABEL = (
    "REFERENCE ONLY. Implied customs unit value is not a price quote. "
    "CIF customs valuation is not a supplier offer and does not set the counter."
)


def _stamp_reference(ctx: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(ctx)
    stamped["reference_only"] = True
    stamped["influences_solver"] = False
    stamped["label"] = REFERENCE_LABEL
    caveats = list((stamped.get("provenance") or {}).get("caveats") or [])
    extra = "this block does not influence the solver, the recommended price, or the circuit breaker"
    if extra not in caveats:
        caveats.append(extra)
    provenance = dict(stamped.get("provenance") or {})
    provenance["caveats"] = caveats
    stamped["provenance"] = provenance
    return stamped


def resolve_hs(hs_code: str | None, product_name: str) -> tuple[str | None, str, list[dict[str, Any]]]:
    """User hs_code wins. Otherwise the top keyword hit is labelled, not confirmed."""
    code = (hs_code or "").strip()
    if code:
        if HS_RE.fullmatch(code):
            return code, "user_override", []
        return None, "bad_hs", []
    candidates = search_hs(product_name or "", limit=5)
    if not candidates:
        return None, "unmapped_keyword", []
    top = candidates[0]
    kind = "curated_override" if top.get("source") == "curated override" else "unconfirmed_top_candidate"
    return str(top["hs"]), kind, candidates


def build_market_context(
    hs_code: str | None,
    product_name: str,
    *,
    fetch: bool = True,
    client: Callable[[str, dict[str, str], int], HttpResult] | None = None,
    cache: MarketCache | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """UK-from-China reference for a session or an auction. Never a solver input."""
    hs, selection, candidates = resolve_hs(hs_code, product_name)
    offline = os.environ.get("TRADEFLOW_MARKET_OFFLINE", "").strip().lower() in {"1", "true", "yes"}
    reason = "offline" if offline else "not_fetched"
    if selection == "bad_hs":
        reason = "bad_request"
    elif not hs:
        reason = "unmapped_keyword"
    if (not fetch) or offline or not hs:
        return _stamp_reference({
            "status": "unavailable",
            "reason": reason,
            "hs": hs or NOT_AVAILABLE,
            "hs_desc": hs_description(hs) if hs else NOT_AVAILABLE,
            "hs_selection": selection,
            "candidates": candidates,
            "value_usd": NOT_AVAILABLE,
            "qty": NOT_AVAILABLE,
            "qty_unit": NOT_AVAILABLE,
            "unit_value_usd": NOT_AVAILABLE,
            "net_weight_kg": NOT_AVAILABLE,
            "year": None,
            "reporter": {"code": 826, "name": reporter_name(826)},
            "partner": {"code": 156, "name": reporter_name(156)},
            "flow": "import",
            "valuation": NOT_AVAILABLE,
            "provenance": {
                "source": "UN Comtrade preview",
                "url": NOT_AVAILABLE,
                "fetched_at": NOT_AVAILABLE,
                "cache": "miss",
                "currency": "USD",
                "caveats": [
                    "customs unit value is not a price quote",
                    "CIF valuation is not a supplier offer",
                ],
            },
            "stale_value": None,
            "tried": [],
        })
    fetched = fetch_latest(
        reporter=826,
        partner=156,
        hs=hs,
        flow=FLOW_IMPORT,
        client=client,
        cache=cache,
        pace=Pace(0),
        today=today,
        immediate=True,
        budget_seconds=4,
        attempts=1,
        timeout=3,
    )
    fetched["hs_selection"] = selection
    fetched["candidates"] = candidates
    return _stamp_reference(fetched)


def derive_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sums and the savings-pool ceiling. Rates are the cited 1.5% and 3% band."""
    parent = _find_row(rows, hs="6601", year=2024, reporter=826, partner=156, flow=FLOW_IMPORT)
    parts = [
        _find_row(rows, hs=code, year=2024, reporter=826, partner=156, flow=FLOW_IMPORT)
        for code in ("660110", "660191", "660199")
    ]
    derived: dict[str, Any] = {
        "label": "estimate",
        "children_hs": ["660110", "660191", "660199"],
        "children_sum_usd": NOT_AVAILABLE,
        "parent_usd": NOT_AVAILABLE,
        "children_equal_parent": NOT_AVAILABLE,
        "savings_pool": [],
        "uk_china_share_of_uk_world_2024": NOT_AVAILABLE,
    }
    amounts = [_money(row) for row in parts]
    parent_amount = _money(parent)
    if parent_amount is not None:
        derived["parent_usd"] = dec_str(parent_amount)
    if all(amount is not None for amount in amounts):
        total = amounts[0] + amounts[1] + amounts[2]
        derived["children_sum_usd"] = dec_str(total)
        if parent_amount is not None:
            derived["children_equal_parent"] = total == parent_amount
            derived["children_minus_parent_usd"] = dec_str(total - parent_amount)
    # 15/1000 = 1.5%, 3/100 = 3%. Both cited large-buyer rates, not a TradeFlow result.
    low_rate = Decimal("15") / Decimal("1000")
    high_rate = Decimal("3") / Decimal("100")
    for row in rows:
        amount = _money(row)
        if amount is None:
            continue
        if str(row.get("hs")) != "6601" or row.get("flow_code") != FLOW_IMPORT:
            continue
        if (row.get("reporter") or {}).get("code") != 826 or (row.get("partner") or {}).get("code") != 156:
            continue
        derived["savings_pool"].append({
            "label": "estimate",
            "upper_bound": True,
            "not_a_tradeflow_result": True,
            "formula": "category import value x s, s in {1.5%, 3%}",
            "rate_source": "large-buyer Walmart/Pactum reported range, not small-buyer China sourcing",
            "year": row.get("year"),
            "low_usd": dec_str((amount * low_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "high_usd": dec_str((amount * high_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "base_usd": dec_str(amount),
        })
    world = _find_row(rows, hs="6601", year=2024, reporter=826, partner=WORLD_CODE, flow=FLOW_IMPORT)
    world_amount = _money(world)
    if parent_amount is not None and world_amount is not None and world_amount != 0:
        share = (parent_amount / world_amount).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        derived["uk_china_share_of_uk_world_2024"] = dec_str(share)
        derived["uk_world_6601_2024_usd"] = dec_str(world_amount)
    return derived


def render_snapshot_markdown(snapshot: dict[str, Any], filename: str) -> str:
    """Deterministic block. ``check_market_doc.py`` requires the doc to contain it."""
    lines = [
        f"<!-- BEGIN GENERATED MARKET SNAPSHOT {filename} -->",
        f"Generated from `{filename}`. Do not edit the figures by hand.",
        f"Source: UN Comtrade public preview (`{snapshot.get('source_url', PREVIEW_URL)}`).",
        f"Fetched at: {snapshot.get('fetched_at', NOT_AVAILABLE)}.",
        "Valuation is whatever the preview row supports (CIF when cifvalue equals primaryValue). Currency is USD.",
        "Quantity 0 on the preview row is stored here as `not available`. Unit value is computed only when value and quantity both exist.",
        "",
        "| Reporter | Partner | Flow | HS | Year | Value USD | Quantity | Qty unit | Unit value USD | Net weight kg | Aggregate | Provisional | Cache |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in snapshot.get("rows") or []:
        provenance = row.get("provenance") or {}
        lines.append(
            "| {reporter} | {partner} | {flow} | {hs} | {year} | {value} | {qty} | {unit} | {auv} | {weight} | {agg} | {prov} | {cache} |".format(
                reporter=(row.get("reporter") or {}).get("name") or NOT_AVAILABLE,
                partner=(row.get("partner") or {}).get("name") or NOT_AVAILABLE,
                flow=row.get("flow") or NOT_AVAILABLE,
                hs=row.get("hs") or NOT_AVAILABLE,
                year=row.get("year") if row.get("year") is not None else NOT_AVAILABLE,
                value=row.get("value_usd", NOT_AVAILABLE),
                qty=row.get("qty", NOT_AVAILABLE),
                unit=row.get("qty_unit", NOT_AVAILABLE),
                auv=row.get("unit_value_usd", NOT_AVAILABLE),
                weight=row.get("net_weight_kg", NOT_AVAILABLE),
                agg=row.get("is_aggregate", NOT_AVAILABLE),
                prov=row.get("provisional", NOT_AVAILABLE),
                cache=provenance.get("cache", NOT_AVAILABLE),
            )
        )
    derived = snapshot.get("derived") or {}
    lines.extend([
        "",
        f"Children 660110+660191+660199 (UK imports from China, 2024) sum to {derived.get('children_sum_usd', NOT_AVAILABLE)} USD.",
        f"Parent HS 6601 for the same flow is {derived.get('parent_usd', NOT_AVAILABLE)} USD.",
        f"Children equal parent: {derived.get('children_equal_parent', NOT_AVAILABLE)}.",
        f"UK-China share of UK-World HS 6601 imports in 2024 (both Comtrade rows): {derived.get('uk_china_share_of_uk_world_2024', NOT_AVAILABLE)}.",
        "",
        "Savings pool (ESTIMATE, upper bound). Formula: `value x s` with s = 1.5% and s = 3% from large-buyer reports. This is not a TradeFlow result.",
    ])
    pools = derived.get("savings_pool") or []
    if not pools:
        lines.append("Savings pool: not available.")
    for pool in pools:
        lines.append(
            f"- Year {pool.get('year')}: base {pool.get('base_usd')} USD x 1.5% = {pool.get('low_usd')} USD; x 3% = {pool.get('high_usd')} USD. Label: estimate. Upper bound. Not a TradeFlow result."
        )
    lines.append(f"<!-- END GENERATED MARKET SNAPSHOT {filename} -->")
    return "\n".join(lines)
