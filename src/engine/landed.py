"""Landed unit cost from prices the caller actually supplied.

Missing freight, duty, fees, or an FX rate needed for a currency change is
``no data``. A missing component is not treated as zero. When the quote
currency is omitted or matches the base currency, no FX rate is invented:
the figure is already in the base currency.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import math
from typing import Any


def _same_currency(currency: str | None, base_currency: str | None) -> bool:
    if not currency or not base_currency:
        return True
    return currency.strip().upper() == base_currency.strip().upper()


def landed_unit_cost(
    *,
    unit_price: float | None,
    freight: float | None = None,
    duty_rate: float | None = None,
    fees: float | None = None,
    fx_to_base: float | None = None,
    currency: str | None = None,
    base_currency: str | None = None,
) -> dict[str, Any]:
    """Unit price plus freight, duty, and fees, converted into the base currency.

    Duty is applied to (unit + freight + fees). ``fx_to_base`` is units of
    base currency per one unit of the quote currency, and only when the
    caller supplied it.
    """
    missing: list[str] = []
    if unit_price is None:
        missing.append("unit_price")
    if freight is None:
        missing.append("freight")
    if duty_rate is None:
        missing.append("duty")
    if fees is None:
        missing.append("fees")
    same = _same_currency(currency, base_currency)
    if not same and fx_to_base is None:
        missing.append("fx")
    unit_base = None
    if unit_price is not None and (same or fx_to_base is not None):
        rate = 1.0 if same else float(fx_to_base)
        unit_base = float(unit_price) * rate
    if missing:
        return {
            "unit_price_base": unit_base,
            "landed_cost": None,
            "adder": None,
            "status": "no data",
            "missing": missing,
            "fx_to_base": None if (not same and fx_to_base is None) else (1.0 if same else float(fx_to_base)),
            "currency": currency,
            "base_currency": base_currency,
        }
    # Decimal input strings preserve submitted financial amounts at cent
    # boundaries. A binary-float sum can otherwise shave a cent off a cap
    # when the landed comparison is inverted and rounded down.
    rate_decimal = Decimal(1) if same else Decimal(str(fx_to_base))
    unit, shipping, charges, duty = (Decimal(str(value)) for value in (unit_price, freight, fees, duty_rate))
    with localcontext() as context:
        context.prec = max(context.prec, _decimal_budget(unit, shipping, charges, duty, rate_decimal))
        multiplier = rate_decimal * (Decimal(1) + duty)
        fixed = (shipping + charges) * multiplier
        exact_landed = unit * multiplier + fixed
        unit_base = float(unit * rate_decimal)
    landed = float(exact_landed)
    rate = float(rate_decimal)
    return {
        "unit_price_base": unit_base,
        "landed_cost": landed,
        "adder": landed - unit_base,
        "unit_multiplier": float(multiplier),
        "fixed_cost": float(fixed),
        # Used internally for comparisons; display floats must not be fed
        # back into an exact monetary cap after losing decimal digits.
        "exact": {"unit_multiplier": str(multiplier), "fixed_cost": str(fixed), "landed_cost": str(exact_landed)},
        "status": "complete",
        "missing": [],
        "fx_to_base": rate,
        "currency": currency,
        "base_currency": base_currency,
    }


def _decimal_budget(*values: Decimal) -> int:
    """Enough digits for the sums/products of the supplied decimal inputs."""
    return sum(len(value.as_tuple().digits) + abs(int(value.as_tuple().exponent)) for value in values)


def rival_unit_cap(own: dict[str, Any], other: dict[str, Any]) -> float:
    """A float upper bound that cannot round above the decimal invoice cap."""
    def exact(parts: dict[str, Any], key: str) -> Decimal:
        return Decimal(str((parts.get("exact") or {}).get(key, parts[key])))

    landed = exact(other, "landed_cost")
    fixed = exact(own, "fixed_cost")
    multiplier = exact(own, "unit_multiplier")
    with localcontext() as context:
        context.prec = max(context.prec, _decimal_budget(landed, fixed, multiplier))
        bound = (landed - fixed) / multiplier
    result = float(bound)
    if Decimal(str(result)) > bound:
        result = math.nextafter(result, -math.inf)
    return result


def ranking_basis(rows: list[dict[str, Any]]) -> str:
    """Landed cost when every row is complete. Otherwise unit price, and say so."""
    if rows and all(row.get("status") == "complete" and row.get("landed_cost") is not None for row in rows):
        return "landed_cost"
    return "unit_price"
