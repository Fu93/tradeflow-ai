"""Quote reading for the desk.

A model JSON is accepted and then discarded. A price is auto-accepted only
when the same clause positively shows all of: the supplier making the offer,
affirmative polarity, a statement rather than a question, a per-unit amount,
and the deal currency. Anything missing stays in review with the candidate
pre-filled. Another currency is never rewritten as the deal currency.
"""
from __future__ import annotations

import re
import unicodedata
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
# 元 and 块 are CNY only as their own token. 港元, 美元, and 欧元 are other names.
_CNY = re.compile(
    r"(?i)(?:人民币|人民幣|(?<![A-Za-z])(?:cny|rmb|yuan)(?![A-Za-z])|"
    r"(?<![\u4e00-\u9fff])[元块塊](?![\u4e00-\u9fff])|¥)"
)
_USD = re.compile(
    r"(?i)(?:us\s*\$|u\.s\.\s+dollars?|us\s+dollars?|(?<![A-Za-z])usd(?![A-Za-z])|"
    r"(?<![A-Za-z])dollars?(?![A-Za-z])|美元|美金|美刀|(?<![\u4e00-\u9fff])刀(?![\u4e00-\u9fff])|\$)"
)
_EUR = re.compile(
    r"(?i)(?:(?<![A-Za-z])eur(?![A-Za-z])|(?<![A-Za-z])euros?(?![A-Za-z])|"
    r"欧元|歐元|(?<![\u4e00-\u9fff])[欧歐](?![\u4e00-\u9fff])|€)"
)
_TWD = re.compile(
    r"(?i)NT\s*\$|新台币|新台幣|新臺幣|台币|臺幣|台幣|(?<![A-Za-z])TWD(?![A-Za-z])"
)
_CJK_CURRENCY = re.compile(
    r"(?<![\u4e00-\u9fff])(?P<name>[\u4e00-\u9fff]{1,8}(?:元|币|幣|圆|圓|铢|銖|镑|鎊|円)|卢布|盧布)"
    r"(?![\u4e00-\u9fff])"
)
_DEAL_CURRENCY_WORDS = frozenset({"人民币", "人民幣", "美元", "美金", "美刀", "欧元", "歐元"})
_ISO_OTHER = re.compile(
    r"(?i)(?<![A-Za-z])(?:AED|AFN|AUD|BRL|CAD|CHF|CZK|DKK|EGP|GBP|HKD|HUF|IDR|ILS|INR|"
    r"JPY|KRW|KWD|MXN|MYR|NOK|NZD|PHP|PLN|QAR|RON|RUB|SAR|SEK|SGD|THB|TWD|UAH|VND|ZAR)"
    r"(?![A-Za-z])"
)
_OTHER_SYMBOL = re.compile(r"[£₩₽₹₺₴₪฿₫₭₦₱₲₡₵₸₼]")
_RANGE = re.compile(rf"(?i)({_NUM})\s*(?:-|–|~|到|至|\bto\b)\s*({_NUM})")
_DURATION = re.compile(
    r"(?i)\s*(?:[A-Za-z]+\s+){0,3}(?:days?|weeks?|months?)\b|\s*(?:个)?(?:天|日(?!元)|周|週)"
)
_AMOUNT = re.compile(
    rf"(?i)(?P<pre>人民币|人民幣|美元|美金|美刀|欧元|歐元|cny|rmb|usd|eur|gbp|hkd|jpy|twd|"
    rf"￥|¥|\$|€|£|₩|(?<![\u4e00-\u9fff])[刀欧歐])?\s*"
    rf"(?P<num>{_NUM})\s*"
    rf"(?P<post>人民币|人民幣|美元|美金|美刀|欧元|歐元|cny|rmb|yuan|usd|eur|gbp|hkd|jpy|twd|"
    rf"元|块|塊|英镑|英鎊|dollars?|\$|€|£|₩|(?<![\u4e00-\u9fff])[刀欧歐](?![\u4e00-\u9fff]))?"
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
    r"(?i)(?:\badds?\b|\bmore\b|\bplus\b|\bupgrade\b|另加|另收|附加|加收|加价|加價|差价|差價|\+\s*\d)"
)
_AT_TIER = re.compile(
    r"(?i)\bat\s+\d+(?:\.\d+)?\s*(?:k|pcs|pc|pieces|件|个|個|只)\b"
)
_FEE_HEAD = re.compile(
    r"(?i)(?:freight|shipping|postage|courier|\bsample\b|mould|mold|tooling|"
    r"bank\s+fee|pallet|\bsetup\b|\bdeposit\b|advance\s+payment|\bupfront\b|"
    r"打样|打樣|样品|樣品|模具|开模|開模|运费|運費|快递|快遞|手续费|手續費|"
    r"包装费|包裝費|包装盒|包裝盒|海运|海運|定金|订金|訂金|保证金|保證金|"
    r"预付|預付|首付|总价|總價|合计|合計|总额|總額|\btotal\b|"
    r"[\u4e00-\u9fff]{1,6}[费費])"
)
_INSTRUCTION = re.compile(
    r"(?i)(?:\[\s*system\s*\]|\bsystem\s*:|update\s+unit_price|record\s+price|"
    r"ignore\s+previous|note\s+to\s+the\s+assistant|\bas\s+an\s+ai\b|"
    r"tell\s+your\s+ai|set\s+the\s+price|写成|寫成|记录单价|記錄單價|"
    r"\bbot\b|机器人|機器人|mark\s+this\s+deal|log\s+the\s+(?:final\s+)?price|"
    r"update\s+your\s+records|agreed\s+price\s+is)"
)
_TIER_QTY = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(k|万|萬|pcs|pc|pieces|件|个|個|只)(?:\b|(?=[\u4e00-\u9fff])|$)"
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
    families = _currency_families(text)
    if "OTHER" in families:
        return True
    return any(code != deal_currency for code in families)


def _bare_blocked_by_other_currency(visible: str, start: int, token: str, deal_currency: str) -> bool:
    visible = _clause_without_conversions(visible or "")
    line = ""
    for row in visible.splitlines():
        if token and token in row:
            line = row
            break
    sentence = _sentence_of(visible, start)
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


_CONVERSION_PAREN = re.compile(r"（[^）]{0,80}）|\([^)]{0,96}\)")
_CONVERSION_CUE = re.compile(
    r"(?i)(?:\b(?:about|approx(?:imately)?|around|roughly|circa)\b|大约|大約|约合|約合|折合|约|約|≈)"
)


def _fold(text: str) -> str:
    """NFKC, then drop format characters such as zero-width and word joiners."""
    folded = unicodedata.normalize("NFKC", text or "")
    return "".join(char for char in folded if unicodedata.category(char) != "Cf")


def _blank_match(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda match: " " * len(match.group(0)), text)


def _has_other_cjk_currency(text: str) -> bool:
    for match in _CJK_CURRENCY.finditer(text):
        if match.group("name") not in _DEAL_CURRENCY_WORDS:
            return True
    return False


def _currency_families(text: str) -> set[str]:
    """Currency families named in text. 元 inside 美元 or 港元 is not a second CNY."""
    raw = _fold(text or "")
    found: set[str] = set()
    if _TWD.search(raw) or _ISO_OTHER.search(raw) or _OTHER_SYMBOL.search(raw):
        found.add("OTHER")
    body = _blank_match(_OTHER_SYMBOL, _blank_match(_ISO_OTHER, _blank_match(_TWD, raw)))
    if _USD.search(body):
        found.add("USD")
    if _EUR.search(body):
        found.add("EUR")
    body = _blank_match(_EUR, _blank_match(_USD, body))
    if _has_other_cjk_currency(body):
        found.add("OTHER")
    if _CNY.search(body):
        found.add("CNY")
    return found


def _is_fx_conversion(text: str, outside: str) -> bool:
    """A parenthetical amount in another currency. A same-currency rebate is not one.

    An unlabelled primary has no currency family. The cued parenthetical is
    still not the unit price.
    """
    if not _CONVERSION_CUE.search(text) or not _TOKEN.search(text):
        return False
    inner = _currency_families(text)
    if not inner:
        return False
    outer = _currency_families(outside)
    if not outer:
        return True
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


def _kind_code(families: set[str]) -> str | None:
    from src.config.heuristics import DEAL_CURRENCY

    if not families:
        return None
    if "OTHER" in families or len(families) > 1:
        return "foreign"
    only = next(iter(families))
    if only == "CNY":
        return str(DEAL_CURRENCY)
    return only


def _kind_for(pre: str, post: str, clause: str) -> str | None:
    """Currency of this amount. Two currency families on the clause stay unconfirmed.

    A parenthetical conversion ('about USD x' beside a CNY price) is not a
    second family, so the primary amount stays confirmable.
    """
    counted = _clause_without_conversions(clause)
    families = _currency_families(counted)
    if families:
        return _kind_code(families)
    return _currency_of(pre, post)


def _currency_of(pre: str, post: str) -> str | None:
    return _kind_code(_currency_families(f"{pre} {post}"))


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
    if not (_currency_families(attached) or _UNIT_LABEL.search(attached)):
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


def _duration_amount(text: str, start: int, token: str) -> bool:
    """A lead time is not a unit price and not a unit-basis candidate."""
    return bool(_DURATION.match(text[start + len(token):]))


def _quantity_amount(text: str, start: int, token: str) -> bool:
    """MOQ and quantity units are not unit prices."""
    head = text[max(0, start - 12):start]
    tail = text[start + len(token):start + len(token) + 8]
    if re.search(r"(?i)(?:moq|起订量|起訂量|起订|起訂|最少|数量|數量)\s*$", head):
        return True
    if re.match(
        r"(?i)\s*(?:个|個|件|只|隻|条|條|双|雙|套|台|臺|箱|斤|公斤|千克|米|包|盒|打|立方|pcs|pc|pieces|k)"
        r"(?:\b|(?=[\u4e00-\u9fff])|$)",
        tail,
    ):
        return True
    return False


_IMPLICIT_EVIDENCE = "implicit currency from deal + language"
_CJK = re.compile(r"[\u4e00-\u9fff]")
_IMPLICIT_CUE = re.compile(
    r"(?:报价|報價|单价|單價|价格|價格|多少钱|多少錢|含税|含稅|不含税|不含稅|起订|起訂|"
    r"每\s*(?:个|個|件|只|隻|条|條|双|雙|套|台|臺|箱|斤|公斤|千克|米|包|盒)|"
    r"一\s*(?:个|個|件|只|隻|条|條|双|雙|套|台|臺|箱|斤|公斤|千克|米|包|盒))"
)
_HEDGE = re.compile(
    r"(?:不到|低于|低於|高于|高於|大概|左右|最多|至少|比.{0,16}便宜|市场价|市場價|行情)"
)
_BUYER = re.compile(r"目标价|目標價|采购价|採購價|我方预算|我方預算|买家|買家")
_PAST = re.compile(r"之前|以前|上次|买过|買過|当时|當時")
_HEARSAY = re.compile(r"听说|聽說|据说|據說|网上|網上")
_HYPOTHETICAL = re.compile(r"如果|假如|估计|估計|预计|預計|应该|應該")
_BUYER_ASK = re.compile(r"能不能|可不可以|可以吗|可以嗎|希望|给个|給個|最低多少")
# Characters that simplified supplier text does not use. 元 beside these is not CNY.
_TRADITIONAL_MARK = re.compile("[報價稅隻條雙點張碼噸錢樣運務買過應說聽據網與對經為臺幣]")
_ATTACH_HEAD = re.compile(
    r"(?i)(?:单价|單價|报价|報價|价格|價格|多少钱|多少錢|不含税|不含稅|含税|含稅|unit\s*price)"
    r"\s*[:：]?\s*[?？]?\s*$"
)
_PERCENT_TAIL = re.compile(r"^\s*(?:%|％|个百分点|个点|百分点)")
_COUNT_TAIL = re.compile(r"^\s*(?:万|萬|千)")
_DATE_TAIL = re.compile(r"^\s*(?:年|月|日(?!元)|号|號)")
_THRESHOLD_HEAD = re.compile(r"(?:超过|超過|不低于|不低於)\s*$")
_THRESHOLD_TAIL = re.compile(r"^\s*(?:以上|以下)")
_MODEL_HEAD = re.compile(
    r"(?i)(?:型号|型號|货号|貨號|款号|款號|料号|料號|平台|sku|1688)\s*[:：]?\s*$"
)
_PHONE = re.compile(r"\d{8,}")
_SUBUNIT = re.compile(r"^\s*(?P<u>分|毛|角|厘|釐)")
_SURCHARGE_HEAD = re.compile(r"加价|加價|加收|另加|另收|差价|差價|\+\s*$")
_LOT = re.compile(
    r"(?i)(?:\bfor\s+\d+\s*(?:pcs|pc|pieces|件|个|個)\b|一共|总计|總計|\blot\s+of\b)"
)
# One lexicon. The deal unit is whitelisted as per_piece. Every other surface is a
# stated non-piece basis. Longer surfaces are compiled first so kg beats g and
# m2 beats m. Latin surfaces keep a letter boundary so a letter inside a word
# is not a unit.
UNIT_LEXICON: tuple[tuple[str, str], ...] = (
    ("个", "per_piece"), ("個", "per_piece"), ("件", "per_piece"),
    ("只", "per_piece"), ("隻", "per_piece"),
    ("piece", "per_piece"), ("pcs", "per_piece"), ("pc", "per_piece"),
    ("unit", "per_piece"), ("each", "per_piece"), ("apiece", "per_piece"),
    ("公斤", "公斤"), ("千克", "公斤"), ("kg", "公斤"), ("kilo", "公斤"), ("kilogram", "公斤"),
    ("平方", "平方"), ("㎡", "平方"), ("m2", "平方"), ("m²", "平方"),
    ("立方", "立方"), ("m3", "立方"), ("m³", "立方"),
    ("吨", "吨"), ("噸", "吨"), ("tonne", "吨"), ("ton", "吨"), ("t", "吨"),
    ("克", "克"), ("gram", "克"), ("g", "克"),
    ("厘米", "厘米"), ("cm", "厘米"), ("毫米", "毫米"), ("mm", "毫米"),
    ("米", "米"), ("meter", "米"), ("metre", "米"), ("m", "米"),
    ("条", "条"), ("條", "条"), ("双", "双"), ("雙", "双"),
    ("对", "对"), ("對", "对"), ("pair", "对"),
    ("套", "套"), ("set", "套"), ("bundle", "套"),
    ("台", "台"), ("臺", "台"),
    ("箱", "箱"), ("carton", "箱"), ("ctn", "箱"), ("box", "箱"), ("case", "箱"),
    ("卷", "卷"), ("roll", "卷"),
    ("斤", "斤"), ("包", "包"), ("pack", "包"), ("盒", "盒"),
    ("打", "打"), ("dozen", "打"),
    ("码", "码"), ("碼", "码"), ("yard", "码"),
    ("张", "张"), ("張", "张"), ("sheet", "张"),
    ("片", "片"), ("点", "点"), ("點", "点"),
)


def _bounded_surface(surface: str) -> str:
    body = re.escape(surface)
    if surface[:1].isascii() and surface[:1].isalpha():
        return rf"(?<![A-Za-z]){body}(?![A-Za-z])"
    return body


def _unit_alternation(rows: tuple[tuple[str, str], ...]) -> str:
    ordered = sorted(rows, key=lambda row: len(row[0]), reverse=True)
    return "|".join(_bounded_surface(surface) for surface, _canon in ordered)


_FIRM_LEXICON = tuple(row for row in UNIT_LEXICON if row[0] not in {"点", "點"})
_UNIT_ALT = _unit_alternation(UNIT_LEXICON)
_FIRM_ALT = _unit_alternation(_FIRM_LEXICON)
_BASIS_UNIT = _UNIT_ALT
_BASIS_CUE = re.compile(
    rf"(?i)(?:每\s*(?P<u1>{_UNIT_ALT})|"
    rf"按\s*(?P<u2>{_UNIT_ALT})|"
    rf"整\s*(?P<u3>{_UNIT_ALT})(?=\s*(?:价|價|\d|元|块|塊|￥|¥))|"
    rf"(?<![这那哪某])(?P<u4>{_UNIT_ALT})\s*[价價]|"
    rf"(?:元|块|塊|￥|¥)\s*/\s*(?P<u5>{_UNIT_ALT})|"
    rf"(?:元|块|塊|￥|¥)\s*一\s*(?P<u6>{_UNIT_ALT})|"
    rf"一\s*(?P<u7>{_UNIT_ALT})(?=\s*(?:\d|元|块|塊|￥|¥))|"
    rf"一\s*(?P<u8>{_FIRM_ALT})(?=\s*(?:[，,。；;]|$))|"
    rf"/\s*(?P<u9>{_UNIT_ALT})|"
    rf"per\s+(?P<u10>{_UNIT_ALT})|"
    rf"\b(?P<u11>each|apiece)\b)"
)
_BASIS_NAME = {surface.lower(): canon for surface, canon in UNIT_LEXICON}
_BASIS_NAME.update({surface: canon for surface, canon in UNIT_LEXICON})
# A character after the price that is grammar, not a measure word.
_CLASSIFIER_STOP = frozenset(
    "含不以上下的和或另元块塊毛角分厘釐整左右约約大概可是为為了呢啊吧请請"
    "税稅起到给給送卖賣买買出售报价價钱錢吗嗎哦喔这這那就还還也都很太"
    "较較比更再已经經過过在从從把被让讓每按及与與一"
)
_UNKNOWN_SLASHED = re.compile(
    r"^\s*(?:元|块|塊|￥|¥)?\s*/\s*(?P<u>[\u4e00-\u9fff]{1,2})(?![\u4e00-\u9fff])"
)
_UNKNOWN_BARE = re.compile(
    r"^\s*(?:元|块|塊|￥|¥)\s*(?P<u>[\u4e00-\u9fff])(?![\u4e00-\u9fff/])"
)
_PACK_COUNT = re.compile(
    r"(?:一\s*(?P<pack>箱|包|套|盒|打)\s*(?P<n>\d+(?:\.\d+)?)\s*(?:个|個|件|只|隻)|"
    r"(?P<n2>\d+(?:\.\d+)?)\s*(?:个|個|件|只|隻)\s*/\s*(?P<pack2>箱|包|套|盒|打))"
)
# Yuan, then jiao, then fen. A bare digit is jiao. A leading-zero pair is jiao then fen.
# A digit after 毛 or 角 is fen. Two digits without that zero, such as 1块20, stay a
# separate number.
_COMPOUND = re.compile(
    r"^\s*(?:元|块|塊)\s*(?:"
    r"(?P<jiao>\d+)\s*(?:毛|角)\s*(?:(?P<fen>\d+)\s*分|(?P<fenbare>\d)(?!\d))?"
    r"|(?P<fenword>\d+)\s*分"
    r"|(?P<pair>0\d)(?!\d)"
    r"|(?P<bare>\d)(?!\d)"
    r")"
)
_MOQ_PIECE = re.compile(
    r"(?i)(?:moq|起订量|起訂量|起订|起訂)\s*[:：]?\s*\d+(?:[.,]\d+)?\s*(?:件|个|個|只|隻|pcs|pc|pieces)\b"
)
_TAX_INCLUDED = re.compile(r"(?<!不)(?:含税|含稅)")
_TAX_EXCLUDED = re.compile(r"不含税|不含稅")


def _canon_of(surface: str) -> str:
    token = (surface or "").strip()
    if not token:
        return ""
    return _BASIS_NAME.get(token) or _BASIS_NAME.get(token.lower(), "")


def _basis_word(match: re.Match[str]) -> str:
    for group in match.groups():
        if group:
            return _canon_of(group)
    return ""


def _unknown_classifier(tail: str) -> str | None:
    """A measure word written right after the price, and missing from the lexicon.

    A slash may carry one or two characters. Without a slash only a single
    character counts, so a following word such as 出售 is not a unit. Grammar
    after the price (含, 整, 以) is not a unit either. Zero-width characters
    are removed first so they cannot split one word into a false unit.
    """
    found = _UNKNOWN_SLASHED.match(_fold(tail or "")) or _UNKNOWN_BARE.match(_fold(tail or ""))
    if found is None:
        return None
    surface = found.group("u")
    if any(char in _CLASSIFIER_STOP for char in surface):
        return None
    known = _canon_of(surface)
    if known:
        return known
    return surface


def _basis_names(text: str) -> list[str]:
    """Basis cues in ``text``. A non-piece cue is not removed by a piece cue."""
    found: list[str] = []
    for match in _BASIS_CUE.finditer(text or ""):
        name = _basis_word(match)
        if name and name not in found:
            found.append(name)
    return found


def non_piece_basis(text: str) -> bool:
    """True when ``text`` names a measure that is not the deal unit."""
    return any(name != "per_piece" for name in _basis_names(text))


def _basis_in(text: str) -> str | None:
    """The deal unit, a non-piece basis, or None when the text states no basis."""
    found = _basis_names(text)
    others = [name for name in found if name != "per_piece"]
    if others:
        return others[0]
    if found:
        return "per_piece"
    return None


def _attach_head(clause: str, amount_at: int) -> str:
    return clause[:amount_at]


def _price_attached(clause: str, amount_at: int, token: str, pre: str, post: str) -> bool:
    """The number itself carries a price cue. A cue elsewhere in the sentence does not."""
    if (pre or "").strip() or (post or "").strip():
        return True
    head = _attach_head(clause, amount_at)
    tail = clause[amount_at + len(token):]
    if _ATTACH_HEAD.search(head):
        return True
    if re.search(rf"每\s*(?:{_BASIS_UNIT})\s*$", head):
        return True
    if re.search(r"(?i)per\s+(?:piece|pc|pcs|unit)\s*$", head):
        return True
    if re.match(rf"^\s*一\s*(?:{_BASIS_UNIT})", tail):
        return True
    if re.match(r"(?i)^\s*per\s+(?:piece|pc|pcs|unit)\b", tail):
        return True
    if re.match(r"(?i)^\s*(?:each|apiece)\b", tail):
        return True
    if _SUBUNIT.match(tail):
        return True
    return False


def _excluded_number(text: str, start: int, token: str, clause: str, amount_at: int) -> bool:
    """Counts, quantities, models, thresholds, percents, dates, and phone numbers."""
    tail = text[start + len(token):]
    head = clause[:amount_at]
    if (
        _PERCENT_TAIL.match(tail)
        or _COUNT_TAIL.match(tail)
        or _DATE_TAIL.match(tail)
        or _THRESHOLD_TAIL.match(tail)
    ):
        return True
    if _THRESHOLD_HEAD.search(head) or _MODEL_HEAD.search(head) or _SURCHARGE_HEAD.search(head):
        return True
    if _PHONE.fullmatch(token):
        return True
    end = start + len(token)
    for match in _DATE.finditer(text):
        if not (end <= match.start() or start >= match.end()):
            return True
    return False


def _subunit_factor(tail: str) -> float | None:
    """分, 毛/角, and 厘 as yuan. The formulas are the registered subunit factors."""
    from src.config.heuristics import FEN_IN_YUAN, JIAO_IN_YUAN, LI_IN_YUAN

    found = _SUBUNIT.match(tail or "")
    if found is None:
        return None
    unit = found.group("u")
    if unit == "分":
        return float(FEN_IN_YUAN)
    if unit in {"毛", "角"}:
        return float(JIAO_IN_YUAN)
    return float(LI_IN_YUAN)


def _taiwan_unknown(text: str, pre: str, post: str) -> bool:
    """Traditional script beside a bare 元 is not CNY. A named currency is handled apart."""
    marker = f"{pre} {post}"
    if not re.search(r"元|块|塊", marker):
        return False
    if re.search(r"(?i)cny|rmb|yuan|￥|¥|人民币|人民幣", text or ""):
        return False
    if _TWD.search(_fold(text or "")):
        return False
    return _TRADITIONAL_MARK.search(text or "") is not None


def _offer_speech(clause: str) -> bool:
    """A question, a refusal, a delta, or an instruction is not a goods candidate."""
    window = _for_rules(clause)
    if _QUESTION.search(window) or _NEGATION.search(window) or _INSTRUCTION.search(window):
        return False
    if _DELTA.search(window) or _ADDON.search(window):
        return False
    return True


def _amount_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    body = text or ""
    for match in _AMOUNT.finditer(body):
        # A digit glued to a letter is part of a unit or a model, not a neighbour price.
        if _glued_amount(body, match):
            continue
        spans.append((match.start("num"), match.end("num")))
    return spans


def _compound_minor(tail: str) -> tuple[float, list[tuple[int, int]]] | None:
    """Minor yuan after 元 or 块, and the digit spans that are not their own prices."""
    from src.config.heuristics import FEN_IN_YUAN, JIAO_IN_YUAN

    match = _COMPOUND.match(tail or "")
    if match is None:
        return None
    spans: list[tuple[int, int]] = []
    jiao: float | None = None
    fen: float | None = None
    if match.group("jiao") is not None:
        parsed = _number(match.group("jiao") or "")
        if parsed is None:
            return None
        jiao = float(parsed)
        spans.append((match.start("jiao"), match.end("jiao")))
        fen_text = match.group("fen") if match.group("fen") is not None else match.group("fenbare")
        fen_name = "fen" if match.group("fen") is not None else "fenbare"
        if fen_text is not None:
            parsed_fen = _number(fen_text)
            if parsed_fen is None:
                return None
            fen = float(parsed_fen)
            spans.append((match.start(fen_name), match.end(fen_name)))
    elif match.group("fenword") is not None:
        parsed_fen = _number(match.group("fenword") or "")
        if parsed_fen is None:
            return None
        fen = float(parsed_fen)
        spans.append((match.start("fenword"), match.end("fenword")))
    elif match.group("pair") is not None:
        pair = match.group("pair") or ""
        jiao = float(_number(pair[:1]) or 0)
        fen = float(_number(pair[1:]) or 0)
        spans.append((match.start("pair"), match.end("pair")))
    else:
        parsed = _number(match.group("bare") or "")
        if parsed is None:
            return None
        jiao = float(parsed)
        spans.append((match.start("bare"), match.end("bare")))
    minor = 0.0
    if jiao is not None:
        minor += jiao * float(JIAO_IN_YUAN)
    if fen is not None:
        minor += fen * float(FEN_IN_YUAN)
    return minor, spans


def _compound_minor_spans(text: str) -> list[tuple[int, int]]:
    """Jiao and fen digits of a compound amount. They are not a second price."""
    spans: list[tuple[int, int]] = []
    body = text or ""
    for match in _AMOUNT.finditer(body):
        found = _compound_minor(body[match.end("num"):])
        if found is None:
            continue
        _minor, parts = found
        base = match.end("num")
        for start, end in parts:
            spans.append((base + start, base + end))
    return spans


def _sentence_span(text: str, start: int) -> tuple[int, int]:
    """Sentence bounds. A comma stays inside. A dot inside a number does too."""
    left = 0
    right = len(text)
    for index, char in enumerate(text):
        if char in "\n。！？!?":
            boundary = True
        elif char == ".":
            before = text[index - 1] if index else ""
            after = text[index + 1] if index + 1 < len(text) else ""
            boundary = not (before.isdigit() and after.isdigit())
        else:
            boundary = False
        if not boundary:
            continue
        if index < start:
            left = index + 1
        elif index > start and index < right:
            right = index
    return left, right


def _cue_gap(cue_at: int, cue_end: int, amount_start: int, amount_end: int) -> int:
    """Characters between a basis cue and an amount. Overlap is zero."""
    if cue_end <= amount_start:
        return amount_start - cue_end
    if cue_at >= amount_end:
        return cue_at - amount_end
    return 0


def _cue_belongs(
    cue_at: int,
    cue_end: int,
    start: int,
    end: int,
    amounts: list[tuple[int, int]],
) -> bool:
    """A cue governs the nearest amount. A lead time does not inherit the price's unit."""
    own = _cue_gap(cue_at, cue_end, start, end)
    for other_start, other_end in amounts:
        if other_start == start and other_end == end:
            continue
        if _cue_gap(cue_at, cue_end, other_start, other_end) < own:
            return False
    return True


def _basis_for_number(text: str, start: int, end: int) -> str | None:
    """Basis cues that belong to this number, not to a neighbouring quantity.

    The window runs from the previous amount to the next one. A cue on the
    other side of another amount is that other amount's basis.
    """
    sentence_left, sentence_right = _sentence_span(text, start)
    minors = _compound_minor_spans(text)
    amounts = [
        span for span in _amount_spans(text)
        if sentence_left <= span[0] < sentence_right and not _inside(span, minors)
    ]
    window_right = sentence_right
    for amount_start, _amount_end in amounts:
        if amount_start >= end:
            window_right = min(window_right, amount_start)
    names: list[str] = []
    sentence = text[sentence_left:sentence_right]
    for match in _BASIS_CUE.finditer(sentence):
        cue_at = sentence_left + match.start()
        cue_end = sentence_left + match.end()
        if not _cue_belongs(cue_at, cue_end, start, end, amounts):
            continue
        name = _basis_word(match)
        if name and name not in names:
            names.append(name)
    others = [name for name in names if name != "per_piece"]
    if others:
        return others[0]
    if names:
        return "per_piece"
    unknown = _unknown_classifier(text[end:window_right])
    if unknown:
        return unknown
    return None


def _fee_of(clause: str, amount_at: int, token: str) -> str | None:
    """A time or service rate, or a named fee. A measure word is not a fee."""
    tail = clause[amount_at + len(token):]
    if re.match(r"(?i)\s*(?:元|块|塊|￥|¥)?\s*/\s*(?:小时|小時)", tail):
        return "元/小时"
    if re.match(r"(?i)\s*(?:元|块|塊|￥|¥)?\s*/\s*天", tail):
        return "元/天"
    if re.match(r"(?i)\s*(?:元|块|塊|￥|¥)?\s*/\s*次", tail):
        return "元/次"
    if not _fee_before(clause, amount_at):
        return None
    pairs = (
        ("运费", r"运费|運費|freight|shipping|postage|courier"),
        ("打样费", r"打样|打樣|sample"),
        ("开模费", r"开模|開模|模具|mould|mold|tooling"),
        ("服务费", r"服务费|服務費"),
        ("手续费", r"手续费|手續費|bank\s+fee"),
    )
    for label, pattern in pairs:
        if re.search(pattern, clause, re.IGNORECASE):
            return label
    return "fee"


def _reference_clause(clause: str) -> bool:
    """Buyer text, a rival, or a comparison. A conversion paren is not one."""
    chars = list(_clause_without_conversions(clause or ""))
    for match in _CONVERSION_PAREN.finditer(clause or ""):
        if not _CONVERSION_CUE.search(match.group(0)):
            continue
        for index in range(match.start(), min(match.end(), len(chars))):
            chars[index] = " "
    window = "".join(chars)
    if _HEDGE.search(window) or _BUYER.search(window):
        return True
    if _PAST.search(window) or _HEARSAY.search(window) or _HYPOTHETICAL.search(window):
        return True
    if _BUYER_ASK.search(window):
        return True
    return _other_speaker(window)


def _implicit_may_accept() -> bool:
    from src.config.heuristics import IMPLICIT_CNY_AUTO_ACCEPT

    return int(IMPLICIT_CNY_AUTO_ACCEPT) == 1


def _stated_conversion_enabled() -> bool:
    from src.config.heuristics import UNIT_BASIS_FROM_MESSAGE

    return int(UNIT_BASIS_FROM_MESSAGE) == 1


def conversion_pieces(text: str, basis: str | None) -> float | None:
    """Pieces in one stated pack. An unstated dozen uses the registered count."""
    if not basis or basis in {"per_piece", "unknown"}:
        return None
    for found in _PACK_COUNT.finditer(text or ""):
        pack = found.group("pack") or found.group("pack2") or ""
        if pack != basis:
            continue
        count = _number(found.group("n") or found.group("n2") or "")
        if count is None or count == 0:
            continue
        return float(count)
    if basis == "打":
        from src.config.heuristics import DOZEN_PIECES

        return float(DOZEN_PIECES)
    return None


def _moq_piece_evidence(text: str, clause: str, basis: str | None) -> bool:
    """单价 plus an MOQ in 件 is the deal unit. It is confirmable, not accepted."""
    if basis is not None:
        return False
    if not _UNIT_LABEL.search(clause or ""):
        return False
    if non_piece_basis(text):
        return False
    return bool(_MOQ_PIECE.search(text or ""))


def _tax_pair(text: str) -> bool:
    """A 含税 amount and a 不含税 amount are two prices."""
    body = text or ""
    return bool(_TAX_INCLUDED.search(body) and _TAX_EXCLUDED.search(body))


def _pieces_label(count: float) -> str:
    whole = int(count)
    if float(whole) == float(count):
        return str(whole)
    return str(count)


# The per-piece shortcut and implicit CNY read a closed list. Anything else in the
# clause is an unrecognised term: the number stays in review, with no one-click
# deal unit and no implicit currency. The lexicon above still converts a stated pack.
_PRICE_CUES: tuple[str, ...] = (
    "不含税", "出厂价", "批发价", "单价", "报价", "价格", "价钱",
    "含税", "税后", "每个", "一个", "每件", "一件", "每只", "一只",
)
_DEAL_UNIT_WORDS: tuple[str, ...] = (
    "per piece", "piece", "pcs", "each", "unit", "pc", "个", "件", "只",
)
_CNY_MARKERS: tuple[str, ...] = ("人民币", "人民幣", "rmb", "cny", "yuan", "元", "块", "塊", "¥")
_USD_MARKERS: tuple[str, ...] = ("美刀", "美元", "美金", "us$", "usd", "$", "刀")
_EUR_MARKERS: tuple[str, ...] = ("欧元", "歐元", "eur", "€", "欧", "歐")
_SUBUNIT_WORDS: tuple[str, ...] = ("毛", "角", "分", "厘", "釐")
_CJK_CHAR = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_MOQ_PHRASE = re.compile(
    r"(?i)(?:起订量|起订|起批|moq)\s*[:：]?\s*\d+(?:[.,]\d+)?\s*"
    r"(?:个|件|只|pcs|pc|piece|each|unit)(?![\u4e00-\u9fffA-Za-z])"
)


def _marker_phrases(deal_currency: str) -> tuple[str, ...]:
    code = (deal_currency or "").upper()
    if code == "USD":
        return _USD_MARKERS
    if code == "EUR":
        return _EUR_MARKERS
    return _CNY_MARKERS


def _is_cjk_char(char: str) -> bool:
    return bool(_CJK_CHAR.match(char or ""))


def _clause_word(char: str) -> bool:
    return _is_cjk_char(char) or (char.isascii() and char.isalpha())


def _skipped_symbol(char: str) -> bool:
    if char.isspace():
        return True
    return unicodedata.category(char).startswith(("P", "Z"))


def _first_unrecognised(clause: str, deal_currency: str) -> str | None:
    """The first letter or CJK token in the clause that is outside the allowlist."""
    text = _fold(clause)
    phrases = sorted(
        {
            *_PRICE_CUES,
            *_DEAL_UNIT_WORDS,
            *_marker_phrases(deal_currency),
            *_SUBUNIT_WORDS,
        },
        key=len,
        reverse=True,
    )
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if _skipped_symbol(char):
            index += 1
            continue
        number = re.match(r"\d+(?:[.,]\d+)?", text[index:])
        if number:
            index += number.end()
            continue
        moq = _MOQ_PHRASE.match(text, index)
        if moq:
            index = moq.end()
            continue
        lowered = text[index:].lower()
        taken = 0
        for phrase in phrases:
            if not lowered.startswith(phrase.lower()):
                continue
            end = index + len(phrase)
            nxt = text[end] if end < length else ""
            if phrase[:1].isascii() and phrase[:1].isalpha() and nxt.isascii() and nxt.isalpha():
                continue
            taken = len(phrase)
            break
        if taken:
            index += taken
            continue
        if _clause_word(char):
            end = index + 1
            cjk = _is_cjk_char(char)
            while end < length and _clause_word(text[end]) and _is_cjk_char(text[end]) == cjk:
                if not cjk and not (text[end].isascii() and text[end].isalpha()):
                    break
                end += 1
            return text[index:end]
        if _OTHER_SYMBOL.match(char):
            return char
        index += 1
    return None


def _basis_prompt(text: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """The one human choice that can settle this number. None when no choice is legal.

    The per-piece shortcut exists only for a number that states no basis, already
    carries the deal currency, and whose clause is nothing but the allowlist.
    """
    if item.get("unrecognised"):
        return None
    if item.get("currency_ok") is not True:
        return None
    basis = str(item.get("basis") or "unknown")
    if basis == "per_piece":
        return None
    if basis in {"", "unknown"}:
        return {"kind": "per_piece", "label": "per piece (deal unit)"}
    allowed = conversion_pieces(text, basis)
    if allowed is None:
        return None
    return {
        "kind": "convert",
        "pieces_per_basis": float(allowed),
        "stated_basis": basis,
        "label": f"{_pieces_label(float(allowed))} pieces per {basis}",
    }


def _tier_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        token = str(item.get("token") or "")
        if not token or token in seen:
            continue
        seen.add(token)
        point = item.get("breakpoint")
        rows.append(
            {
                "token": token,
                "value": float(item["value"]),
                "threshold": None if point is None else float(point),
            }
        )
    return rows


def _highlighted_token(items: list[dict[str, Any]], quantity: float | None) -> str | None:
    """The tier at the deal quantity. It is a highlight, never the prefilled price."""
    if len(items) < 2:
        return None
    picked = _pick_tier(items, quantity)
    if all(item.get("breakpoint") is not None for item in items):
        return None if picked is None else str(picked["token"])
    if quantity is None:
        return None
    qty = float(quantity)

    def floor(item: dict[str, Any]) -> float:
        point = item.get("breakpoint")
        return 0.0 if point is None else float(point)

    eligible = [item for item in items if floor(item) <= qty]
    pool = eligible if eligible else items
    best = max(floor(item) for item in pool) if eligible else min(floor(item) for item in pool)
    chosen = [item for item in pool if floor(item) == best]
    if len(chosen) != 1:
        return None
    return str(chosen[0]["token"])


def _normalised_piece(text: str, mismatches: list[dict[str, Any]]) -> float | None:
    """A stated pack count converts a bundle only when that policy is on."""
    if not _stated_conversion_enabled() or len(mismatches) != 1:
        return None
    found = _PACK_COUNT.search(text or "")
    if found is None:
        return None
    count = _number(found.group("n") or found.group("n2") or "")
    if count is None or count == 0:
        return None
    return float(mismatches[0]["value"]) / float(count)


def _note_currency(item: dict[str, Any], deal_currency: str) -> None:
    """Deal-currency evidence is explicit kind or implicit CNY. Anything else is not."""
    from src.config.heuristics import DEAL_CURRENCY

    kind = item.get("kind")
    if item.get("evidence") == _IMPLICIT_EVIDENCE and kind in {None, deal_currency}:
        item["currency_ok"] = True
        item["currency_state"] = "implicit"
        return
    if kind == deal_currency:
        item["currency_ok"] = True
        item["currency_state"] = "deal"
        return
    if kind is None and deal_currency == DEAL_CURRENCY:
        item["currency_ok"] = False
        item["currency_state"] = "missing"
        return
    item["currency_ok"] = False
    item["currency_state"] = "other"


def _currency_failed(item: dict[str, Any], deal_currency: str) -> bool:
    if item.get("currency_state") == "other":
        return True
    kind = item.get("kind")
    return kind == "foreign" or (kind is not None and kind != deal_currency)


def _collect(text: str, deal_currency: str) -> dict[str, Any]:
    """Unit rows, other money, fees, and labelled references. A measure word is not a currency."""
    from src.config.heuristics import DEAL_CURRENCY

    units: list[dict[str, Any]] = []
    competing: list[dict[str, Any]] = []
    review_tokens: list[dict[str, Any]] = []
    fees: list[dict[str, Any]] = []
    labelled_references: list[dict[str, Any]] = []
    unknown_currency = False
    ranges = _range_spans(text)
    conversions = _conversion_spans(text)
    chinese = bool(_CJK.search(text))
    consumed: list[tuple[int, int]] = []

    def _keep(bucket: list[dict[str, Any]], row: dict[str, Any]) -> None:
        _note_currency(row, deal_currency)
        bucket.append(row)

    for match in _AMOUNT.finditer(text):
        token = match.group("num")
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        value = _number(token)
        if value is None or token not in text or _glued_amount(text, match):
            continue
        span = (match.start(), match.end())
        if _inside((match.start("num"), match.end("num")), consumed):
            continue
        if _inside(span, ranges) or _inside(span, conversions):
            continue
        left, _right = _clause_span(text, match.start("num"))
        clause = text[left:_right]
        kind = _kind_for(pre, post, clause)
        if _quantity_amount(text, match.start("num"), token) or _duration_amount(text, match.start("num"), token):
            continue
        amount_at = match.start("num") - left
        if _excluded_number(text, match.start("num"), token, clause, amount_at):
            continue
        tail = text[match.end("num"):]
        compound = _compound_minor(tail)
        factor = None
        if compound is not None:
            minor, parts = compound
            if value is not None:
                value = float(value) + minor
                for part_start, part_end in parts:
                    consumed.append((match.end("num") + part_start, match.end("num") + part_end))
        else:
            factor = _subunit_factor(tail)
            if factor is not None and value is not None:
                value = float(value) * factor
                if kind is None:
                    kind = deal_currency
        sentence = _sentence_of(text, match.start("num"))
        basis = _basis_for_number(text, match.start("num"), match.end("num"))
        offender = _first_unrecognised(clause, deal_currency)
        per_piece = basis == "per_piece"
        fee = _fee_of(clause, amount_at, token)
        key = round(float(value), 4)
        unit_name = "per_piece" if per_piece and basis in {None, "per_piece"} else (basis or "unknown")
        item = {
            "token": token,
            "value": value,
            "span": span,
            "kind": kind,
            "breakpoint": None,
            "basis": basis or unit_name,
            "unit": "per_piece" if unit_name == "per_piece" else unit_name,
            "identity": (key, str(kind or ""), unit_name),
        }
        if _TWD.search(_fold(sentence)):
            kind = "foreign"
            item["kind"] = kind
            item["identity"] = (key, str(kind), unit_name)
        if offender and basis is None:
            item["unrecognised"] = offender

        if fee:
            item["label"] = fee
            _keep(fees, item)
            _keep(competing, item)
            continue
        if _reference_clause(clause):
            item["label"] = "reference"
            _keep(labelled_references, item)
            continue
        if _taiwan_unknown(sentence, pre, post):
            unknown_currency = True
            continue
        if kind == "foreign" or (kind is not None and kind != deal_currency):
            if basis != "per_piece":
                item["issue"] = "unit_mismatch"
            _keep(competing, item)
            continue
        if not _price_attached(clause, amount_at, token, pre, post) and basis is None:
            continue
        if kind is None and deal_currency != DEAL_CURRENCY:
            if basis != "per_piece":
                item["issue"] = "unit_mismatch"
            _keep(competing, item)
            _keep(review_tokens, item)
            continue
        if basis != "per_piece":
            if not _offer_speech(clause):
                continue
            if _moq_piece_evidence(text, clause, basis) and not offender:
                item["basis"] = "per_piece"
                item["unit"] = "per_piece"
                item.pop("unrecognised", None)
                _keep(review_tokens, item)
                continue
            if (
                basis is None
                and not offender
                and chinese
                and deal_currency == DEAL_CURRENCY
                and not pre
                and not post
                and factor is None
            ):
                item["evidence"] = _IMPLICIT_EVIDENCE
            item["issue"] = "unit_mismatch"
            item["basis"] = basis or "unknown"
            item["unit"] = basis or "unknown"
            _keep(review_tokens, item)
            continue
        if _LOT.search(clause) and not _PER_PIECE.search(clause) and not _UNIT_LABEL.search(clause):
            item["issue"] = "unit_mismatch"
            item["basis"] = "lot"
            item["unit"] = "lot"
            _keep(review_tokens, item)
            continue
        if kind is None:
            # A bare number is a confirmable candidate only when a unit-price
            # label or a per-piece marker sits on that number. A quantity
            # breakpoint (1000个, 5000 pcs) is not the price.
            tail = clause[amount_at:amount_at + len(token) + 6]
            if re.match(rf"(?i){re.escape(token)}\s*(?:个|件|只|pcs|pc|pieces|k)\b", tail):
                continue
            near = clause[max(0, amount_at - 16):amount_at + len(token) + 16]
            sentence = _sentence_of(text, match.start("num"))
            cued = bool(_IMPLICIT_CUE.search(sentence))
            labelled = bool(
                _UNIT_LABEL.search(near) or _QUOTE_LABEL.search(near) or _PER_PIECE.search(near) or cued
            )
            if not labelled:
                continue
            if _bare_blocked_by_other_currency(text, match.start("num"), token, deal_currency):
                continue
            if chinese and deal_currency == DEAL_CURRENCY and cued and not offender:
                item["evidence"] = _IMPLICIT_EVIDENCE
                if _implicit_may_accept() and per_piece:
                    ok, breakpoint = _slots_ok(clause, amount_at, value)
                    item["breakpoint"] = breakpoint
                    if ok:
                        _keep(units, item)
                        continue
            _keep(review_tokens, item)
            continue
        window = _for_rules(clause)
        if _INSTRUCTION.search(window):
            item["label"] = "reference"
            _keep(labelled_references, item)
            continue
        ok, breakpoint = _slots_ok(clause, amount_at, value)
        item["breakpoint"] = breakpoint
        _keep(review_tokens, item)
        if not ok:
            _keep(competing, item)
            continue
        _keep(units, item)
    kept_units, kept_competing = _dedupe(units, competing)
    return {
        "units": kept_units,
        "competing": kept_competing,
        "review_tokens": review_tokens,
        "ranges": bool(ranges),
        "fees": fees,
        "labelled_references": labelled_references,
        "unknown_currency": unknown_currency,
    }


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
    collected = _collect(visible, currency)
    units = collected["units"]
    competing = collected["competing"]
    review_tokens = collected["review_tokens"]
    saw_range = collected["ranges"]
    fees = collected["fees"]
    labelled_references = collected["labelled_references"]
    unknown_currency = bool(collected.get("unknown_currency"))
    tokens = numeric_tokens(text)
    distinct = _distinct([value for _, value in tokens])
    reason = None
    unit_price = None
    accepted = False
    chosen: dict[str, Any] | None = None
    mismatches = [item for item in review_tokens if item.get("issue") == "unit_mismatch"]
    foreign = [
        item for item in competing
        if item.get("currency_state") == "other" or item.get("kind") not in (None, currency)
    ]
    if saw_range:
        reason = "price range"
    elif _tax_pair(visible) and (units or review_tokens):
        reason = "more than one price"
    elif len(units) == 1 and not competing:
        chosen = units[0]
    elif len(units) > 1 and not competing:
        reason = "more than one price"
    elif mismatches and not units:
        if any(item.get("currency_state") == "other" for item in mismatches):
            reason = "not the deal currency"
        elif all(str(item.get("basis") or "") in {"", "unknown"} for item in mismatches):
            named = next((item.get("unrecognised") for item in mismatches if item.get("unrecognised")), None)
            reason = f"unrecognised term: {named}" if named else "unit mismatch"
        else:
            reason = "unit mismatch"
    elif fees and not units and not review_tokens:
        reason = "fee, not a unit price"
    elif labelled_references and not units and not review_tokens and not competing:
        reason = "reference, not this supplier's price"
    elif unknown_currency and not units and not review_tokens and not competing:
        reason = "unknown currency"
    elif competing and not units:
        reason = "not the deal currency" if foreign else "no usable amount in the deal currency"
    elif competing:
        reason = "more than one price"
    elif review_tokens:
        reason = None
    else:
        reason = "no currency-anchored price"
    if chosen is not None:
        settled = _settled(chosen["token"])
        if settled is not None and chosen["token"] in text:
            accepted = True
            unit_price = float(chosen["value"])
            reason = None
        else:
            chosen = None
            reason = reason or "no usable amount in the deal currency"
    candidate_tokens: list[str] = []
    shown_values: dict[str, float] = {}
    for item in units + review_tokens:
        if item["token"] not in candidate_tokens:
            candidate_tokens.append(item["token"])
        shown_values.setdefault(str(item["token"]), float(item["value"]))
    # One attached price can be the one-click prefill. Two prices, and neither is.
    if chosen is not None:
        suggested = chosen["token"]
    elif len(candidate_tokens) == 1 and not _tax_pair(visible):
        suggested = candidate_tokens[0]
    else:
        suggested = None
    if len(candidate_tokens) > 1 or _tax_pair(visible):
        suggested = None
    bases: list[str] = []
    for item in units + review_tokens:
        label = str(item.get("basis") or "")
        if label and label not in bases:
            bases.append(label)
    evidence = None
    if any(item.get("evidence") == _IMPLICIT_EVIDENCE for item in units + review_tokens):
        evidence = _IMPLICIT_EVIDENCE
    quoted_amount = None
    if len(candidate_tokens) > 1 or _tax_pair(visible):
        quoted_amount = None
    elif accepted and chosen is not None:
        quoted_amount = float(chosen["value"])
    elif review_tokens:
        quoted_amount = float(review_tokens[0]["value"])
    elif units:
        quoted_amount = float(units[0]["value"])
    goods: list[dict[str, Any]] = []
    seen_goods: set[str] = set()
    for item in units + review_tokens:
        if item.get("issue") not in (None, "unit_mismatch"):
            continue
        token = str(item.get("token") or "")
        if not token or token in seen_goods:
            continue
        seen_goods.add(token)
        goods.append(item)
    tier_list = _tier_rows(goods) if len(goods) > 1 else []
    highlighted = _highlighted_token(goods, quantity) if len(goods) > 1 else None
    prompts: dict[str, dict[str, Any]] = {}
    for item in units + review_tokens:
        token = str(item.get("token") or "")
        if not token or token in prompts:
            continue
        prompt = _basis_prompt(visible, item)
        if prompt is not None:
            prompts[token] = prompt
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
        "confirm_value": None if suggested is None else shown_values.get(str(suggested)),
        "confirm_span": None if not suggested else _confirm_span(stage_text, suggested),
        "confirm_spans": {
            token: _confirm_span(stage_text, token) for token in candidate_tokens if token
        },
        "review_reason": None if accepted else reason,
        "review_reasons": [] if accepted else _reading_reasons(collected, currency),
        "unit_basis": bases,
        "currency_evidence": evidence,
        "fee_lines": [
            {"token": item["token"], "value": float(item["value"]), "label": item.get("label") or "fee"}
            for item in fees
        ],
        "labelled_references": [
            {"token": item["token"], "value": float(item["value"]), "label": "reference"}
            for item in labelled_references
        ],
        "normalised_unit_price": _normalised_piece(visible, mismatches),
        "tiers": tier_list,
        "highlighted_token": highlighted,
        "basis_prompts": prompts,
    }
    return {
        "parsed": parsed,
        "confidence": "high" if accepted else "low",
        "status": "accepted" if accepted else "review",
        "provenance": provenance,
    }


_REASON_CURRENCY = "not the deal currency"
_REASON_REFERENCE = "reference, not this supplier's price"
_REASON_FEE = "fee, not a unit price"
_REASON_BASIS = "unit mismatch"


def _item_reasons(item: dict[str, Any], deal_currency: str, *, goods: bool) -> list[str]:
    reasons: list[str] = []
    if _currency_failed(item, deal_currency):
        reasons.append(_REASON_CURRENCY)
    label = str(item.get("label") or "")
    if label == "reference":
        reasons.append(_REASON_REFERENCE)
    if label and label != "reference":
        reasons.append(_REASON_FEE)
    term = item.get("unrecognised")
    if term:
        reasons.append(f"unrecognised term: {term}")
    stated = str(item.get("basis") or "")
    if item.get("issue") == "unit_mismatch" or (
        goods and stated not in {"", "per_piece", "unknown"}
    ):
        reasons.append(_REASON_BASIS)
    return reasons


def _ordered_reasons(reasons: list[str]) -> list[str]:
    order = (_REASON_CURRENCY, _REASON_REFERENCE, _REASON_FEE)
    ordered = [reason for reason in order if reason in reasons]
    ordered.extend(reason for reason in reasons if str(reason).startswith("unrecognised term:"))
    if _REASON_BASIS in reasons:
        ordered.append(_REASON_BASIS)
    return ordered


def _reading_reasons(collected: dict[str, Any], deal_currency: str) -> list[str]:
    """Every failing check on the numbers this reading kept. Currency stays first."""
    reasons: list[str] = []
    goods = collected["units"] + collected["review_tokens"]
    pools = (
        (collected["units"], True),
        (collected["review_tokens"], True),
        (collected["competing"], False),
        (collected["fees"], False),
        (collected["labelled_references"], False),
    )
    seen: set[int] = set()
    for bucket, is_goods in pools:
        for item in bucket:
            marker = id(item)
            if marker in seen and not is_goods:
                continue
            seen.add(marker)
            # A goods row that was also copied into competing still reports basis.
            report_goods = is_goods or item in goods
            for reason in _item_reasons(item, deal_currency, goods=report_goods):
                if reason not in reasons:
                    reasons.append(reason)
    return _ordered_reasons(reasons)


def token_issues(raw_text: str, token: str, deal_currency: str | None = None) -> list[str]:
    """Failing checks for ``token``, currency first. An empty list may be confirmed."""
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    visible = _visible_text(raw_text or "")
    if not token or token not in visible:
        return []
    collected = _collect(visible, currency)
    goods = [
        item for item in collected["units"] + collected["review_tokens"]
        if item.get("token") == token
    ]
    clean = [
        item for item in goods
        if item.get("issue") != "unit_mismatch" and item.get("currency_ok") is True
        and str(item.get("basis") or "") == "per_piece"
    ]
    if clean:
        return []
    reasons: list[str] = []
    related = goods + [
        item for item in (
            collected["competing"] + collected["fees"] + collected["labelled_references"]
        )
        if item.get("token") == token
    ]
    for item in related:
        for reason in _item_reasons(
            item,
            currency,
            goods=item in goods or item in collected["review_tokens"] or item in collected["units"],
        ):
            if reason not in reasons:
                reasons.append(reason)
    return _ordered_reasons(reasons)


def token_issue(raw_text: str, token: str, deal_currency: str | None = None) -> str | None:
    """Why a reviewer cannot confirm ``token``. None means the confirm path may read it."""
    found = token_issues(raw_text, token, deal_currency=deal_currency)
    return found[0] if found else None


def token_currency_ok(raw_text: str, token: str, deal_currency: str | None = None) -> bool:
    """True when this number already carries the deal currency."""
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    visible = _visible_text(raw_text or "")
    if not token or token not in visible:
        return False
    collected = _collect(visible, currency)
    for item in collected["units"] + collected["review_tokens"]:
        if item.get("token") != token:
            continue
        if item.get("issue") not in (None, "unit_mismatch"):
            continue
        return bool(item.get("currency_ok"))
    return False


def confirm_amount(
    raw_text: str, token: str, deal_currency: str | None = None
) -> tuple[float, str] | None:
    """Goods-candidate value and basis. A fee or a reference is not one."""
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    visible = _visible_text(raw_text or "")
    if not token or token not in visible:
        return None
    collected = _collect(visible, currency)
    for item in collected["units"] + collected["review_tokens"]:
        if item.get("token") != token:
            continue
        if item.get("issue") not in (None, "unit_mismatch"):
            continue
        return float(item["value"]), str(item.get("basis") or "unknown")
    return None


def price_from_token(raw_text: str, token: str, deal_currency: str | None = None) -> float | None:
    """A human may confirm a token when that digit string is in the text and is not a foreign amount.

    The grammar may have left the message in review. The confirmation is the
    missing evidence. A token anchored to another currency stays None.
    A fee, a hedge, or a basis other than the deal unit stays None as well.
    Unknown is not the deal unit: this path does not choose a basis.
    """
    from src.config.heuristics import DEAL_CURRENCY

    currency = deal_currency or DEAL_CURRENCY
    if token_issue(raw_text, token, deal_currency=currency):
        return None
    found = confirm_amount(raw_text, token, deal_currency=currency)
    if found is None or found[1] != "per_piece":
        return None
    amount = float(found[0])
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
        return amount
    # An unmarked number is confirmable only for the yuan default.
    if currency == DEAL_CURRENCY and bare_value is not None and not other:
        return amount
    return None
