"""Read how long a supplier says its quote stands.

The desk shows the result next to the quote. It never changes the ranking,
the counter or the draft: those are re-derived from the chain when a draft is
sent, and a figure that depended on today's date would not re-derive.

Only a statement tied to a validity word counts ("报价有效期 7 天", "3天内有效",
"有效期至11月5日", "valid for 2 weeks", "valid until 2026-11-05"). Shelf life,
certificates and warranties are not quote validity and are skipped. Business
days are skipped, because the calendar date they end on depends on holidays
the desk does not know. Two statements that end on different dates are
reported as a conflict, with no date.

A duration counts from when the desk received the message, which is not
always when the supplier sent it. The desk says so next to the date.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any

from src.config.heuristics import (
    DAYS_PER_WEEK,
    MONTHS_PER_YEAR,
    QUOTE_VALIDITY_MAX_DAYS,
    QUOTE_VALIDITY_YEAR_ROLL_DAYS,
)
from src.desk.parse import _visible_text

_FULL_WIDTH = str.maketrans("０１２３４５６７８９：／．－", "0123456789:/.-")
_CN_DIGITS = {ch: value for value, ch in enumerate("零一二三四五六七八九")}
_CN_DIGITS["两"] = _CN_DIGITS["二"]
_CN_DIGITS["兩"] = _CN_DIGITS["二"]
_CN_TEN = "十"
_CN_TEN_VALUE = 10
_EN_COUNTS = {
    word: value
    for value, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    )
}
_EN_COUNTS["a"] = _EN_COUNTS["one"]
_EN_COUNTS["an"] = _EN_COUNTS["one"]
_MONTHS = {
    name: index
    for index, name in enumerate(calendar.month_name)
    if name
}
_MONTHS.update({name: index for index, name in enumerate(calendar.month_abbr) if name})
_MONTHS = {name.lower(): index for name, index in _MONTHS.items()}
_MONTHS["sept"] = _MONTHS["sep"]

_COUNT = r"(?P<n>\d{1,3}|[零一二两兩三四五六七八九十]{1,3})"
_EN_COUNT = r"(?P<n>\d{1,3}|zero|one|two|three|four|five|six|seven|eight|nine|ten|an?)"
_ZH_UNIT = r"(?P<unit>个?工作日|個?工作日|天|日|周|週|星期|个月|個月|月)"
_EN_UNIT = r"(?P<unit>business days?|working days?|days?|weeks?|months?)"
_PRICE_WORD = r"(?:报价|報價|价格|價格|价钱|價錢|单价|單價|此价|此價|本价|本價|价|價)"

_ZH_DURATION = (
    re.compile(_PRICE_WORD + r"?\s*有效期(?:为|為|是)?\s*[:：]?\s*" + _COUNT + r"\s*" + _ZH_UNIT),
    re.compile(_COUNT + r"\s*" + _ZH_UNIT + r"\s*(?:之?内|之?內)\s*有效"),
)
_EN_DURATION = (
    re.compile(r"\b(?:valid(?:ity)?|good)\s*(?:for|:)?\s*" + _EN_COUNT + r"\s*" + _EN_UNIT + r"\b", re.I),
    re.compile(r"\b" + _EN_COUNT + r"[\s-]*" + _EN_UNIT + r"\s+validity\b", re.I),
)
_ZH_DATE = (
    re.compile(
        r"有效(?:期)?\s*(?:至|到|截至|截止|截止到|截止至)\s*[:：]?\s*"
        r"(?:(?P<y>\d{4})\s*[年\-/.]\s*)?(?P<m>\d{1,2})\s*[月\-/.]\s*(?P<d>\d{1,2})\s*(?:日|号|號)?"
    ),
    re.compile(
        r"(?:(?P<y>\d{4})\s*年\s*)?(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*(?:日|号|號)\s*(?:之?前|以前)\s*有效"
    ),
)
_EN_DATE = re.compile(
    r"\bvalid\s+(?:until|till|til|through|thru|to)\s+"
    r"(?:(?P<iso>\d{4}-\d{1,2}-\d{1,2})"
    r"|(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y1>\d{4}))?"
    r"|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m2>[A-Za-z]{3,9})\.?(?:,?\s+(?P<y2>\d{4}))?)",
    re.I,
)
# Shelf life, certificates and warranties use the same words for something else.
_NOT_A_QUOTE = re.compile(
    r"保质|保質|质保|質保|保修|证书|證書|执照|執照|产品有效期|產品有效期|商品有效期|"
    r"certificate|licen[cs]e|warranty|guarantee|shelf|expiry date of the product",
    re.I,
)


def _count(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    if token in _EN_COUNTS:
        return _EN_COUNTS[token]
    if not token or any(ch not in _CN_DIGITS and ch != _CN_TEN for ch in token):
        return None
    if _CN_TEN not in token:
        return _CN_DIGITS[token] if len(token) == 1 else None
    head, _, tail = token.partition(_CN_TEN)
    if _CN_TEN in tail or len(head) > 1 or len(tail) > 1:
        return None
    tens = _CN_DIGITS[head] if head else 1
    ones = _CN_DIGITS[tail] if tail else 0
    return tens * _CN_TEN_VALUE + ones


def _add_months(start: date, months: int) -> date:
    total = start.month - 1 + months
    year = start.year + total // int(MONTHS_PER_YEAR)
    month = total % int(MONTHS_PER_YEAR) + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _working_days(unit: str) -> bool:
    unit = unit.lower()
    return "工作日" in unit or "business" in unit or "working" in unit


def _end_of(received: date, count: int, unit: str) -> date:
    unit = unit.lower()
    if unit.startswith("month") or unit.endswith("月"):
        return _add_months(received, count)
    if unit.startswith("week") or unit in {"周", "週", "星期"}:
        return received + timedelta(days=count * int(DAYS_PER_WEEK))
    return received + timedelta(days=count)


def _dated(received: date, year: str | None, month: int, day: int) -> date | None:
    try:
        when = date(int(year) if year else received.year, month, day)
    except ValueError:
        return None
    if not year and (received - when).days > int(QUOTE_VALIDITY_YEAR_ROLL_DAYS):
        # "1月5日" written in late December is next January.
        try:
            when = date(received.year + 1, month, day)
        except ValueError:
            return None
    return when


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in "。！？!?\n;；")
    rights = [pos for pos in (text.find(mark, end) for mark in "。！？!?\n;；") if pos >= 0]
    return text[left + 1:min(rights) if rights else len(text)]


def _received_date(received_at: str | None) -> date | None:
    if not received_at:
        return None
    try:
        return datetime.fromisoformat(str(received_at).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def read_validity(raw_text: str, received_at: str | None) -> dict[str, Any] | None:
    """The date the supplier's own quote stops standing, or None when not stated.

    Returns one of:

    * ``{"status": "stated", "valid_until": "YYYY-MM-DD", "span", "spans",
      "counted_from": "received" | "stated_date", "received_on"}``
    * ``{"status": "working_days", "valid_until": None, "span", "spans"}``: the
      supplier counts in working days, so no calendar date is given.
    * ``{"status": "conflict", "valid_until": None, "spans"}``: statements that
      do not end on one date.

    Every span is verbatim from the message.
    """
    received = _received_date(received_at)
    if received is None:
        return None
    original = _visible_text(raw_text or "")
    text = original.translate(_FULL_WIDTH)
    found: list[tuple[date, str, str]] = []
    working: list[str] = []

    def keep(match: re.Match[str], when: date | None, counted_from: str) -> None:
        if when is None:
            return
        if _NOT_A_QUOTE.search(_sentence(text, match.start(), match.end())):
            return
        if (when - received).days > int(QUOTE_VALIDITY_MAX_DAYS):
            return
        found.append((when, original[match.start():match.end()].strip(), counted_from))

    for pattern in _ZH_DURATION + _EN_DURATION:
        for match in pattern.finditer(text):
            count = _count(match.group("n"))
            if count is None or count <= 0:
                continue
            unit = match.group("unit")
            # A bare 有效期 in months may be shelf life; a price word must come first.
            if (unit.endswith("月") and pattern is _ZH_DURATION[0]
                    and not re.match(_PRICE_WORD, match.group(0))):
                continue
            if _working_days(unit):
                if not _NOT_A_QUOTE.search(_sentence(text, match.start(), match.end())):
                    working.append(original[match.start():match.end()].strip())
                continue
            keep(match, _end_of(received, count, unit), "received")
    for pattern in _ZH_DATE:
        for match in pattern.finditer(text):
            keep(match, _dated(received, match.group("y"), int(match.group("m")), int(match.group("d"))),
                 "stated_date")
    for match in _EN_DATE.finditer(text):
        when = None
        if match.group("iso"):
            year, month, day = (int(part) for part in match.group("iso").split("-"))
            when = _dated(received, str(year), month, day)
        else:
            name = (match.group("m1") or match.group("m2") or "").lower()
            day_token = match.group("d1") or match.group("d2")
            if name in _MONTHS and day_token:
                when = _dated(received, match.group("y1") or match.group("y2"), _MONTHS[name], int(day_token))
        keep(match, when, "stated_date")

    if not found and not working:
        return None
    if not found:
        spans = list(dict.fromkeys(working))
        return {"status": "working_days", "valid_until": None, "span": spans[0], "spans": spans}
    dates = {when for when, _span, _how in found}
    spans = list(dict.fromkeys([span for _when, span, _how in found] + working))
    if len(dates) > 1 or working:
        return {"status": "conflict", "valid_until": None, "spans": spans}
    when, span, counted_from = found[0]
    return {
        "status": "stated",
        "valid_until": when.isoformat(),
        "span": span,
        "spans": spans,
        "counted_from": counted_from,
        "received_on": received.isoformat(),
    }
