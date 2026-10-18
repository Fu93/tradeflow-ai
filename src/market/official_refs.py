"""Official reference rates. Failure is ``no data``, never a stand-in number.

ECB euro foreign exchange reference rates are read through Frankfurter.
UK import duty is read from the UK Trade Tariff API. Both are reference
only unless a caller has confirmed the figure and passed it in as an
input. This module does not cache an invented rate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

ECB_RATES_URL = "https://api.frankfurter.dev/v2/providers/ecb/rates"
UK_TARIFF_COMMODITY_URL = "https://www.trade-tariff.service.gov.uk/api/v2/commodities/{code}"
ECB_LICENCE = (
    "ECB euro foreign exchange reference rates, redistributed by Frankfurter "
    "without modification. For information only. ECB discourages using the "
    "rates as transaction prices."
)
TARIFF_LICENCE = (
    "UK Trade Tariff (Open Government Licence v3). For information only. "
    "Not a customs ruling."
)


def _unavailable(source_url: str, licence: str, reason: str) -> dict[str, Any]:
    return {
        "status": "no data",
        "value": None,
        "reason": reason,
        "source_url": source_url,
        "as_of": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": licence,
        "used_in_solver": False,
    }


def _default_getter(url: str, timeout: float):
    import httpx

    response = httpx.get(url, timeout=timeout, headers={"Accept": "application/json", "User-Agent": "TradeFlow-MarketData/1.0"})
    response.raise_for_status()
    return response.json()


def fetch_ecb_rates(
    symbols: list[str] | None = None,
    *,
    getter: Callable[[str, float], Any] | None = None,
    timeout: float = 8,
) -> dict[str, Any]:
    """ECB reference rates quoted as currency units per 1 euro.

    ``symbols`` filters the returned map. A transport or parse failure
    returns status ``no data`` and ``value`` null.
    """
    wanted = [item.strip().upper() for item in (symbols or []) if item and item.strip()]
    url = ECB_RATES_URL
    if wanted:
        url = f"{ECB_RATES_URL}?quotes={','.join(wanted)}"
    fetch = getter or _default_getter
    try:
        payload = fetch(url, timeout)
    except Exception as exc:
        return _unavailable(ECB_RATES_URL, ECB_LICENCE, f"{type(exc).__name__}: {exc}")
    rates = _ecb_rate_map(payload)
    if rates is None:
        return _unavailable(ECB_RATES_URL, ECB_LICENCE, "response had no ECB rate map")
    if wanted:
        rates = {key: rates[key] for key in wanted if key in rates}
        missing = [key for key in wanted if key not in rates]
        if missing and not rates:
            return _unavailable(ECB_RATES_URL, ECB_LICENCE, "requested currencies were absent")
    as_of = _ecb_as_of(payload)
    return {
        "status": "ok",
        "value": rates,
        "reason": None,
        "source_url": ECB_RATES_URL,
        "as_of": as_of,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": ECB_LICENCE,
        "used_in_solver": False,
        "base": "EUR",
    }


def _ecb_rate_map(payload: Any) -> dict[str, float] | None:
    """Accept Frankfurter v2's list, a provider document, or a flat map.

    The live ECB provider returns a JSON list of ``{date, base, quote, rate}``
    objects, with ``base`` EUR and ``rate`` as currency units per euro.
    A payload that is neither that list nor a rate map is not a rate.
    """
    if isinstance(payload, list):
        return _rates_from_frankfurter_list(payload)
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("rates"), dict):
        return _numeric_map(payload["rates"])
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("rates"), dict):
        return _numeric_map(data["rates"])
    # Some responses nest quotes under a provider record.
    for key in ("quotes", "rates"):
        if isinstance(payload.get(key), dict):
            return _numeric_map(payload[key])
    flat = _numeric_map(payload)
    if flat and any(len(code) == 3 and code.isalpha() for code in flat):
        return flat
    return None


def _rates_from_frankfurter_list(payload: list) -> dict[str, float] | None:
    """Currency-per-euro rates from a Frankfurter v2 provider list."""
    out: dict[str, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        base = item.get("base")
        if base is not None and str(base).strip().upper() != "EUR":
            continue
        quote = item.get("quote")
        rate = item.get("rate")
        if not isinstance(quote, str):
            continue
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            continue
        number = float(rate)
        if number != number or number <= 0:
            continue
        out[quote.strip().upper()] = number
    return out or None


def _ecb_as_of(payload: Any):
    if isinstance(payload, dict):
        return payload.get("date") or payload.get("as_of")
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("date"):
                return item.get("date")
    return None


def _numeric_map(raw: dict) -> dict[str, float] | None:
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        code = key.strip().upper()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if number != number or number <= 0:
            continue
        out[code] = number
    return out or None


def reference_bundle(
    currencies: list[str],
    base_currency: str,
    commodity_code: str | None,
    *,
    getter: Callable[[str, float], Any] | None = None,
    timeout: float = 8,
) -> dict[str, Any]:
    """Fetch ECB and, when a code is present, the UK tariff. Never invents a rate.

    ``used_in_solver`` stays false. A caller that copies a confirmed figure
    into an input flips that flag on the copied leg only.
    """
    wanted = []
    for code in list(currencies) + [base_currency]:
        if not code:
            continue
        token = str(code).strip().upper()
        if token and token not in wanted:
            wanted.append(token)
    fx = fetch_ecb_rates(wanted, getter=getter, timeout=timeout)
    if commodity_code:
        tariff = fetch_uk_tariff(commodity_code, getter=getter, timeout=timeout)
    else:
        tariff = _unavailable(UK_TARIFF_COMMODITY_URL, TARIFF_LICENCE, "no commodity code")
    return {
        "fx": fx,
        "tariff": tariff,
        "used_in_solver": False,
        "note": "Reference only until the caller confirms a figure and it is copied into an input.",
    }


def fx_to_base(rates: dict[str, float] | None, quote_currency: str, base_currency: str) -> float | None:
    """Units of ``base_currency`` per 1 unit of ``quote_currency``, via EUR.

    ECB rates here are currency-per-euro. A cross rate is rate(base) / rate(quote).
    Returns None when either leg is missing. Does not invent a rate.
    """
    if rates is None:
        return None
    quote = quote_currency.strip().upper()
    base = base_currency.strip().upper()
    if quote == base:
        return 1.0
    per_euro = dict(rates)
    per_euro["EUR"] = 1.0
    quote_per_euro = per_euro.get(quote)
    base_per_euro = per_euro.get(base)
    if quote_per_euro is None or base_per_euro is None:
        return None
    if quote_per_euro <= 0:
        return None
    return float(base_per_euro) / float(quote_per_euro)


def fetch_uk_tariff(
    commodity_code: str,
    *,
    getter: Callable[[str, float], Any] | None = None,
    timeout: float = 8,
) -> dict[str, Any]:
    """Third-country import duty for a commodity code, if the API states one.

    The duty expression is returned as text plus a simple ad-valorem rate
    when the expression is a single percentage. Anything else stays textual
    and the numeric rate is null. Failures are ``no data``.
    """
    code = "".join(ch for ch in str(commodity_code) if ch.isdigit())
    url = UK_TARIFF_COMMODITY_URL.format(code=code)
    if not code:
        return _unavailable(UK_TARIFF_COMMODITY_URL, TARIFF_LICENCE, "empty commodity code")
    fetch = getter or _default_getter
    try:
        payload = fetch(url, timeout)
    except Exception as exc:
        return _unavailable(url, TARIFF_LICENCE, f"{type(exc).__name__}: {exc}")
    parsed = _duty_from_tariff(payload)
    if parsed is None:
        return _unavailable(url, TARIFF_LICENCE, "no import duty expression in the response")
    return {
        "status": "ok",
        "value": parsed,
        "reason": None,
        "source_url": url,
        "as_of": parsed.get("as_of"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": TARIFF_LICENCE,
        "used_in_solver": False,
        "commodity_code": code,
    }


def _duty_from_tariff(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    measures = _measures(payload)
    for measure in measures:
        text = _duty_text(measure)
        if not text:
            continue
        return {
            "duty_expression": text,
            "ad_valorem_rate": _simple_percent(text),
            "as_of": measure.get("effective_start_date") or measure.get("import_date"),
            "geographical_area": _area(measure),
        }
    return None


def _measures(payload: dict) -> list[dict]:
    found: list[dict] = []
    included = payload.get("included")
    if isinstance(included, list):
        for item in included:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"measure", "import_measure", "duty_expression"}:
                attrs = item.get("attributes")
                if isinstance(attrs, dict):
                    found.append(attrs)
                else:
                    found.append(item)
    data = payload.get("data")
    attrs = data.get("attributes") if isinstance(data, dict) else None
    if isinstance(attrs, dict):
        for key in ("import_measures", "import_trade_summary"):
            block = attrs.get(key)
            if isinstance(block, list):
                found.extend(item for item in block if isinstance(item, dict))
            elif isinstance(block, dict):
                found.append(block)
    return found


def _duty_text(measure: dict) -> str | None:
    for key in ("duty_expression", "verbose_duty", "duty_expression_text", "third_country_duty"):
        value = measure.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for inner in ("base", "formatted", "text"):
                text = value.get(inner)
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return None


def _area(measure: dict) -> str | None:
    area = measure.get("geographical_area") or measure.get("geographical_area_id")
    if isinstance(area, dict):
        description = area.get("description") or area.get("id")
        return str(description) if description else None
    if isinstance(area, str):
        return area
    return None


def _simple_percent(text: str) -> float | None:
    """A lone ``3.50 %`` becomes 0.035. Compound expressions stay null."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned.endswith("%"):
        return None
    body = cleaned[:-1].replace(",", "")
    if not body or any(ch.isalpha() for ch in body):
        return None
    try:
        percent = float(body)
    except ValueError:
        return None
    if percent < 0:
        return None
    return percent / 100
