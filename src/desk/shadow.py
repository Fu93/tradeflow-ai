"""Shadow agreement gate. It records a would-be accept and does not apply it.

Phase 1 still requires the user's click. Gated precision is the share of
those would-be accepts that the user later confirmed unchanged, with a
Wilson interval. The certification count is 189 resolved accepts.
"""
from __future__ import annotations

import math
import re
from typing import Any

from src.config.heuristics import (
    DEAL_CURRENCY,
    GATE_CERTIFY_ACCEPTS,
    NEIGHBORHOOD_CHARS,
    WILSON_QUARTIC,
    Z_95,
)
from src.desk.extract import extract_mentions
from src.desk.span_read import currency_marked, per_piece_marked, read_one, span_in_message

# A span that names a total, a duty, or a fee is not a goods unit price,
# even when the model calls it an own offer.
_NOT_UNIT_GOODS = re.compile(
    r"(?i)(?:\btotal\b|\bduty\b|\bshipping\b|\bfreight\b|\bsample\s+fee\b|"
    r"\bmould\b|\bmold\b|合计|合計|总额|總額|运费|運費|关税|關稅|样品费|樣品費|模具)"
)


def _neighborhood(message: str, span: str) -> str:
    """A short window around the cited span. The currency mark often sits beside the number."""
    index = str(message).find(str(span))
    if index < 0:
        return ""
    radius = int(NEIGHBORHOOD_CHARS)
    return str(message)[max(0, index - radius): index + len(span) + radius]


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """Two-sided Wilson interval. ``total`` is resolved shadow accepts, not messages."""
    count = int(total)
    if count <= 0:
        return None
    wins = min(max(int(successes), 0), count)
    phat = wins / count
    zed = float(z)
    z2 = zed * zed
    denom = 1.0 + z2 / count
    centre = (phat + z2 / (2 * count)) / denom
    margin = zed * math.sqrt((phat * (1.0 - phat) + z2 / (int(WILSON_QUARTIC) * count)) / count) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _rule_value(reading: dict[str, Any]) -> float | None:
    if reading.get("status") != "accepted":
        return None
    price = (reading.get("parsed") or {}).get("unit_price")
    if price is None:
        return None
    return float(price)


def _mention_agrees(
    mention: dict[str, Any],
    message: str,
    rule_value: float,
    low: float,
    high: float,
    currency: str,
) -> float | None:
    if mention.get("class") != "own_unit_offer":
        return None
    if mention.get("class") == "instruction":
        return None
    if str(mention.get("speaker") or "") != "supplier":
        return None
    if str(mention.get("polarity") or "") != "affirmative":
        return None
    if bool(mention.get("is_question")):
        return None
    span = str(mention.get("evidence_span") or "")
    if not span_in_message(span, message):
        return None
    if _NOT_UNIT_GOODS.search(span):
        return None
    nearby = _neighborhood(message, span)
    if str(mention.get("currency") or "") != currency:
        return None
    if not (currency_marked(span, currency) or currency_marked(nearby, currency)):
        return None
    if str(mention.get("unit") or "") != "per_piece" and not (per_piece_marked(span) or per_piece_marked(nearby)):
        return None
    amount = read_one(span)
    if amount is None or amount != float(rule_value):
        return None
    if amount < float(low) or amount > float(high):
        return None
    return amount


def _own_offer_amount(
    mention: dict[str, Any],
    message: str,
    low: float,
    high: float,
    currency: str,
) -> tuple[float, str] | None:
    """The amount in a span the model calls the supplier's own unit offer.

    The same tests as the gate, except agreement with the rules: the class,
    speaker, polarity, the span verbatim in the message, a currency and a
    per-piece mark, no total or fee words, and the deal's sanity band. The
    amount is read by code from the span; the model never supplies it.
    """
    if mention.get("class") != "own_unit_offer":
        return None
    if str(mention.get("speaker") or "") != "supplier":
        return None
    if str(mention.get("polarity") or "") != "affirmative" or bool(mention.get("is_question")):
        return None
    span = str(mention.get("evidence_span") or "")
    if not span or not span_in_message(span, message) or _NOT_UNIT_GOODS.search(span):
        return None
    nearby = _neighborhood(message, span)
    if str(mention.get("currency") or "") != currency:
        return None
    if not (currency_marked(span, currency) or currency_marked(nearby, currency)):
        return None
    if str(mention.get("unit") or "") != "per_piece" and not (per_piece_marked(span) or per_piece_marked(nearby)):
        return None
    amount = read_one(span)
    if amount is None or amount < float(low) or amount > float(high):
        return None
    return float(amount), span


def model_offer_token(
    message: str,
    mentions: list[dict[str, Any]] | None,
    candidates: list[str] | None,
    *,
    low: float,
    high: float,
    currency: str = DEAL_CURRENCY,
) -> dict[str, Any] | None:
    """Which of the rule reader's candidate figures the model calls the supplier's own offer.

    Returns ``{"token", "span"}`` only when every own-offer span the model
    labelled reads to one amount, and exactly one candidate token sits inside
    those spans with that amount. Otherwise None. It only chooses among the
    rule reader's own candidates, so it cannot introduce a number, and a
    person still confirms the figure.
    """
    instructed = {
        str(mention.get("evidence_span") or "")
        for mention in mentions or []
        if mention.get("class") == "instruction"
    }
    readings = []
    for mention in mentions or []:
        if str(mention.get("evidence_span") or "") in instructed:
            continue
        found = _own_offer_amount(mention, message, low, high, currency)
        if found is not None:
            readings.append(found)
    amounts = {amount for amount, _span in readings}
    if len(amounts) != 1:
        return None
    amount = amounts.pop()
    matched = []
    for token in candidates or []:
        token = str(token)
        if not token:
            continue
        spans = [span for value, span in readings if token in span]
        if not spans or read_one(token) != amount:
            continue
        matched.append((token, spans[0]))
    if len({token for token, _span in matched}) != 1:
        return None
    token, span = matched[0]
    return {"token": token, "span": span}


def evaluate_shadow(
    message: str,
    reading: dict[str, Any],
    *,
    low: float,
    high: float,
    mentions: list[dict[str, Any]] | None = None,
    extractor_configured: bool = False,
    currency: str = DEAL_CURRENCY,
) -> dict[str, Any]:
    """Would the gate have accepted? Phase 1 does not apply the answer."""
    rule_value = _rule_value(reading)
    span_value = None
    would = False
    instructed = set()
    if mentions:
        for mention in mentions:
            if mention.get("class") == "instruction":
                instructed.add(str(mention.get("evidence_span") or ""))
    if extractor_configured and mentions and rule_value is not None:
        for mention in mentions:
            span = str(mention.get("evidence_span") or "")
            if mention.get("class") == "instruction" or span in instructed:
                continue
            agreed = _mention_agrees(mention, message, rule_value, low, high, currency)
            if agreed is None:
                continue
            span_value = agreed
            would = True
            break
    return {
        "would_accept": would,
        "applied": False,
        "rule_value": rule_value,
        "span_value": span_value,
        "extractor_configured": bool(extractor_configured),
        "auto_accept": False,
    }


def shadow_from_message(
    message: str,
    reading: dict[str, Any],
    *,
    low: float,
    high: float,
    transport=None,
    currency: str = DEAL_CURRENCY,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    extracted = extract_mentions(message, transport=transport, budget_seconds=budget_seconds)
    decision = evaluate_shadow(
        message,
        reading,
        low=low,
        high=high,
        mentions=extracted.get("mentions") or [],
        extractor_configured=bool(extracted.get("configured")),
        currency=currency,
    )
    decision["numeric_field_discarded"] = bool(extracted.get("numeric_field_discarded"))
    decision["mentions"] = extracted.get("mentions") or []
    decision["rejected_rows"] = int(extracted.get("rejected_rows") or 0)
    decision["model"] = extracted.get("model") or ""
    decision["prompt_sha256"] = extracted.get("prompt_sha256") or ""
    decision["response_sha256"] = extracted.get("response_sha256") or ""
    decision["attempts"] = list(extracted.get("attempts") or [])
    decision["model_offer"] = model_offer_token(
        message,
        decision["mentions"],
        list((reading.get("provenance") or {}).get("candidates") or []),
        low=low,
        high=high,
        currency=currency,
    )
    if extracted.get("error"):
        decision["error"] = extracted["error"]
    return decision


def progress(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows are shadow accepts the user has resolved."""
    resolved = [row for row in rows if row.get("would_accept") and row.get("resolved")]
    unchanged = [row for row in resolved if row.get("matched")]
    interval = wilson_interval(len(unchanged), len(resolved))
    return {
        "confirmed_unchanged": len(unchanged),
        "resolved_shadow_accepts": len(resolved),
        "target": int(GATE_CERTIFY_ACCEPTS),
        "wilson_lower": None if interval is None else interval[0],
        "wilson_upper": None if interval is None else interval[1],
        "auto_accept": False,
    }
