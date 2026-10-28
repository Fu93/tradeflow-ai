"""Read the amounts a person's quoted words state, for a typed price.

A typed price (PR #39) names the words it came from, such as 28塊5. The desk
accepts the typed amount only when those words state that amount, so typing
5 against 28塊5 is refused. This reads:

* plain numbers: 28.50, ２８．５０
* colloquial yuan: 28塊5 / 28块5毛 / 28元5角 (28.5), 28块5毛3 (28.53)
* Chinese numerals: 二十八块五 (28.5), 二十八元 (28)

A digit that belongs to a colloquial amount (the 5 in 28塊5) is not also read
on its own.
"""
from __future__ import annotations

import re

_FULL_WIDTH = str.maketrans("０１２３４５６７８９．", "0123456789.")
_DIGITS = {ch: value for value, ch in enumerate("零一二三四五六七八九")}
_DIGITS["两"] = _DIGITS["二"]
_DIGITS["兩"] = _DIGITS["二"]
_TEN = 10
_HUNDRED = 100
_THOUSAND_UNIT = 1000
_UNITS = {"十": _TEN, "百": _HUNDRED, "千": _THOUSAND_UNIT}
_YUAN = r"[块塊元圆圓]"
_CN_NUM = r"[零一二两兩三四五六七八九十百千]{1,8}"
_CN_DIGIT = r"[零一二两兩三四五六七八九]"

# 28塊5, 28块5毛, 28元5角3分: whole yuan, then tenths, then an optional hundredth.
_COLLOQUIAL = re.compile(
    r"(?<![\d.])(\d{1,6})\s*" + _YUAN + r"\s*(\d)(?![\d.])\s*[毛角]?(?:\s*(\d)(?![\d.])\s*分?)?"
)
# 二十八块五, 二十八元五角
_CHINESE = re.compile(r"(" + _CN_NUM + r")\s*" + _YUAN + r"(?:\s*(" + _CN_DIGIT + r")\s*[毛角]?)?")
_PLAIN = re.compile(r"(?<![\d.])(\d{1,6}(?:\.\d{1,4})?)(?![\d.])")


def _chinese_number(text: str) -> int | None:
    total = 0
    digit: int | None = None
    for char in text:
        if char in _DIGITS:
            if digit is not None:
                return None
            digit = _DIGITS[char]
        elif char in _UNITS:
            total += (1 if digit is None else digit) * _UNITS[char]
            digit = None
        else:
            return None
    return total + (digit or 0)


def amounts_in_words(words: str) -> set[float]:
    """Every amount the words state, rounded to the cent."""
    text = str(words or "").translate(_FULL_WIDTH)
    found: set[float] = set()
    taken: list[tuple[int, int]] = []
    for match in _COLLOQUIAL.finditer(text):
        whole, tenths, hundredths = match.groups()
        value = int(whole) + int(tenths) / _TEN + (int(hundredths) / _HUNDRED if hundredths else 0)
        found.add(round(value, 2))
        taken.append(match.span())
    for match in _CHINESE.finditer(text):
        whole = _chinese_number(match.group(1))
        if whole is None:
            continue
        tenths = _DIGITS.get(match.group(2) or "", 0)
        found.add(round(whole + tenths / _TEN, 2))
        taken.append(match.span())
    for match in _PLAIN.finditer(text):
        start, end = match.span()
        if any(left <= start and end <= right for left, right in taken):
            continue
        found.add(round(float(match.group(1)), 2))
    return found


def words_state(words: str, amount: float, tolerance: float) -> bool:
    """Do the words state this amount?"""
    return any(abs(value - float(amount)) <= tolerance for value in amounts_in_words(words))
