"""Pick the row of a supplier's tier table that matches the deal quantity.

Factories often quote a ladder ("1000-4999 把 28 元/把，5000 把以上 26 元/把").
The rule reader leaves such a message for a person, and its pre-selected
figure is just the first row. This module reads the ladder and names the row
for the deal's quantity, so the pre-selection is the right row. It never
accepts a price: the quote stays in review and a person confirms the figure.

It is deliberately separate from ``parse_quote``. The rule reader is scored
on a held-out set, and that score must not move while the set is in use.

A table is read only when every priced line has a quantity, every row has one
price in the deal's currency, and the rows do not overlap. Anything else
returns None and the desk keeps the rule reader's pre-selection.
"""
from __future__ import annotations

import re
from typing import Any

from src.desk.parse import _MIN_TIER_QTY, _THOUSAND, _WAN, _visible_text

# The Chinese comma stays: it separates rows, while an ASCII comma before three
# digits is a thousands mark.
_FULL_WIDTH = str.maketrans("０１２３４５６７８９．：～－＋", "0123456789.:~-+")
# Possessive quantifiers: a long digit run must not make the pattern backtrack.
# Each number starts at the beginning of a digit run, so a search cannot retry
# from inside a long run (that made a 10,000-digit paste quadratic).
_QTY_NUM = r"(?<![\d,.])(\d{1,3}+(?:,\d{3})++|\d++)(?:\s*+(k|K|千|万|萬))?"
_QTY_UNIT = r"(?:件|个|個|把|只|套|台|条|條|双|雙|支|张|張|pcs?\b|pieces?\b|units?\b|sets?\b|pairs?\b)"
_RANGE = re.compile(
    _QTY_NUM + r"\s*+" + _QTY_UNIT + r"?\s*+(?:-|~|—|–|至|到|to)\s*+" + _QTY_NUM + r"\s*+" + _QTY_UNIT + r"?",
    re.I,
)
_OPEN_AFTER = re.compile(
    _QTY_NUM + r"\s*+" + _QTY_UNIT + r"?\s*+(?:及以上|以上|或以上|起|\+|or more|and above|and up|plus)", re.I
)
_OPEN_BEFORE = re.compile(
    r"(?:≥|>=|满|滿|over|above|moq|起订量|起訂量|起订|起訂)\s*+[:：]?\s*+" + _QTY_NUM + r"\s*+" + _QTY_UNIT + r"?",
    re.I,
)
_SINGLE = re.compile(_QTY_NUM + r"\s*+" + _QTY_UNIT, re.I)
_PRICE = re.compile(
    r"(?:(?:¥|￥|rmb|cny)\s*+(\d++(?:\.\d++)?))|(?:(?<![\d.])(\d++(?:\.\d++)?)\s*+(?:元|块|塊|rmb|cny))",
    re.I,
)
_OTHER_CURRENCY = re.compile(r"(?i)\$|usd|us\$|美元|eur|€|欧元|歐元")
_SEGMENTS = re.compile(r"[;；\n。，]|,(?!\d{3})")


def _quantity(number: str, scale: str | None) -> float:
    value = float(number.replace(",", ""))
    if scale and scale.lower() in {"k", "千"}:
        value *= _THOUSAND
    elif scale in {"万", "萬"}:
        value *= _WAN
    return value


class _NotALadder(Exception):
    """A priced line that is not a readable ladder row."""


def _row(segment: str) -> dict[str, Any] | None:
    """One ladder row, or None for a line with no price. Raises for a line that cannot be read."""
    prices = [match for match in _PRICE.finditer(segment)]
    if not prices:
        return None
    if len(prices) != 1 or _OTHER_CURRENCY.search(segment):
        raise _NotALadder
    price = prices[0]
    token = price.group(1) or price.group(2)
    head = segment[:price.start()] + " " * (price.end() - price.start()) + segment[price.end():]
    match = _RANGE.search(head)
    if match:
        low_digits, low_scale, high_digits, high_scale = match.groups()
        low = _quantity(low_digits, low_scale)
        high = _quantity(high_digits, high_scale)
        if high < low:
            raise _NotALadder
        return {"low": low, "high": high, "token": token, "text": segment.strip()}
    match = _OPEN_AFTER.search(head) or _OPEN_BEFORE.search(head)
    if match:
        return {"low": _quantity(match.group(1), match.group(2)), "high": None, "token": token,
                "text": segment.strip()}
    singles = list(_SINGLE.finditer(head))
    if len(singles) == 1:
        # "1000件 28元": a breakpoint, open upward until the next row.
        return {"low": _quantity(singles[0].group(1), singles[0].group(2)), "high": None, "token": token,
                "text": segment.strip()}
    raise _NotALadder


def tier_for_quantity(raw_text: str, quantity: float | None) -> dict[str, Any] | None:
    """The ladder row for ``quantity``, or None when the message is not a clear ladder.

    Returns ``{"token", "row", "rows", "below_smallest", "quantity"}``. ``token`` is the price
    digits exactly as written; ``row`` is that line of the message.
    """
    if quantity is None or float(quantity) <= 0:
        return None
    text = _visible_text(raw_text or "").translate(_FULL_WIDTH)
    rows: list[dict[str, Any]] = []
    for segment in _SEGMENTS.split(text):
        if not segment.strip():
            continue
        try:
            row = _row(segment)
        except _NotALadder:
            return None
        if row is not None:
            rows.append(row)
    if len(rows) < 2:
        return None
    lows = [row["low"] for row in rows]
    if len(set(lows)) != len(lows):
        return None
    # An open row ("1000件以上") must name a real order size; a range may start at 1.
    if any(row["high"] is None and row["low"] < float(_MIN_TIER_QTY) for row in rows):
        return None
    if len({row["token"] for row in rows}) != len(rows):
        return None
    rows.sort(key=lambda row: row["low"])
    qty = float(quantity)
    for current, following in zip(rows, rows[1:], strict=False):
        if current["high"] is None:
            continue
        if current["high"] > following["low"]:
            return None
        if current["high"] == following["low"] and qty == following["low"]:
            # "1k-5k" then "5k+": an order of exactly 5000 could be either row.
            return None
    eligible = [row for row in rows if row["low"] <= qty]
    if not eligible:
        smallest = rows[0]
        return {"token": smallest["token"], "row": smallest["text"], "rows": len(rows), "below_smallest": True,
                "quantity": qty}
    chosen = eligible[-1]
    if chosen["high"] is not None and qty > chosen["high"]:
        # Between rows, or above a closed top row: the ladder does not say.
        return None
    return {"token": chosen["token"], "row": chosen["text"], "rows": len(rows), "below_smallest": False,
            "quantity": qty}
