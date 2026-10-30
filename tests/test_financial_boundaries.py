"""Independent invoice arithmetic at adversarial minor-unit boundaries."""

from decimal import Decimal, ROUND_DOWN
import random

import pytest

from src.desk.input_checks import require_landed_total, require_price_band
from src.desk.money import quantise_down
from src.desk.recompute import _landed_consistent_rivals
from src.desk.store import DeskRejected
from src.engine.landed import landed_unit_cost


def test_cent_boundary_caps_match_independent_decimal_invoices():
    rng = random.Random(301026)
    for _ in range(1000):
        ask = Decimal(rng.randrange(100, 10000)) / 100
        cent = Decimal(rng.randrange(1, int(ask * 100) + 1)) / 100
        freight = Decimal(rng.randrange(0, 10000)) / 1000
        fees = Decimal(rng.randrange(0, 1000)) / 1000
        duty = Decimal(rng.randrange(0, 1000)) / 1000
        rival_input = float((cent + freight + fees) * (1 + duty))
        own = landed_unit_cost(unit_price=float(ask), freight=float(freight), fees=float(fees), duty_rate=float(duty))
        rival = landed_unit_cost(unit_price=rival_input, freight=0, fees=0, duty_rate=0)
        cap = _landed_consistent_rivals(
            "own", {"own": float(ask), "rival": rival_input}, {"own": own, "rival": rival}, "landed_cost"
        )[0]
        actual = Decimal(str(quantise_down(cap)))
        exact_invoice = Decimal(str(rival_input))
        expected = (exact_invoice / (1 + duty) - freight - fees).quantize(Decimal(".01"), rounding=ROUND_DOWN)
        assert actual == expected
        assert (actual + freight + fees) * (1 + duty) <= exact_invoice


@pytest.mark.parametrize("amount", [1e16, 1e26, 1e30])
def test_prices_that_cannot_preserve_cents_are_rejected_at_intake(amount):
    with pytest.raises(DeskRejected):
        require_price_band(amount, amount * 2)


def test_derived_invoice_that_loses_cent_precision_is_rejected():
    with pytest.raises(DeskRejected):
        require_landed_total(20, 1e30, 0, 0)
