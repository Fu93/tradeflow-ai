"""Quote reading for the desk.

A model JSON is accepted and then discarded. A price is auto-accepted only
when the same clause positively shows all of: the supplier making the offer,
affirmative polarity, a statement rather than a question, a per-unit amount,
and the deal currency. Anything missing stays in review with the candidate
pre-filled. Another currency is never rewritten as the deal currency.
"""
from __future__ import annotations

import re
from typing import Any

from src.engine.bilingual import InboundIntentClassifier
from src.engine.quote_fields import price_the_solver_may_use

_NUM = (
    r"(?<![\d.,])(?:"
    r"\d{1,3}(?:\.\d{3})+,\d+|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d+(?:[.,]\d+)?"
    r")(?![\d.]|,(?=\d))"
)
_TOKEN = re.compile(_NUM)
_PRICE_KEYS = ("price", "unit_price", "mentioned_price", "quoted_amount", "cny_amount")
_QUOTE_SPLIT = re.compile(
    r"(?im)^(?:-----original message-----|-----\s*转发邮件\s*-----|----\s*转发邮件\s*----|"
    r"-{6,}\s*forwarded message\s*-{6,}|原始邮件|发件人\s*[:：]|from\s*:|on .+wrote:\s*)$"
)
_DATE = re.compile(r"(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?")
_EXCLUSION = re.compile(
    r"（[^）]{0,40}不含[^）]{0,40}）|"
    r"\([^)]{0,48}(?:不含|excl\.?|excluding|not including|ex\s+freight|ex\s+shipping)[^)]{0,48}\)",
    re.IGNORECASE,
)
_CLAUSE_MARKS = ("\n", "。", "，", ",", ";", "；", "、")
_FOREIGN = re.compile(r"(?i)(?:usd|eur|gbp|hkd|jpy|aud|cad|美元|欧元|歐元|英镑|英鎊|港币|港幣|日元|\$|€|£)")
_CNY = re.compile(r"(?i)(?:cny|rmb|yuan|元|块|塊|￥|¥)")
_RANGE = re.compile(rf"(?i)({_NUM})\s*(?:-|–|~|到|至|\bto\b)\s*({_NUM})")
_AMOUNT = re.compile(
    rf"(?i)(?P<pre>cny|rmb|usd|eur|gbp|hkd|jpy|￥|¥|\$|€|£)?\s*"
    rf"(?P<num>{_NUM})\s*"
    rf"(?P<post>cny|rmb|yuan|usd|eur|gbp|hkd|jpy|元|块|塊|美元|欧元|歐元|英镑|英鎊|\$|€|£)?"
)
# Positive slots. These name what an offer looks like. They are not a list of
# words that disqualify a sentence.
_UNIT_LABEL = re.compile(r"(?i)(?:unit\s*price|單價|单价|報價單價|报价单价)")
_QUOTE_LABEL = re.compile(r"報價|报价")
_PER_PIECE = re.compile(
    r"(?i)(?:per\s+(?:piece|pc|pcs|unit)|/\s*pcs?\b|/\s*unit\b|\beach\b|\bapiece\b|"
    r"每件|每只|每隻|每个|每個|每隻|元\s*/\s*件|元\s*/\s*个|元\s*/\s*個|元\s*/\s*只|"
    r"一件|一个|一個|一只|一隻|单\s*pcs|單\s*pcs)"
)
_OFFER = re.compile(
    r"(?i)(?:\b(?:we|our|i)\b.{0,32}\b(?:offer|quote|quoted)\b|"
    r"\b(?:our\s+)?factory\b.{0,24}\boffers?\b|"
    r"(?:我们|我們|我方|本厂|本廠|我司).{0,12}(?:报价|報價|报|報))"
)
_OTHER = re.compile(
    r"(?i)(?:quoted\s+us|\bseller\b.{0,48}\bquoted\b|bought\s+from|"
    r"\bcompetitor\b|another\s+factory|other\s+factory|\bvendor\b|"
    r"人家|隔壁|其他厂|其他廠|同行|别家|別家)"
)
_ECHO = re.compile(
    r"(?i)(?:your\s+target|your\s+offer|your\s+price|you\s+offered|we\s+offered|"
    r"您说的|您說的|您报|你报|你方|原价|原價|上次|旧价|舊價|previous\s+price)"
)
_NEGATION = re.compile(
    r"(?i)(?:\bcannot\b|\bcan't\b|\bcan\s+not\b|做不了|不行|不接受|不能接受|"
    r"无法接受|無法接受|已失效|below\s+our\s+cost|too\s+low|not\s+acceptable|"
    r"unable\s+to\s+accept|\breject(?:ed)?\b)"
)
_QUESTION = re.compile(
    r"(?i)(?:\?|？|能否|吗|嗎|\b(?:can|could|would|will)\s+you\b|"
    r"\b(?:can|could)\s+we\b|\bis\b.{0,48}\bok\b)"
)
_DELTA = re.compile(
    r"(?i)(?:\boff\b|\bminus\b|\bdiscount\b|优惠|優惠|让利|讓利|再少|再降|便宜|"
    r"降价|降價|降\s*\d|increase\s+by|reduc(?:e|ed|ing).{0,40}\bby\b|"
    r"\bknock\b.{0,32}\boff\b|\blower(?:ed|ing)?\b.{0,24}\bby\b|"
    r"\bcut\b.{0,24}\bby\b|减(?!税)|減(?!稅))"
)
_ADDON = re.compile(
    r"(?i)(?:\badds?\b|\bmore\b|\bplus\b|\bupgrade\b|另加|另收|附加|加收|\+\s*\d)"
)
_AT_TIER = re.compile(
    r"(?i)\bat\s+\d+(?:\.\d+)?\s*(?:k|pcs|pc|pieces|件|个|個|只)\b"
)
_FEE_HEAD = re.compile(
    r"(?i)(?:freight|shipping|postage|courier|\bsample\b|mould|mold|tooling|"
    r"bank\s+fee|pallet|\bsetup\b|\bdeposit\b|advance\s+payment|\bupfront\b|"
    r"打样|打樣|样品|樣品|模具|开模|開模|运费|運費|快递|快遞|手续费|手續費|"
    r"包装费|包裝費|包装盒|包裝盒|海运|海運|定金|订金|訂金|保证金|保證金|"
    r"预付|預付|首付|总价|總價|合计|合計|总额|總額|\btotal\b|每立方|每箱|每盒|"
    r"每包|每打|\bdozen\b|\bset\s+of\b|\bpack\s+of\b|per\s+(?:carton|box|case)|"
    r"\bcubic\b|[\u4e00-\u9fff]{1,6}[费費])"
)
_INSTRUCTION = re.compile(
    r"(?i)(?:\[\s*system\s*\]|\bsystem\s*:|update\s+unit_price|record\s+price|"
    r"ignore\s+previous|note\s+to\s+the\s+assistant|\bas\s+an\s+ai\b|"
    r"tell\s+your\s+ai|set\s+the\s+price|写成|寫成|记录单价|記錄單價|"
    r"\bbot\b|机器人|機器人|mark\s+this\s+deal|log\s+the\s+(?:final\s+)?price|"
    r"update\s+your\s+records|agreed\s+price\s+is)"
)
_TIER_QTY = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(k|万|萬|pcs|pc|pieces|件|个|個|只)\b"
)
_THOUSAND = 1000
_WAN = 10000
_MIN_TIER_QTY = 10


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
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"\d+,\d+", text):
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
    """Drop quoted history and the signature. Instruction lines stay, so a human can confirm them."""
    text = _QUOTE_SPLIT.split(raw_text or "", maxsplit=1)[0]
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped in {"--", "-- "}:
            break
        kept.append(line)
    return "\n".join(kept)


def _break_positions(text: str) -> list[int]:
    """Clause breaks. A comma or period inside a number stays in the number.

    '?' and '？' are not breaks. The question mark has to remain inside the
    clause, or a question is read as a statement.
    """
    spots: list[int] = []
    for index, char in enumerate(text):
        if char in _CLAUSE_MARKS:
            if char == ",":
                before = text[index - 1] if index else ""
                after = text[index + 1] if index + 1 < len(text) else ""
                if before.isdigit() and after.isdigit():
                    continue
            spots.append(index)
            continue
        if char != ".":
            continue
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        if before.isdigit() and after.isdigit():
            continue
        spots.append(index)
    return spots


def _clause_span(text: str, start: int) -> tuple[int, int]:
    spots = _break_positions(text)
    left = max((index for index in spots if index < start), default=-1)
    right_candidates = [index for index in spots if index > start]
    right = min(right_candidates) if right_candidates else len(text)
    return left + 1, right


def _clause_of(text: str, start: int) -> str:
    left, right = _clause_span(text, start)
    return text[left:right]


def _for_rules(clause: str) -> str:
    return _EXCLUSION.sub(" ", clause)


_USD = re.compile(r"(?i)(?:usd|美元|\$)")
_EUR = re.compile(r"(?i)(?:eur|欧元|歐元|€)")


def _currency_of(pre: str, post: str) -> str | None:
    from src.config.heuristics import DEAL_CURRENCY

    token = f"{pre} {post}"
    yuan = bool(_CNY.search(token))
    usd = bool(_USD.search(token))
    eur = bool(_EUR.search(token))
    other = bool(_FOREIGN.search(token)) and not usd and not eur
    if yuan and not (usd or eur or other):
        return DEAL_CURRENCY
    if usd and not yuan:
        return "USD"
    if eur and not yuan:
        return "EUR"
    if usd or eur or other or yuan:
        return "foreign"
    return None


def _other_speaker(clause: str) -> bool:
    if _OTHER.search(clause) or _ECHO.search(clause):
        return True
    if re.search(r"(?i)\bsays\b", clause) and not re.search(
        r"(?i)\b(?:we|our|i)\b|我们|我們|我方|本厂|本廠", clause
    ):
        return True
    return False


def _tier_breakpoint(clause: str, price: float) -> float | None:
    found: list[float] = []
    for match in _TIER_QTY.finditer(clause):
        raw = _number(match.group(1))
        if raw is None:
            continue
        unit = match.group(2)
        if unit.lower() == "k":
            qty = raw * _THOUSAND
        elif unit in {"万", "萬"}:
            qty = raw * _WAN
        else:
            qty = raw
        if qty < _MIN_TIER_QTY:
            continue
        if abs(qty - float(price)) < 1e-6:
            continue
        found.append(qty)
    if not found:
        return None
    return max(found)


def _fee_before(clause: str, amount_at: int) -> bool:
    head = clause[:amount_at]
    return bool(_FEE_HEAD.search(head))


def _role_ok(clause: str, amount_at: int, *, price: float) -> tuple[bool, float | None]:
    """Per-unit role, and the quantity breakpoint when this clause is a tier row."""
    label = bool(_UNIT_LABEL.search(clause) or _QUOTE_LABEL.search(clause))
    per = bool(_PER_PIECE.search(clause))
    offer = bool(_OFFER.search(clause))
    delta = bool(_DELTA.search(clause) or _ADDON.search(clause))
    if delta:
        return False, _tier_breakpoint(clause, price)
    fee = _fee_before(clause, amount_at)
    if label and not fee:
        return True, _tier_breakpoint(clause, price)
    if label and fee:
        # A unit-price label names the amount. A fee word in front of that label does not.
        return True, _tier_breakpoint(clause, price)
    if fee:
        return False, _tier_breakpoint(clause, price)
    if per or offer:
        return True, _tier_breakpoint(clause, price)
    breakpoint = _tier_breakpoint(clause, price)
    # "33 CNY at 5000 pcs" is a tier row. "Price for 100 pcs is 3200" is a bundle.
    if breakpoint is not None and _AT_TIER.search(clause) and not fee:
        return True, breakpoint
    return False, breakpoint


def _speech_ok(clause: str, *, tier: bool) -> bool:
    if _INSTRUCTION.search(clause) or _other_speaker(clause):
        return False
    if _UNIT_LABEL.search(clause) or _QUOTE_LABEL.search(clause) or _OFFER.search(clause):
        return True
    if _PER_PIECE.search(clause) or tier:
        return True
    return False


def _slots_ok(clause: str, amount_at: int, price: float) -> tuple[bool, float | None]:
    window = _for_rules(clause)
    role, breakpoint = _role_ok(window, amount_at, price=price)
    if _INSTRUCTION.search(window):
        return False, breakpoint
    if not _speech_ok(window, tier=bool(role and breakpoint is not None)):
        return False, breakpoint
    if _NEGATION.search(window):
        return False, breakpoint
    if _QUESTION.search(window):
        return False, breakpoint
    if not role:
        return False, breakpoint
    return True, breakpoint


def _not_a_price_range(text: str, match: re.Match[str]) -> bool:
    window = text[max(0, match.start() - 12):min(len(text), match.end() + 12)]
    if not (_CNY.search(window) or _FOREIGN.search(window) or _UNIT_LABEL.search(window)):
        return True
    if _DATE.search(window):
        return True
    for group in (match.group(1), match.group(2)):
        digits = re.sub(r"\D", "", group)
        if len(digits) >= 7 and "." not in group and "," not in group:
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


def _settled(token: str) -> float | None:
    settled = InboundIntentClassifier.extract_quoted_price("单价 " + token + " 元")
    if settled.cny_amount is None:
        return None
    value = _number(token)
    if value is None:
        return None
    if round(float(settled.cny_amount), 4) != round(float(value), 4):
        return None
    return float(settled.cny_amount)


def _quantity_amount(text: str, start: int, token: str) -> bool:
    """MOQ and quantity units are not unit prices."""
    head = text[max(0, start - 12):start]
    tail = text[start + len(token):start + len(token) + 8]
    if re.search(r"(?i)(?:moq|起订量|起訂量|起订|起訂|最少|数量|數量)\s*$", head):
        return True
    if re.match(r"(?i)\s*(?:个|件|只|pcs|pc|pieces|k)(?:\b|(?=[\u4e00-\u9fff])|$)", tail):
        return True
    return False


def _collect(text: str, deal_currency: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Unit candidates, competing money, review tokens, and whether a price range was seen."""
    units: list[dict[str, Any]] = []
    competing: list[dict[str, Any]] = []
    review_tokens: list[dict[str, Any]] = []
    ranges = _range_spans(text)
    seen: set[float] = set()
    for match in _AMOUNT.finditer(text):
        token = match.group("num")
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        value = _number(token)
        if value is None or token not in text:
            continue
        span = (match.start(), match.end())
        if _inside(span, ranges):
            continue
        kind = _currency_of(pre, post)
        if _quantity_amount(text, match.start("num"), token):
            continue
        left, _right = _clause_span(text, match.start("num"))
        clause = text[left:_right]
        amount_at = match.start("num") - left
        item = {
            "token": token,
            "value": value,
            "span": span,
            "kind": kind,
            "breakpoint": None,
        }
        if kind == "foreign" or (kind is not None and kind != deal_currency):
            competing.append(item)
            continue
        if kind is None:
            # A bare number is a confirmable candidate only when a unit-price
            # label or a per-piece marker sits on that number. A quantity
            # breakpoint (1000个, 5000 pcs) is not the price.
            tail = clause[amount_at:amount_at + len(token) + 6]
            if re.match(rf"(?i){re.escape(token)}\s*(?:个|件|只|pcs|pc|pieces|k)\b", tail):
                continue
            near = clause[max(0, amount_at - 16):amount_at + len(token) + 16]
            if _UNIT_LABEL.search(near) or _QUOTE_LABEL.search(near) or _PER_PIECE.search(near):
                review_tokens.append(item)
            continue
        window = _for_rules(clause)
        if _INSTRUCTION.search(window):
            review_tokens.append(item)
            continue
        ok, breakpoint = _slots_ok(clause, amount_at, value)
        item["breakpoint"] = breakpoint
        review_tokens.append(item)
        if not ok:
            competing.append(item)
            continue
        key = round(float(value), 4)
        if key in seen:
            continue
        seen.add(key)
        units.append(item)
    return units, competing, review_tokens, bool(ranges)


def _pick_tier(units: list[dict[str, Any]], quantity: float | None) -> dict[str, Any] | None:
    if quantity is None:
        return None
    if any(item.get("breakpoint") is None for item in units):
        return None
    qty = float(quantity)
    eligible = [item for item in units if float(item["breakpoint"]) <= qty]
    pool = eligible if eligible else units
    best = max(float(item["breakpoint"]) for item in pool) if eligible else min(
        float(item["breakpoint"]) for item in pool
    )
    chosen = [item for item in pool if float(item["breakpoint"]) == best]
    if len(chosen) != 1:
        return None
    return chosen[0]


def parse_quote(
    raw_text: str,
    llm_fields: dict | None = None,
    quantity: float | None = None,
    deal_currency: str | None = None,
) -> dict[str, Any]:
    """Return parsed fields, confidence, status, and provenance.

    ``status`` is ``accepted`` only when every positive slot holds for one
    per-unit amount in the deal currency. ``unit_price`` is None otherwise.
    """
    text = raw_text or ""
    visible = _visible_text(text)
    ignored = _llm_price(llm_fields)
    quoted = InboundIntentClassifier.extract_quoted_price(visible)
    usable = price_the_solver_may_use(None, None, ignored)
    del usable
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    units, competing, review_tokens, saw_range = _collect(visible, currency)
    tokens = numeric_tokens(text)
    distinct = _distinct([value for _, value in tokens])
    reason = None
    unit_price = None
    accepted = False
    chosen: dict[str, Any] | None = None
    if saw_range:
        reason = "price range"
    elif len(units) == 1 and not competing:
        chosen = units[0]
    elif len(units) > 1 and not competing:
        chosen = _pick_tier(units, quantity)
        if chosen is None:
            reason = "more than one price"
    elif competing and not units:
        reason = "not the deal currency"
    elif competing:
        reason = "more than one price"
    else:
        reason = "no currency-anchored price"
    if chosen is not None:
        settled = _settled(chosen["token"])
        if settled is not None and chosen["token"] in text:
            accepted = True
            unit_price = settled
            reason = None
        else:
            chosen = None
            reason = reason or "no usable amount in the deal currency"
    suggested = None
    if chosen is not None:
        suggested = chosen["token"]
    elif review_tokens:
        suggested = review_tokens[0]["token"]
    candidate_tokens: list[str] = []
    for item in units + review_tokens:
        if item["token"] not in candidate_tokens:
            candidate_tokens.append(item["token"])
    if suggested and suggested not in candidate_tokens:
        candidate_tokens.insert(0, suggested)
    quoted_amount = None
    if accepted and chosen is not None:
        quoted_amount = float(chosen["value"])
    elif review_tokens:
        quoted_amount = float(review_tokens[0]["value"])
    elif units:
        quoted_amount = float(units[0]["value"])
    stage_text = visible if visible.strip() else text
    from src.engine.quote_fields import extract_stage1

    stage1 = extract_stage1(stage_text)
    parsed = {
        "unit_price": unit_price,
        "quoted_amount": quoted_amount,
        "currency": currency if accepted else quoted.currency,
        "moq": stage1.get("moq"),
        "lead_time_days": stage1.get("lead_time_days"),
        "deposit_pct": stage1.get("deposit_pct"),
        "freight": None,
        "duty_rate": None,
        "fees": None,
    }
    provenance = {
        "reader": "positive_evidence",
        "llm_price_ignored": ignored is not None or isinstance(llm_fields, dict),
        "quoted_amount": parsed["quoted_amount"],
        "currency": parsed["currency"],
        "cny_amount": unit_price,
        "currency_note": quoted.note,
        "distinct_amounts": distinct,
        "tokens": [token for token, _value in tokens],
        "candidates": candidate_tokens,
        "suggested_token": None if accepted else suggested,
        "review_reason": None if accepted else reason,
    }
    return {
        "parsed": parsed,
        "confidence": "high" if accepted else "low",
        "status": "accepted" if accepted else "review",
        "provenance": provenance,
    }


def price_from_token(raw_text: str, token: str, deal_currency: str | None = None) -> float | None:
    """A human may confirm a token when that digit string is in the text and is not a foreign amount.

    The grammar may have left the message in review. The confirmation is the
    missing evidence. A token anchored to another currency stays None.
    """
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    if not token or token not in (raw_text or ""):
        return None
    visible = _visible_text(raw_text)
    if token not in visible:
        return None
    cny_value = None
    bare_value = None
    foreign = False
    for match in _AMOUNT.finditer(visible):
        if match.group("num") != token:
            continue
        kind = _currency_of(match.group("pre") or "", match.group("post") or "")
        value = _settled(token)
        if value is None:
            continue
        if kind == "foreign":
            foreign = True
        elif kind == "CNY":
            cny_value = value
        elif kind is None:
            bare_value = value
    if currency == DEAL_CURRENCY:
        if cny_value is not None:
            return cny_value
        if foreign:
            return None
        return bare_value
    matched = None
    for match in _AMOUNT.finditer(visible):
        if match.group("num") != token:
            continue
        kind = _currency_of(match.group("pre") or "", match.group("post") or "")
        if kind == currency:
            matched = _settled(token)
    return matched
