"""Shadow agreement gate. It records a would-be accept and does not apply it.

Phase 1 still requires the user's click. Gated precision is the share of
those would-be accepts that the user later confirmed unchanged, with a
Wilson interval. The certification count is 189 resolved accepts.
"""
from __future__ import annotations

import math
import re
from typing import Any

from src.config.heuristics import (
    DEAL_CURRENCY,
    GATE_CERTIFY_ACCEPTS,
    NEIGHBORHOOD_CHARS,
    WILSON_QUARTIC,
    Z_95,
)
from src.desk.extract import extract_mentions
from src.desk.span_read import currency_marked, per_piece_marked, read_amounts, span_in_message

# A span that names a total, a duty, or a fee is not a goods unit price,
# even when the model calls it an own offer.
_NOT_UNIT_GOODS = re.compile(
    r"(?i)(?:\btotal\b|\bduty\b|\bshipping\b|\bfreight\b|\bsample\s+fee\b|"
    r"\bmould\b|\bmold\b|合计|合計|总额|總額|运费|運費|关税|關稅|样品费|樣品費|模具|"
    r"打样费|打樣費|开模费|開模費|服务费|服務費|手续费|手續費|"
    r"元\s*/\s*(?:小时|小時|天|次))"
)
_PIECE_UNIT = re.compile(r"(?i)(?:件|个|個|只|隻|per\s*piece|/\s*pcs?\b|每件|每个|每個|每只|每隻)")
_REFERENCE_TEXT = re.compile(
    r"(?i)(?:不到|低于|低於|高于|高於|大概|左右|最多|至少|比.{0,12}便宜|"
    r"别家|別家|同行|市场价|市場價|你报|你方|your\s+target|your\s+price|your\s+offer)"
)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_CUE = re.compile(
    r"报价|報價|单价|單價|价格|價格|多少钱|多少錢|含税|含稅|不含税|不含稅|起订|起訂|"
    r"每\s*(?:个|個|件|只|隻|条|條|套|箱|斤|米|包)|一\s*(?:个|個|件|只|隻|条|條|套|箱)"
)
_QTY_PHRASE = re.compile(
    r"(?i)(?:moq|起订量|起訂量|起订|起訂|数量|數量|最少)\s*\d+(?:,\d{3})*(?:\.\d+)?"
    r"\s*(?:个|個|件|只|隻|条|條|套|箱|包|pcs|pc|pieces)?"
)
_FEE_PHRASE = re.compile(
    r"(?i)(?:运费|運費|打样费|打樣費|开模费|開模費|服务费|服務費|手续费|手續費|"
    r"freight|shipping|sample\s+fee)\s*\d+(?:,\d{3})*(?:\.\d+)?"
    r"|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元|块|塊)?\s*/\s*(?:小时|小時|天|次)"
)


def _neighborhood(message: str, span: str) -> str:
    """A short window around the cited span. The currency mark often sits beside the number."""
    index = str(message).find(str(span))
    if index < 0:
        return ""
    radius = int(NEIGHBORHOOD_CHARS)
    return str(message)[max(0, index - radius): index + len(span) + radius]


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """Two-sided Wilson interval. ``total`` is resolved shadow accepts, not messages."""
    count = int(total)
    if count <= 0:
        return None
    wins = min(max(int(successes), 0), count)
    phat = wins / count
    zed = float(z)
    z2 = zed * zed
    denom = 1.0 + z2 / count
    centre = (phat + z2 / (2 * count)) / denom
    margin = zed * math.sqrt((phat * (1.0 - phat) + z2 / (int(WILSON_QUARTIC) * count)) / count) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _rule_value(reading: dict[str, Any]) -> float | None:
    if reading.get("status") != "accepted":
        return None
    price = (reading.get("parsed") or {}).get("unit_price")
    if price is None:
        return None
    return float(price)


def _implicit_cny(message: str, span: str, nearby: str, currency: str) -> bool:
    """A Chinese price cue on a CNY deal names the currency when the span itself does not."""
    if str(currency) != "CNY":
        return False
    if not _CJK.search(message):
        return False
    return _CUE.search(str(span) + str(nearby)) is not None


def _basis_ok(span: str, nearby: str) -> bool:
    """The span's measure word has to be the deal unit. A pack is not a piece."""
    from src.desk.parse import non_piece_basis

    if _PIECE_UNIT.search(span) and not non_piece_basis(span):
        return True
    if non_piece_basis(span) or non_piece_basis(nearby):
        return False
    return True


def _priced_amount(span: str) -> float | None:
    """One goods price in the span. A MOQ or a fee beside it is a different role."""
    cleaned = _FEE_PHRASE.sub(" ", _QTY_PHRASE.sub(" ", str(span or "")))
    amounts = read_amounts(cleaned)
    if len(amounts) != 1:
        return None
    return float(amounts[0])


def _mention_agrees(
    mention: dict[str, Any],
    message: str,
    rule_value: float | None,
    low: float,
    high: float,
    currency: str,
) -> float | None:
    if mention.get("class") != "own_unit_offer":
        return None
    if mention.get("class") == "instruction":
        return None
    if str(mention.get("speaker") or "") != "supplier":
        return None
    if str(mention.get("polarity") or "") != "affirmative":
        return None
    if bool(mention.get("is_question")):
        return None
    span = str(mention.get("evidence_span") or "")
    if not span_in_message(span, message):
        return None
    if _NOT_UNIT_GOODS.search(span):
        return None
    nearby = _neighborhood(message, span)
    if str(mention.get("currency") or "") != currency:
        return None
    marked = currency_marked(span, currency) or currency_marked(nearby, currency)
    if not marked and not _implicit_cny(message, span, nearby, currency):
        return None
    if str(mention.get("unit") or "") != "per_piece" and not (per_piece_marked(span) or per_piece_marked(nearby)):
        return None
    if not _basis_ok(span, nearby):
        return None
    if _REFERENCE_TEXT.search(span):
        return None
    amount = _priced_amount(span)
    if amount is None:
        return None
    if not _rule_agrees(span, amount, str(mention.get("unit") or ""), currency):
        return None
    if amount < float(low) or amount > float(high):
        return None
    return amount


def _rule_agrees(span: str, amount: float, unit: str, currency: str) -> bool:
    """The rule parser reads the span on its own. A regex hit is not agreement."""
    import math

    from src.desk.parse import parse_quote

    reading = parse_quote(span, deal_currency=currency)
    if reading.get("status") != "accepted":
        return False
    price = (reading.get("parsed") or {}).get("unit_price")
    if price is None or not math.isclose(float(price), float(amount)):
        return False
    bases = list((reading.get("provenance") or {}).get("unit_basis") or [])
    if "per_piece" not in bases:
        return False
    if per_piece_marked(span) and unit != "per_piece":
        return False
    parsed_currency = str((reading.get("parsed") or {}).get("currency") or "")
    return parsed_currency == str(currency)


def _price_list(message: str, currency: str) -> bool:
    """Several goods prices in one message are a list. The gate does not pick one."""
    from src.desk.parse import parse_quote

    reading = parse_quote(message, deal_currency=currency)
    candidates = list((reading.get("provenance") or {}).get("candidates") or [])
    return len(candidates) > 1


def evaluate_shadow(
    message: str,
    reading: dict[str, Any],
    *,
    low: float,
    high: float,
    mentions: list[dict[str, Any]] | None = None,
    extractor_configured: bool = False,
    currency: str = DEAL_CURRENCY,
) -> dict[str, Any]:
    """Would the gate have accepted? Phase 1 does not apply the answer."""
    rule_value = _rule_value(reading)
    span_value = None
    would = False
    instructed = set()
    if mentions:
        for mention in mentions:
            if mention.get("class") == "instruction":
                instructed.add(str(mention.get("evidence_span") or ""))
    if extractor_configured and mentions and not _price_list(message, currency):
        for mention in mentions:
            span = str(mention.get("evidence_span") or "")
            if mention.get("class") == "instruction" or span in instructed:
                continue
            agreed = _mention_agrees(mention, message, rule_value, low, high, currency)
            if agreed is None:
                continue
            span_value = agreed
            would = True
            break
    return {
        "would_accept": would,
        "applied": False,
        "rule_value": rule_value,
        "span_value": span_value,
        "extractor_configured": bool(extractor_configured),
        "auto_accept": False,
    }


def shadow_from_message(
    message: str,
    reading: dict[str, Any],
    *,
    low: float,
    high: float,
    transport=None,
    currency: str = DEAL_CURRENCY,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    extracted = extract_mentions(message, transport=transport, budget_seconds=budget_seconds)
    unstable = False
    if extracted.get("configured") and extracted.get("mentions") and not extracted.get("error"):
        second = extract_mentions(message, transport=transport, budget_seconds=budget_seconds)
        if second.get("error") or _mention_signature(second.get("mentions")) != _mention_signature(
            extracted.get("mentions")
        ):
            unstable = True
    decision = evaluate_shadow(
        message,
        reading,
        low=low,
        high=high,
        mentions=extracted.get("mentions") or [],
        extractor_configured=bool(extracted.get("configured")),
        currency=currency,
    )
    decision["numeric_field_discarded"] = bool(extracted.get("numeric_field_discarded"))
    decision["mentions"] = extracted.get("mentions") or []
    decision["rejected_rows"] = int(extracted.get("rejected_rows") or 0)
    decision["model"] = extracted.get("model") or ""
    decision["prompt_sha256"] = extracted.get("prompt_sha256") or ""
    decision["response_sha256"] = extracted.get("response_sha256") or ""
    decision["attempts"] = list(extracted.get("attempts") or [])
    if extracted.get("error"):
        decision["error"] = extracted["error"]
    if unstable:
        decision["would_accept"] = False
        decision["unstable"] = True
    return decision


def _mention_signature(mentions: list[dict[str, Any]] | None) -> tuple:
    rows = []
    for mention in mentions or []:
        rows.append(
            (
                str(mention.get("class") or ""),
                str(mention.get("evidence_span") or ""),
                str(mention.get("currency") or ""),
                str(mention.get("unit") or ""),
                str(mention.get("speaker") or ""),
            )
        )
    return tuple(sorted(rows))


def progress(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rows are shadow accepts the user has resolved."""
    resolved = [row for row in rows if row.get("would_accept") and row.get("resolved")]
    unchanged = [row for row in resolved if row.get("matched")]
    interval = wilson_interval(len(unchanged), len(resolved))
    return {
        "confirmed_unchanged": len(unchanged),
        "resolved_shadow_accepts": len(resolved),
        "target": int(GATE_CERTIFY_ACCEPTS),
        "wilson_lower": None if interval is None else interval[0],
        "wilson_upper": None if interval is None else interval[1],
        "auto_accept": False,
    }
