"""Read a supplier's minimum order and lead time, for display next to the quote.

Both are shown with the words they came from. Neither changes the ranking,
the counter or the draft (the send guard re-derives those from the chain).
The desk flags a minimum order above the deal's quantity, because a supplier
can rank first on price and still be unable to take the order.

Only a number tied to an MOQ or lead-time word counts. 万 and k are scaled.
Two different statements give a conflict, with no figure. Working days are
shown without a day count, since holidays are unknown.
"""
from __future__ import annotations

import re
from typing import Any

from src.config.heuristics import DAYS_PER_WEEK
from src.desk.parse import _THOUSAND, _WAN, _visible_text

_FULL_WIDTH = str.maketrans("０１２３４５６７８９．：～－", "0123456789.:~-")
# Anchored to the start of a digit run so a long run cannot backtrack.
# Bounded digit counts: a quantity or a day count is never a 20,000-digit
# number, and converting one would raise.
_NUM = r"(?<![\d,.])(\d{1,3}+(?:,\d{3}){1,3}+|\d{1,9}+(?:\.\d{1,4}+)?)(?![\d,])\s*+(k|K|千|万|萬)?"
_PIECES = r"(?:件|个|個|把|只|套|台|条|條|双|雙|支|张|張|pcs?\b|pieces?\b|units?\b|sets?\b|pairs?\b)"
_MOQ = (
    re.compile(
        r"(?:最低起订量|最低起訂量|最小起订量|最小起訂量|起订量|起訂量|起订|起訂|最低订量|最低訂量|最少订|最少訂|"
        r"\bMOQ\b|minimum order(?: quantity)?)"
        r"\s*+(?:is|为|為|是)?\s*+[:：]?\s*+" + _NUM + r"\s*+" + _PIECES + r"?",
        re.I,
    ),
    re.compile(_NUM + r"\s*+" + _PIECES + r"?\s*+(?:起订|起訂|起批)"),
)
_DAY_UNIT = r"(个?工作日|個?工作日|working days?|business days?|天|日|days?|周|週|weeks?)"
_LEAD = (
    re.compile(
        r"(?:交期|交货期|交貨期|货期|貨期|生产周期|生產週期|lead[\s-]?time|delivery time|production time)"
        r"\s*+(?:is|为|為|是|约|約|about|around)?\s*+[:：]?\s*+(?:约|約|about|around)?\s*+"
        r"(?<![\d,.])(\d{1,4}+)(?!\d)(?:\s*+[-~至到]\s*+(\d{1,4}+)(?!\d))?\s*+" + _DAY_UNIT,
        re.I,
    ),
    re.compile(
        r"(?<![\d,.])(\d{1,4}+)(?!\d)(?:\s*+[-~至到]\s*+(\d{1,4}+)(?!\d))?\s*+" + _DAY_UNIT +
        r"\s*+(?:内|內)?\s*+(?:出货|出貨|交货|交貨|发货|發貨|完成|ship|delivery)",
        re.I,
    ),
)


def _scaled(digits: str, scale: str | None) -> float:
    value = float(digits.replace(",", ""))
    if scale and scale.lower() in {"k", "千"}:
        value *= _THOUSAND
    elif scale in {"万", "萬"}:
        value *= _WAN
    return value


def _text(raw_text: str) -> tuple[str, str]:
    original = _visible_text(raw_text or "")
    return original, original.translate(_FULL_WIDTH)


def read_moq(raw_text: str) -> dict[str, Any] | None:
    """``{"status": "stated", "quantity", "span"}``, ``{"status": "conflict", "spans"}`` or None."""
    original, text = _text(raw_text)
    found: dict[float, str] = {}
    for pattern in _MOQ:
        for match in pattern.finditer(text):
            quantity = _scaled(match.group(1), match.group(2))
            if quantity < 1:
                continue
            found.setdefault(quantity, original[match.start():match.end()].strip())
    if not found:
        return None
    if len(found) > 1:
        return {"status": "conflict", "quantity": None, "spans": list(found.values())}
    quantity, span = next(iter(found.items()))
    return {"status": "stated", "quantity": int(quantity) if quantity.is_integer() else quantity, "span": span}


def read_lead_time(raw_text: str) -> dict[str, Any] | None:
    """``{"status": "stated", "days", "span"}`` (a range gives its upper end),
    ``{"status": "working_days", "span"}``, ``{"status": "conflict", "spans"}`` or None."""
    original, text = _text(raw_text)
    days: dict[int, str] = {}
    working: list[str] = []
    for pattern in _LEAD:
        for match in pattern.finditer(text):
            span = original[match.start():match.end()].strip()
            low, high, unit_word = match.groups()
            unit = unit_word.lower()
            if "工作日" in unit or "working" in unit or "business" in unit:
                working.append(span)
                continue
            upper = int(high or low)
            if unit.startswith("week") or unit in {"周", "週"}:
                upper *= int(DAYS_PER_WEEK)
            if upper <= 0:
                continue
            days.setdefault(upper, span)
    if not days and not working:
        return None
    if not days:
        return {"status": "working_days", "days": None, "span": working[0]}
    if len(days) > 1 or working:
        return {"status": "conflict", "days": None, "spans": list(days.values()) + working}
    value, span = next(iter(days.items()))
    return {"status": "stated", "days": value, "span": span}
