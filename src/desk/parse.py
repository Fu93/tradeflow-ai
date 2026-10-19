"""Quote reading for the desk.

The deterministic reader supplies every number the solver may see. A model
JSON is accepted and then discarded. A price is accepted only when one
CNY unit price survives the filters and that digit string is in the raw
text. Another currency is never rewritten as CNY. Ranges, questions,
echoes of our offer, fees, forwarded history, and instruction lines stay
out of the solve.
"""
from __future__ import annotations

import re
from typing import Any

from src.engine.bilingual import InboundIntentClassifier
from src.engine.quote_fields import price_the_solver_may_use

_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?")
_PRICE_KEYS = ("price", "unit_price", "mentioned_price", "quoted_amount", "cny_amount")
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_QUOTE_SPLIT = re.compile(
    r"(?im)^(?:-----original message-----|-----\s*转发邮件\s*-----|----\s*转发邮件\s*----|"
    r"-{6,}\s*forwarded message\s*-{6,}|原始邮件|发件人\s*[:：]|from\s*:|on .+wrote:\s*)$"
)
_INJECTION = re.compile(
    r"(?i)(?:\bsystem\s*:|record price as|ignore previous|you must (?:set|use)|as an ai|"
    r"note to the assistant|update your records|agreed price)"
)
_NEGATION = re.compile(
    r"(?i)(cannot accept|can't accept|can not accept|unable to accept|too low|"
    r"do not accept|not acceptable|reject(?:ed)?|不能接受|无法接受|無法接受|不接受|太低)"
)
_ECHO = re.compile(
    r"(?i)(previous price|your target|your offer|you offered|we offered|原价|原價|"
    r"上次报价|上次報價|上次|您报|你报|你方)"
)
_QUESTION = re.compile(
    r"(?i)(?:\?|？|\b(?:can you|could you|would you|will you|do you|can we|could we)\b|能否|吗|嗎)"
)
_DATE = re.compile(r"(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?")
_FEE = re.compile(
    r"(?i)(freight|shipping|postage|courier|sample|mould|mold|tooling|"
    r"运费|運費|快递|快遞|样品|樣品|模具|开模|開模|总价|總價|合计|合計|总额|總額|total amount)"
)
_FOREIGN = re.compile(r"(?i)(usd|eur|gbp|hkd|jpy|aud|cad|美元|欧元|歐元|英镑|英鎊|港币|港幣|日元|\$|€|£)")
_CNY = re.compile(r"(?i)(?:cny|rmb|元|块|塊|￥|¥)")
_RANGE = re.compile(rf"(?i)({_NUM})\s*(?:-|–|~|到|至|\bto\b)\s*({_NUM})")
_AMOUNT = re.compile(
    rf"(?i)(?P<pre>cny|rmb|usd|eur|gbp|hkd|jpy|￥|¥|\$|€|£)?\s*"
    rf"(?P<num>{_NUM})\s*"
    rf"(?P<post>cny|rmb|usd|eur|gbp|hkd|jpy|元|块|塊|美元|欧元|歐元|英镑|英鎊|\$|€|£)?"
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
    right_candidates = [text.find(mark, start) for mark in ("\n", "。", "?", "？")]
    right_candidates = [item for item in right_candidates if item >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    end = right + 1 if right < len(text) and text[right] in "?？" else right
    return text[left + 1:end]


def _not_a_price_range(text: str, match: re.Match[str]) -> bool:
    """Dates and telephone numbers are not price ranges."""
    window = text[max(0, match.start() - 1):min(len(text), match.end() + 6)]
    if _DATE.search(window):
        return True
    for group in (match.group(1), match.group(2)):
        digits = re.sub(r"\D", "", group)
        if len(digits) >= 7 and "." not in group and "," not in group:
            return True
    around = text[max(0, match.start() - 8):min(len(text), match.end() + 12)]
    compact = re.sub(r"[\s-]", "", around)
    if re.search(r"\d{7,}", compact):
        return True
    return False


def _range_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _RANGE.finditer(text):
        if _not_a_price_range(text, match):
            continue
        spans.append((match.start(), match.end()))
    return spans


def _inside(span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= left or start >= right) for left, right in ranges)


def _anchored_prices(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """CNY unit-price candidates, other money, and whether a range was seen."""
    units: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    ranges = _range_spans(text)
    seen: set[float] = set()
    for match in _AMOUNT.finditer(text):
        token = match.group("num")
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        if not pre and not post:
            continue
        value = _number(token)
        if value is None or token not in text:
            continue
        span = (match.start(), match.end())
        if _inside(span, ranges):
            continue
        sentence = _sentence_of(text, match.start())
        window = (pre + post + " " + sentence)
        kind = "unit"
        if _FOREIGN.search(window) and not _CNY.search(pre + post):
            kind = "foreign"
        elif _FOREIGN.search(pre + post):
            kind = "foreign"
        elif _QUESTION.search(sentence) or _ECHO.search(sentence) or _NEGATION.search(sentence):
            kind = "echo"
        elif _FEE.search(sentence):
            kind = "fee"
        item = {"token": token, "value": value, "span": span, "kind": kind}
        key = round(float(value), 4)
        if kind == "unit":
            if key in seen:
                continue
            seen.add(key)
            units.append(item)
        elif kind != "echo":
            others.append(item)
    return units, others, bool(ranges)


def parse_quote(raw_text: str, llm_fields: dict | None = None) -> dict[str, Any]:
    """Return parsed fields, confidence, status, and provenance.

    ``status`` is ``accepted`` only for one traceable CNY unit price.
    ``unit_price`` is None otherwise, so a model figure, a foreign
    currency, a range, or a second price cannot reach the solver.
    """
    text = raw_text or ""
    visible = _visible_text(text)
    ignored = _llm_price(llm_fields)
    quoted = InboundIntentClassifier.extract_quoted_price(visible)
    usable = price_the_solver_may_use(None, None, ignored)
    del usable
    units, others, saw_range = _anchored_prices(visible)
    tokens = numeric_tokens(text)
    distinct = _distinct([value for _, value in tokens])
    reason = None
    unit_price = None
    accepted = False
    if len(units) == 1 and not others and not saw_range:
        anchor = units[0]
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
    elif saw_range:
        reason = "price range"
    elif others and not units:
        reason = "not a CNY unit price"
    elif len(units) > 1 or others:
        reason = "more than one price"
    else:
        reason = "no currency-anchored price"
    stage_text = visible if visible.strip() else text
    from src.engine.quote_fields import extract_stage1

    stage1 = extract_stage1(stage_text)
    parsed = {
        "unit_price": unit_price,
        "quoted_amount": None if not units else units[0]["value"],
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
        "candidates": [item["token"] for item in units],
        "review_reason": None if accepted else reason,
    }
    return {
        "parsed": parsed,
        "confidence": "high" if accepted else "low",
        "status": "accepted" if accepted else "review",
        "provenance": provenance,
    }


def price_from_token(raw_text: str, token: str) -> float | None:
    """A human may pick a token only when that exact digit string is a CNY amount in the text."""
    if not token or token not in (raw_text or ""):
        return None
    visible = _visible_text(raw_text)
    if token not in visible:
        return None
    units, others, saw_range = _anchored_prices(visible)
    if saw_range or others:
        return None
    match = next((item for item in units if item["token"] == token), None)
    if match is None:
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
