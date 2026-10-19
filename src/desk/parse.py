"""Quote reading for the desk.

The deterministic reader supplies every number the solver may see. A model
JSON is accepted and then discarded. A quote is accepted only when the raw
text contains exactly one distinct amount and that amount is a usable CNY
figure. Anything else waits on the review queue.
"""
from __future__ import annotations

import re
from typing import Any

from src.engine.bilingual import InboundIntentClassifier
from src.engine.quote_fields import extract_stage1, price_the_solver_may_use

_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_PRICE_KEYS = ("price", "unit_price", "mentioned_price", "quoted_amount", "cny_amount")


def numeric_tokens(text: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0)
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        if value != value:
            continue
        found.append((token, value))
    return found


def _distinct(values: list[float]) -> list[float]:
    keys: list[float] = []
    for value in values:
        key = round(float(value), 4)
        if key not in keys:
            keys.append(key)
    return keys


def _llm_price(fields: dict | None) -> Any:
    if not isinstance(fields, dict):
        return None
    for key in _PRICE_KEYS:
        if key in fields and fields.get(key) is not None:
            return fields.get(key)
    return None


def parse_quote(raw_text: str, llm_fields: dict | None = None) -> dict[str, Any]:
    """Return parsed fields, confidence, status, and provenance.

    ``status`` is ``accepted`` only for a single traceable CNY amount.
    ``unit_price`` is None whenever the quote is not accepted, so a rejected
    or model-supplied figure cannot reach the solver.
    """
    text = raw_text or ""
    stage1 = extract_stage1(text)
    quoted = InboundIntentClassifier.extract_quoted_price(text)
    ignored = _llm_price(llm_fields)
    usable = price_the_solver_may_use(None, stage1.get("price"), ignored)
    tokens = numeric_tokens(text)
    distinct = _distinct([value for _, value in tokens])
    cny = quoted.cny_amount
    traceable = False
    if cny is not None and len(distinct) == 1 and usable is not None:
        traceable = round(float(usable), 4) == distinct[0] and round(float(cny), 4) == distinct[0]
        if traceable:
            traceable = any(token in text for token, _value in tokens)
    accepted = bool(traceable)
    unit_price = float(usable) if accepted and usable is not None else None
    parsed = {
        "unit_price": unit_price,
        "quoted_amount": quoted.amount,
        "currency": quoted.currency,
        "moq": stage1.get("moq"),
        "lead_time_days": stage1.get("lead_time_days"),
        "deposit_pct": stage1.get("deposit_pct"),
        "freight": None,
        "duty_rate": None,
        "fees": None,
    }
    provenance = {
        "reader": "stage1",
        "llm_price_ignored": ignored is not None or isinstance(llm_fields, dict),
        "quoted_amount": quoted.amount,
        "currency": quoted.currency,
        "cny_amount": cny,
        "currency_note": quoted.note,
        "distinct_amounts": distinct,
        "tokens": [token for token, _value in tokens],
    }
    return {
        "parsed": parsed,
        "confidence": "high" if accepted else "low",
        "status": "accepted" if accepted else "review",
        "provenance": provenance,
    }
