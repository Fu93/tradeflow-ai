"""Money quantisation. One place, one rounding rule.

Why this module exists
----------------------
The counter was stored at full float precision (``8.196919283``) while the
draft body the supplier receives was rendered with ``f"{counter:.2f}"``
(``8.20``). Two consequences, both real:

1. The approved ``body_sha256`` covers a body that contains a number the
   decision record does not contain. The record and the message disagree about
   the price, which is exactly the property an audit trail is supposed to fix.
2. A ceiling of ``8.1952`` passes ``counter <= ceiling`` at float precision,
   while the supplier is offered ``8.20`` — above the ceiling the buyer set.

The fix is not "round more carefully at the edges". It is to quantise once, at
the single choke point where the issued number is computed, and to make the
record refuse a counter that is not a whole number of minor units.

``Decimal`` with ``ROUND_HALF_UP`` is deliberate: ``round()`` is banker's
rounding, so ``to_minor(0.005)`` is 0 and ``to_minor(0.015)`` is 2 — an
asymmetric rule that is wrong for money and impossible to explain to a supplier.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from src.config.heuristics import MINOR_SCALE


def minor_decimals(scale: int = MINOR_SCALE) -> int:
    """Number of decimal places implied by the minor-unit scale (100 → 2).

    Derived from the digit count rather than a loop with a literal bound, so
    this module adds no numeric literal that the decision-literal registry would
    have to justify.
    """
    return max(0, len(str(int(scale))) - 1)


def _quantum(scale: int = MINOR_SCALE) -> Decimal:
    return Decimal(1).scaleb(-minor_decimals(scale))


def quantise(amount: float, scale: int = MINOR_SCALE) -> float:
    """Round to the nearest minor unit, half away from zero.

    Half away from zero matches what a human writes on paper and what
    ``Decimal``'s ``ROUND_HALF_UP`` does for positive values, which is the only
    sign money has here.
    """
    value = Decimal(str(float(amount)))
    return float(value.quantize(_quantum(scale), rounding=ROUND_HALF_UP))


def quantise_down(amount: float, scale: int = MINOR_SCALE) -> float:
    """Round toward zero. Used for caps: a cap must never be rounded upward."""
    value = Decimal(str(float(amount)))
    return float(value.quantize(_quantum(scale), rounding=ROUND_DOWN))


def quantise_offer(offer: float, ceiling: float, ask: float, scale: int = MINOR_SCALE) -> float:
    """The number to put in front of a supplier.

    Rounded to the nearest whole minor unit, then held at or below both caps
    *at minor-unit precision*. Rounding a value of 8.1952 up to 8.20 would
    otherwise breach a ceiling of 8.1952 — the record would say "within
    ceiling" and the supplier would read a price above it.

    Only the caps are floored. Flooring the offer itself would shave a cent off
    every price that happened to land on a half-cent, which is a different bug.
    """
    capped = min(float(offer), float(ceiling), float(ask))
    rounded = quantise(capped, scale)
    cap = quantise_down(min(float(ceiling), float(ask)), scale)
    return min(rounded, cap)


def is_quantised(amount: float, scale: int = MINOR_SCALE) -> bool:
    """True when the value is already a whole number of minor units."""
    value = Decimal(str(float(amount)))
    return value == value.quantize(_quantum(scale), rounding=ROUND_HALF_UP)


def to_minor(amount: float, scale: int = MINOR_SCALE) -> int:
    """Integer minor units. Replaces the banker's-rounding helper in record.py."""
    return int(Decimal(str(float(amount))).quantize(_quantum(scale), rounding=ROUND_HALF_UP) * int(scale))
