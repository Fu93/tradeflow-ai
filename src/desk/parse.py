"""Quote reading for the desk.

The deterministic reader supplies every number the solver may see. A model
JSON is accepted and then discarded. A price is accepted only when one
currency-anchored amount survives the filters and that digit string is in
the raw text. MOQ, lead time, dates, and signatures are not prices.
Negations, quoted history, and instruction lines are not prices either.
"""
from __future__ import annotations

import re
from typing import Any

from src.engine.bilingual import InboundIntentClassifier
from src.engine.quote_fields import price_the_solver_may_use

_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?")
_PRICE_KEYS = ("price", "unit_price", "mentioned_price", "quoted_amount", "cny_amount")
_QUOTE_SPLIT = re.compile(
    r"(?im)^(?:-----original message-----|-----\s*转发邮件\s*-----|原始邮件|发件人\s*[:：]|from\s*:)"
)
_INJECTION = re.compile(
    r"(?i)\b(?:system\s*:|record price as|ignore previous|you must (?:set|use)|as an ai)\b"
)
_NEGATION = re.compile(
    r"(?i)(cannot accept|can't accept|can not accept|unable to accept|too low|"
    r"do not accept|not acceptable|reject(?:ed)?|不能接受|无法接受|無法接受|不接受|太低)"
)
_ECHO = re.compile(r"(?i)(previous price|your target|原价|原價|上次报价|上次報價)")
_NOT_PRICE = re.compile(
    r"(?i)(moq|pcs|pieces|qty|quantity|lead\s*time|days?|天|起订|起訂|订量|訂量|"
    r"deposit|定金|增值税|vat|tel|phone|电话|電話|mobile|微信|wechat|%\s*$)"
)


def numeric_tokens(text: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0)
        value = _number(token)
        if value is None:
            continue
        found.append((token, value))
    return found


def _number(token: str) -> float | None:
    text = token.strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"\d+,\d{2}", text):
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None
    if value != value:
        return None
    return value


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


def _visible_text(raw_text: str) -> str:
    """Drop quoted history, signatures, and instruction lines."""
    text = _QUOTE_SPLIT.split(raw_text or "", maxsplit=1)[0]
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped in {"--", "-- "}:
            break
        if _INJECTION.search(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def _sentence_of(text: str, start: int) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind("。", 0, start), text.rfind(".", 0, start))
    right_candidates = [text.find(mark, start) for mark in ("\n", "。")]
    right_candidates = [item for item in right_candidates if item >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right]


def _anchored_prices(text: str) -> list[dict[str, Any]]:
    """Currency-adjacent amounts, skipping negations and non-price labels."""
    found: list[dict[str, Any]] = []
    patterns = (
        re.compile(
            r"(?i)(?:单价|單價|报价|報價|价格|價格|unit price|price)\s*[:：]?\s*"
            r"([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)\s*"
            r"(元|块|塊|cny|rmb|￥|¥)?"
        ),
        re.compile(
            r"(?:￥|¥)\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)"
        ),
        re.compile(
            r"(?i)([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)\s*(cny|rmb|元|块|塊)"
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            sentence = _sentence_of(text, match.start())
            if _NEGATION.search(sentence) or _ECHO.search(sentence):
                continue
            window = text[max(0, match.start() - 24):match.end() + 16]
            if _NOT_PRICE.search(window) and not re.search(
                r"(?i)单价|單價|报价|報價|价格|價格|unit price|￥|¥|cny|rmb|元",
                window,
            ):
                continue
            token = match.group(1)
            value = _number(token)
            if value is None:
                continue
            if token not in text:
                continue
            found.append({"token": token, "value": value, "span": (match.start(), match.end())})
    unique: list[dict[str, Any]] = []
    seen: set[float] = set()
    for item in found:
        key = round(float(item["value"]), 4)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_quote(raw_text: str, llm_fields: dict | None = None) -> dict[str, Any]:
    """Return parsed fields, confidence, status, and provenance.

    ``status`` is ``accepted`` only for one traceable CNY price. ``unit_price``
    is None otherwise, so a model figure, a negation, or a second price cannot
    reach the solver.
    """
    text = raw_text or ""
    visible = _visible_text(text)
    ignored = _llm_price(llm_fields)
    quoted = InboundIntentClassifier.extract_quoted_price(visible)
    usable = price_the_solver_may_use(None, None, ignored)
    del usable
    anchors = _anchored_prices(visible)
    tokens = numeric_tokens(text)
    distinct = _distinct([value for _, value in tokens])
    reason = None
    unit_price = None
    accepted = False
    if len(anchors) == 1:
        anchor = anchors[0]
        settled = InboundIntentClassifier.extract_quoted_price(
            "单价 " + anchor["token"] + " 元"
        )
        cny = settled.cny_amount
        if (
            cny is not None
            and anchor["token"] in text
            and round(float(cny), 4) == round(float(anchor["value"]), 4)
        ):
            accepted = True
            unit_price = float(cny)
        else:
            reason = "no usable CNY amount"
    elif len(anchors) > 1:
        reason = "more than one price"
    else:
        reason = "no currency-anchored price"
    stage_text = visible if visible.strip() else text
    from src.engine.quote_fields import extract_stage1

    stage1 = extract_stage1(stage_text)
    parsed = {
        "unit_price": unit_price,
        "quoted_amount": None if not anchors else anchors[0]["value"],
        "currency": "CNY" if accepted else quoted.currency,
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
        "quoted_amount": parsed["quoted_amount"],
        "currency": parsed["currency"],
        "cny_amount": unit_price,
        "currency_note": quoted.note,
        "distinct_amounts": distinct,
        "tokens": [token for token, _value in tokens],
        "candidates": [item["token"] for item in anchors],
        "review_reason": None if accepted else reason,
    }
    return {
        "parsed": parsed,
        "confidence": "high" if accepted else "low",
        "status": "accepted" if accepted else "review",
        "provenance": provenance,
    }


def price_from_token(raw_text: str, token: str) -> float | None:
    """A human may pick a token only when that exact digit string is in the text."""
    if not token or token not in (raw_text or ""):
        return None
    value = _number(token)
    if value is None:
        return None
    settled = InboundIntentClassifier.extract_quoted_price("单价 " + token + " 元")
    if settled.cny_amount is None:
        return None
    if round(float(settled.cny_amount), 4) != round(float(value), 4):
        return None
    return float(settled.cny_amount)
