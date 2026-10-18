"""Supplier-quote fields. The language model never sets the buyer's price.

Stage-1 patterns and the clamps in ``InboundIntentClassifier`` decide which
numbers are usable. A model JSON is validated through the same bounds. Out of
range becomes a rejection, not a guess.
"""
from __future__ import annotations

import re
from typing import Any

from src.config.heuristics import DEPOSIT_HARD_CAP
from src.engine.bilingual import InboundIntentClassifier

# Lead time uses the same integer bounds as RoundActionRequest.offered_lead_days.
LEAD_TIME_MIN_DAYS = 1
LEAD_TIME_MAX_DAYS = 3650

_MOQ_WORD = re.compile(r"(?:MOQ|起订量|起訂量)\s*[:：]?\s*(\d{1,7})", re.IGNORECASE)
_DEPOSIT_EN = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*deposit|deposit\s*[:：]?\s*(\d{1,2}(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)

ACCURACY_NOTE = (
    "No measured extraction accuracy for Gemma-4-31B-IT is in this repository. "
    "Offline fields come from the deterministic reader. "
    "The model never sets our price."
)


def price_the_solver_may_use(
    explicit_price: float | None,
    stage1_price: float | None,
    llm_price: float | None,
) -> float | None:
    """Return the offer the solver may see.

    ``llm_price`` is accepted so callers can show they passed the model output
    in, and then discarded it. It does not affect the result.
    """
    del llm_price
    if explicit_price is not None:
        return explicit_price
    return stage1_price


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _lead_days(text: str) -> int | None:
    match = InboundIntentClassifier.LEAD_DAYS_PATTERN.search(text or "")
    if not match:
        return None
    raw = next((group for group in match.groups() if group), None)
    number = _as_float(raw)
    if number is None:
        return None
    days = int(number)
    if LEAD_TIME_MIN_DAYS <= days <= LEAD_TIME_MAX_DAYS:
        return days
    return None


def _deposit_fraction_from_text(text: str) -> float | None:
    match = InboundIntentClassifier.DEPOSIT_PATTERN.search(text or "")
    raw = None
    if match:
        raw = next((group for group in match.groups() if group), None)
    if raw is None:
        english = _DEPOSIT_EN.search(text or "")
        if english:
            raw = next((group for group in english.groups() if group), None)
    return _deposit_fraction(_as_float(raw) if raw is not None else None)[0]


def _deposit_fraction(value: float | None) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    fraction = value / 100 if value > 1 else value
    if fraction <= 0 or fraction > DEPOSIT_HARD_CAP:
        return None, "outside deposit bounds"
    return fraction, None


def _moq_from_text(text: str, stage1_moq: int | None) -> int | None:
    if stage1_moq is not None:
        return stage1_moq
    match = _MOQ_WORD.search(text or "")
    if not match:
        return None
    return InboundIntentClassifier._clamp_moq(match.group(1))


def extract_stage1(text: str) -> dict:
    """Deterministic fields. No network."""
    classification = InboundIntentClassifier.classify_stage1_only(text or "")
    return {
        "price": classification.mentioned_price,
        "moq": _moq_from_text(text or "", classification.demanded_moq),
        "lead_time_days": _lead_days(text or ""),
        "deposit_pct": _deposit_fraction_from_text(text or ""),
        "quoted_amount": classification.quoted_amount,
        "currency": classification.mentioned_currency,
        "currency_note": classification.price_currency_note,
    }


def validate_fields(raw: dict | None) -> dict:
    """Clamp a model or stage-1 dict. Rejected keys are listed, not invented."""
    source = raw or {}
    rejected: list[dict[str, str]] = []

    price_in = source.get("price", source.get("mentioned_price"))
    price = InboundIntentClassifier._clamp_price(price_in)
    if price_in not in (None, "") and price is None:
        rejected.append({"field": "price", "reason": "outside price bounds"})

    moq_in = source.get("moq", source.get("demanded_moq"))
    moq = InboundIntentClassifier._clamp_moq(moq_in)
    if moq_in not in (None, "") and moq is None:
        rejected.append({"field": "moq", "reason": "outside MOQ bounds"})

    lead_in = source.get("lead_time_days")
    lead_number = _as_float(lead_in)
    lead = None
    if lead_in not in (None, ""):
        if lead_number is None:
            rejected.append({"field": "lead_time_days", "reason": "not a number"})
        else:
            lead = int(lead_number)
            if lead < LEAD_TIME_MIN_DAYS or lead > LEAD_TIME_MAX_DAYS:
                rejected.append({"field": "lead_time_days", "reason": "outside lead-time bounds"})
                lead = None

    deposit_in = source.get("deposit_pct")
    deposit_number = _as_float(deposit_in)
    deposit, deposit_reason = _deposit_fraction(deposit_number)
    if deposit_in not in (None, "") and deposit is None:
        rejected.append({"field": "deposit_pct", "reason": deposit_reason or "outside deposit bounds"})

    return {
        "price": price,
        "moq": moq,
        "lead_time_days": lead,
        "deposit_pct": deposit,
        "rejected": rejected,
    }
