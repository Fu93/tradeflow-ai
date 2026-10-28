"""Second number reader. A state machine, not the rule parser's regular expression.

``src.desk.parse`` must not import this module, and this module must not
import ``src.desk.parse``. Agreement between the two readings is evidence
only when the tokenizers are different.
"""
from __future__ import annotations

from src.config.heuristics import SPAN_GROUP

_MARKS = {
    "CNY": ("cny", "rmb", "yuan", "元", "块", "塊", "¥", "￥"),
    "USD": ("usd", "$", "美元"),
    "EUR": ("eur", "€", "欧元", "歐元"),
}
_PER_PIECE = ("per piece", "per pc", "/pc", "each", "元/件", "元/个", "元/只", "一件", "每件", "每个", "每只")
# A currency word glued to a number ('2.55usd', '30RMB', 'EUR4.20') is a mark.
# Any other ASCII letter run is a model code ('3X', 'XT660') and is not an amount.
_CURRENCY_WORDS = frozenset({"usd", "rmb", "cny", "eur", "yuan"})


def _ascii_letter(char: str) -> bool:
    return len(char) == 1 and ("A" <= char <= "Z" or "a" <= char <= "z")


def _letter_run_before(text: str, index: int) -> str:
    start = index
    while start > 0 and _ascii_letter(text[start - 1]):
        start -= 1
    return text[start:index]


def _letter_run_after(text: str, index: int) -> tuple[str, int]:
    end = index
    size = len(text)
    while end < size and _ascii_letter(text[end]):
        end += 1
    return text[index:end], end


def _is_digit(char: str) -> bool:
    # ASCII and full-width digits; float() reads both.
    return "0" <= char <= "9" or "０" <= char <= "９"


def _group_is_thousands(text: str, index: int) -> bool:
    width = int(SPAN_GROUP)
    chunk = text[index:index + width]
    if len(chunk) != width or any(not _is_digit(char) for char in chunk):
        return False
    after = index + width
    if after < len(text) and _is_digit(text[after]):
        return False
    return True


def read_amounts(text: str) -> list[float]:
    """Every decimal amount in ``text``. Commas are thousands or a decimal mark."""
    raw = str(text or "")
    found: list[float] = []
    index = 0
    size = len(raw)
    while index < size:
        if not _is_digit(raw[index]):
            index += 1
            continue
        start = index
        whole = []
        fraction = []
        saw_decimal = False
        while index < size:
            char = raw[index]
            if _is_digit(char):
                if saw_decimal:
                    fraction.append(char)
                else:
                    whole.append(char)
                index += 1
                continue
            if char == "," and not saw_decimal and _group_is_thousands(raw, index + 1):
                index += 1
                continue
            if char in {",", ".", "．"} and not saw_decimal:
                saw_decimal = True
                index += 1
                continue
            break
        if not whole and not fraction:
            continue
        # '3X' and 'XT660' are not amounts. A currency word glued on either
        # side is a mark. A currency ideograph such as 元 is not a letter.
        if start and _ascii_letter(raw[start - 1]):
            if _letter_run_before(raw, start).lower() not in _CURRENCY_WORDS:
                continue
        if index < size and _ascii_letter(raw[index]):
            word, end = _letter_run_after(raw, index)
            if word.lower() not in _CURRENCY_WORDS:
                continue
            index = end
        body = "".join(whole) if whole else "0"
        if fraction:
            body = body + "." + "".join(fraction)
        found.append(float(body))
    return found


def read_one(text: str) -> float | None:
    amounts = read_amounts(text)
    if len(amounts) != 1:
        return None
    return amounts[0]


def span_in_message(span: str, message: str) -> bool:
    text = str(span or "")
    if not text:
        return False
    return text in str(message or "")


def currency_marked(text: str, currency: str) -> bool:
    """True when ``text`` carries a symbol or a name of ``currency``."""
    folded = str(text or "").lower()
    marks = _MARKS.get(str(currency or "").upper(), ())
    return any(str(marker).lower() in folded for marker in marks)


def cny_marked(text: str) -> bool:
    return currency_marked(text, "CNY")


def per_piece_marked(text: str) -> bool:
    folded = str(text or "").lower()
    return any(marker.lower() in folded for marker in _PER_PIECE)
