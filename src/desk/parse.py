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
_DURATION = re.compile(
    r"(?i)\s*(?:[A-Za-z]+\s+){0,3}(?:days?|weeks?|months?)\b|\s*(?:个)?(?:天|日|周|週)"
)
_AMOUNT = re.compile(
    rf"(?i)(?P<pre>cny|rmb|usd|eur|gbp|hkd|jpy|￥|¥|\$|€|£)?\s*"
    rf"(?P<num>{_NUM})\s*"
    rf"(?P<post>cny|rmb|yuan|usd|eur|gbp|hkd|jpy|元|块|塊|美元|美金|欧元|歐元|英镑|英鎊|dollars?|\$|€|£)?"
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
_NAMED_RIVAL = re.compile(
    r"(?i)\b(?:supplier|vendor|competitor|rival)\s+\S+\s+offers?\b"
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
# A question is a clause shape (can you, 吗, is ... ok). A '?' is not one:
# forum text uses runs of '?' where a space was.
_QUESTION = re.compile(
    r"(?i)(?:能否|吗|嗎|\b(?:can|could|would|will)\s+you\b|"
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


def _ascii_letter(char: str) -> bool:
    return len(char) == 1 and ("A" <= char <= "Z" or "a" <= char <= "z")


def _glued_amount(text: str, match: re.Match[str]) -> bool:
    """Digits glued to a letter are a model code or a mask, not a price.

    A currency word that this match already captured ('USD18.40', '18.40USD')
    is the currency, so those letters do not glue the number.
    """
    start = match.start("num")
    end = match.end("num")
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    pre = match.group("pre") or ""
    post = match.group("post") or ""
    if _ascii_letter(before) and not pre:
        return True
    if _ascii_letter(after) and not post:
        return True
    return False


def numeric_tokens(text: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    raw = text or ""
    for match in _TOKEN.finditer(raw):
        before = raw[match.start() - 1] if match.start() else ""
        after = raw[match.end()] if match.end() < len(raw) else ""
        if _ascii_letter(before) or _ascii_letter(after):
            continue
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


def _unwrap_quoted(line: str) -> str:
    text = line.strip()
    while text.startswith(">"):
        text = text[1:].strip()
    return text


def _quoted_noise(line: str) -> bool:
    return bool(_ECHO.search(line) or _OTHER.search(line) or _QUESTION.search(line) or _NEGATION.search(line))


def _supplier_line(line: str) -> bool:
    if _quoted_noise(line):
        return False
    return bool(
        _UNIT_LABEL.search(line) or _QUOTE_LABEL.search(line) or _PER_PIECE.search(line) or _OFFER.search(line)
    )


def _has_currency_amount(text: str) -> bool:
    for match in _AMOUNT.finditer(text):
        if _currency_of(match.group("pre") or "", match.group("post") or ""):
            return True
    return False


def _keep_quoted(lines: list[str]) -> list[str]:
    """Supplier offer lines stay. An echo or a question is not the supplier's price."""
    offers = [line for line in lines if _supplier_line(line)]
    if offers:
        return offers
    quiet = [line for line in lines if line.strip() and not _quoted_noise(line)]
    return quiet or [line for line in lines if line.strip()]


def _soften_marks(text: str) -> str:
    """Forum dumps replace spaces with '?'. The mark is not part of the price."""
    return re.sub(r"[?？]+", " ", text)


def _confirm_span(visible: str, token: str) -> str:
    """The line that contains the amount a person is about to accept."""
    for line in (visible or "").splitlines():
        if token and token in line and line.strip():
            return line.strip()
    if token and token in (visible or ""):
        return token
    return ""


def _visible_text(raw_text: str) -> str:
    """Drop forwarded history and the signature.

    A body that is entirely quoted is the message the supplier sent. A quoted
    block is kept when the unquoted lines do not already name an amount, and
    buyer echoes in that block are left out. Instruction lines stay.
    """
    text = _QUOTE_SPLIT.split(raw_text or "", maxsplit=1)[0]
    plain: list[str] = []
    quoted: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"--", "-- "}:
            break
        if stripped.startswith(">"):
            quoted.append(_unwrap_quoted(stripped))
            continue
        plain.append(line)
    body = "\n".join(plain)
    if quoted and not _has_currency_amount(body):
        kept = "\n".join(_keep_quoted(quoted))
        body = "\n".join(part for part in (body, kept) if part.strip())
    return _soften_marks(body)


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


def _sentence_of(text: str, start: int) -> str:
    """The sentence around ``start``. A comma stays inside it."""
    breaks: list[int] = []
    for index, char in enumerate(text):
        if char in "\n。！？!?":
            breaks.append(index)
            continue
        if char != ".":
            continue
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        if before.isdigit() and after.isdigit():
            continue
        breaks.append(index)
    left = max((index for index in breaks if index < start), default=-1)
    right_candidates = [index for index in breaks if index > start]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right]


def _mentions_other_currency(text: str, deal_currency: str) -> bool:
    from src.config.heuristics import DEAL_CURRENCY

    if not text:
        return False
    usd = bool(_USD.search(text))
    eur = bool(_EUR.search(text))
    yuan = bool(_CNY.search(text))
    other = bool(_FOREIGN.search(text)) and not (usd or eur)
    named: set[str] = set()
    if yuan:
        named.add(str(DEAL_CURRENCY))
    if usd:
        named.add("USD")
    if eur:
        named.add("EUR")
    if other:
        named.add("OTHER")
    return any(code != deal_currency for code in named)


def _bare_blocked_by_other_currency(visible: str, start: int, token: str, deal_currency: str) -> bool:
    line = ""
    for row in (visible or "").splitlines():
        if token and token in row:
            line = row
            break
    sentence = _sentence_of(visible or "", start)
    blocked = _mentions_other_currency(line, deal_currency) or _mentions_other_currency(sentence, deal_currency)
    elsewhere = _other_currency_on_another_line(visible, start, deal_currency)
    return blocked or elsewhere


def _other_currency_on_another_line(visible: str, start: int, deal_currency: str) -> bool:
    """A currency named on a different line applies to every bare number in the message."""
    offset = 0
    for row in (visible or "").splitlines():
        end = offset + len(row)
        on_this_line = offset <= start <= end
        if not on_this_line and _mentions_other_currency(row, deal_currency):
            return True
        offset = end + 1
    return False


def _for_rules(clause: str) -> str:
    return _EXCLUSION.sub(" ", clause)


_USD = re.compile(r"(?i)(?:usd|us\s+dollars?|u\.s\.\s+dollars?|美元|美金|\$|\bdollars?\b)")
_EUR = re.compile(r"(?i)(?:eur|euros?|欧元|歐元|€)")
_CONVERSION_PAREN = re.compile(r"（[^）]{0,80}）|\([^)]{0,96}\)")
_CONVERSION_CUE = re.compile(
    r"(?i)(?:\b(?:about|approx(?:imately)?|around|roughly|circa)\b|大约|大約|约合|約合|折合|约|約|≈)"
)


def _currency_families(text: str) -> set[str]:
    """Currency families named in text. The 元 inside 美元 is not a second currency."""
    raw = text or ""
    found: set[str] = set()
    if _USD.search(raw):
        found.add("USD")
    if _EUR.search(raw):
        found.add("EUR")
    without_named = _EUR.sub(" ", _USD.sub(" ", raw))
    if _CNY.search(without_named):
        found.add("CNY")
    if _FOREIGN.search(_CNY.sub(" ", without_named)):
        found.add("OTHER")
    return found


def _is_fx_conversion(text: str, outside: str) -> bool:
    """A parenthetical amount in another currency. A same-currency rebate is not one."""
    if not _CONVERSION_CUE.search(text) or not _TOKEN.search(text):
        return False
    inner = _currency_families(text)
    outer = _currency_families(outside)
    if not inner or not outer:
        return False
    return bool(inner - outer)


def _same_currency_aside(text: str) -> bool:
    """A parenthetical price in the same currency. The clause is not one clear price."""
    for match in _CONVERSION_PAREN.finditer(text or ""):
        inner = match.group(0)
        outside = (text or "")[: match.start()] + " " + (text or "")[match.end() :]
        if not (_CONVERSION_CUE.search(inner) and _TOKEN.search(inner)):
            continue
        inner_codes = _currency_families(inner)
        outer_codes = _currency_families(outside)
        if inner_codes and outer_codes and not (inner_codes - outer_codes):
            return True
    return False


def _conversion_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _CONVERSION_PAREN.finditer(text or ""):
        outside = (text or "")[: match.start()] + " " + (text or "")[match.end() :]
        if _is_fx_conversion(match.group(0), outside):
            spans.append((match.start(), match.end()))
    return spans


def _clause_without_conversions(clause: str) -> str:
    chars = list(clause)
    for start, end in _conversion_spans(clause):
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)


def _kind_for(pre: str, post: str, clause: str) -> str | None:
    """Currency of this amount. Two currency families on the clause stay unconfirmed.

    A parenthetical conversion ('about USD x' beside a CNY price) is not a
    second family, so the primary amount stays confirmable.
    """
    kind = _currency_of(pre, post)
    counted = _clause_without_conversions(clause)
    families = 0
    if _USD.search(counted):
        families += 1
    if _EUR.search(counted):
        families += 1
    if _CNY.search(counted):
        families += 1
    if families > 1:
        return "foreign"
    if kind is None and _USD.search(counted):
        return "USD"
    return kind


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
    if _OTHER.search(clause) or _ECHO.search(clause) or _NAMED_RIVAL.search(clause):
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


_COMPLIMENTARY = re.compile(
    r"(?i)free\s+samples?(?!\s+fee\b)|complimentary\s+samples?|免费样品|免費樣品|样品免费|樣品免費"
)


def _fee_before(clause: str, amount_at: int) -> bool:
    head = _COMPLIMENTARY.sub(" ", clause[:amount_at])
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
    """A lead time such as 20~30 days is not a price range.

    Currency has to sit on the range itself. A currency that belongs to the
    previous amount, with a digit between it and this range, does not.
    """
    window = text[max(0, match.start() - 12):min(len(text), match.end() + 12)]
    tail = text[match.end():min(len(text), match.end() + len(window))]
    if _DURATION.match(tail):
        return True
    head = text[max(0, match.start() - len(window)):match.start()]
    pieces = re.split(r"\d", head)
    attached = (pieces[-1] if pieces else head) + tail
    if not (_CNY.search(attached) or _FOREIGN.search(attached) or _UNIT_LABEL.search(attached)):
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


def _collect(
    text: str, deal_currency: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Unit candidates, competing money, review tokens, and whether a price range was seen."""
    units: list[dict[str, Any]] = []
    competing: list[dict[str, Any]] = []
    review_tokens: list[dict[str, Any]] = []
    ranges = _range_spans(text)
    conversions = _conversion_spans(text)
    for match in _AMOUNT.finditer(text):
        token = match.group("num")
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        value = _number(token)
        if value is None or token not in text or _glued_amount(text, match):
            continue
        span = (match.start(), match.end())
        if _inside(span, ranges) or _inside(span, conversions):
            continue
        left, _right = _clause_span(text, match.start("num"))
        clause = text[left:_right]
        kind = _kind_for(pre, post, clause)
        if _quantity_amount(text, match.start("num"), token):
            continue
        amount_at = match.start("num") - left
        per_piece = bool(_PER_PIECE.search(clause) or _UNIT_LABEL.search(clause))
        key = round(float(value), 4)
        item = {
            "token": token,
            "value": value,
            "span": span,
            "kind": kind,
            "breakpoint": None,
            "unit": "per_piece" if per_piece else "unknown",
            "identity": (key, str(kind or ""), "per_piece" if per_piece else "unknown"),
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
                if _bare_blocked_by_other_currency(text, match.start("num"), token, deal_currency):
                    continue
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
        units.append(item)
    return (*_dedupe(units, competing), review_tokens, bool(ranges))


def _dedupe(
    units: list[dict[str, Any]], competing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One row per (amount, currency, unit). A repeat is not a second price."""
    kept_units: list[dict[str, Any]] = []
    unit_ids: set[tuple[Any, ...]] = set()
    for item in units:
        identity = item.get("identity")
        if not isinstance(identity, tuple) or identity in unit_ids:
            continue
        unit_ids.add(identity)
        kept_units.append(item)
    kept_competing: list[dict[str, Any]] = []
    rival_ids: set[tuple[Any, ...]] = set()
    for item in competing:
        identity = item.get("identity")
        if not isinstance(identity, tuple) or identity in unit_ids or identity in rival_ids:
            continue
        rival_ids.add(identity)
        kept_competing.append(item)
    return kept_units, kept_competing


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


_HEADER_CCY = re.compile(
    r"(?i)(?:currency|币种|幣種)\s*[:：]?\s*(usd|eur|cny|rmb|美元|欧元|歐元|元|¥|￥|\$|€)"
)


def _named_currency(token: str) -> str | None:
    usd = bool(_USD.search(token))
    eur = bool(_EUR.search(token))
    cny = bool(_CNY.search(token))
    if usd and not (eur or cny):
        return "USD"
    if eur and not (usd or cny):
        return "EUR"
    if cny and not (usd or eur):
        return "CNY"
    return None


def _cells(line: str) -> list[str]:
    if "|" in line:
        return [cell.strip() for cell in line.split("|") if cell.strip()]
    return [cell.strip() for cell in re.split(r"\t+|\s{2,}", line) if cell.strip()]


def _plain_amount(cell: str) -> bool:
    text = cell.strip()
    return _number(text) is not None and bool(re.fullmatch(r"[\d.,]+", text))


def _invoice_annex(text: str) -> str:
    """Rows whose currency is only in the header inherit that currency.

    The header cell is a unit-price label with no amount of its own. A prose
    line that already contains the price is not a column header.
    """
    lines = text.splitlines()
    header_at = None
    column = None
    declared = None
    for index, line in enumerate(lines):
        found = _HEADER_CCY.search(line)
        if found and declared is None:
            declared = _named_currency(found.group(1))
        cells = _cells(line)
        for cell_index, cell in enumerate(cells):
            if _UNIT_LABEL.search(cell) and not _TOKEN.search(cell):
                header_at = index
                column = cell_index
                named = _named_currency(cell)
                if named:
                    declared = named
                break
        if header_at is not None:
            break
    if header_at is None or column is None or not declared:
        return ""
    rows: list[str] = []
    for line in lines[header_at + 1:]:
        if not line.strip():
            break
        cells = _cells(line)
        if column >= len(cells) or not _plain_amount(cells[column]):
            break
        rows.append(f"unit price {cells[column]} {declared}")
    return "\n".join(rows)


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
    annex = _invoice_annex(visible)
    if annex:
        visible = visible + "\n" + annex
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
    goods = [item["token"] for item in units if item.get("token")]
    suggested = None
    if chosen is not None:
        suggested = chosen["token"]
    elif goods:
        suggested = goods[0]
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
        "suggested_token": suggested,
        "confirm_value": (
            None if suggested is None
            else (chosen["value"] if chosen is not None and chosen.get("token") == suggested else _settled(suggested))
        ),
        "confirm_span": None if not suggested else _confirm_span(stage_text, suggested),
        "confirm_spans": {
            token: _confirm_span(stage_text, token) for token in candidate_tokens if token
        },
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
    if _same_currency_aside(visible):
        return None
    conversions = _conversion_spans(visible)
    matched = None
    bare_value = None
    other = False
    for match in _AMOUNT.finditer(visible):
        if match.group("num") != token or _glued_amount(visible, match):
            continue
        if _inside((match.start(), match.end()), conversions):
            continue
        left, right = _clause_span(visible, match.start("num"))
        kind = _kind_for(match.group("pre") or "", match.group("post") or "", visible[left:right])
        value = _settled(token)
        if value is None:
            continue
        if kind == currency:
            matched = value
        elif kind is None:
            if _bare_blocked_by_other_currency(visible, match.start("num"), token, currency):
                continue
            bare_value = value
        else:
            other = True
    if matched is not None:
        return matched
    # An unmarked number is confirmable only for the yuan default.
    if currency == DEAL_CURRENCY and bare_value is not None and not other:
        return bare_value
    return None
