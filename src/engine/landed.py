"""Landed unit cost from prices the caller actually supplied.

Missing freight, duty, fees, or an FX rate needed for a currency change is
``no data``. A missing component is not treated as zero. When the quote
currency is omitted or matches the base currency, no FX rate is invented:
the figure is already in the base currency.
"""
from __future__ import annotations

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
    rate = 1.0 if same else float(fx_to_base)
    pre_duty = (float(unit_price) + float(freight) + float(fees)) * rate
    landed = pre_duty * (1.0 + float(duty_rate))
    unit_base = float(unit_price) * rate
    return {
        "unit_price_base": unit_base,
        "landed_cost": landed,
        "adder": landed - unit_base,
        "unit_multiplier": rate * (1.0 + float(duty_rate)),
        "fixed_cost": (float(freight) + float(fees)) * rate * (1.0 + float(duty_rate)),
        "status": "complete",
        "missing": [],
        "fx_to_base": rate,
        "currency": currency,
        "base_currency": base_currency,
    }


def ranking_basis(rows: list[dict[str, Any]]) -> str:
    """Landed cost when every row is complete. Otherwise unit price, and say so."""
    if rows and all(row.get("status") == "complete" and row.get("landed_cost") is not None for row in rows):
        return "landed_cost"
    return "unit_price"
