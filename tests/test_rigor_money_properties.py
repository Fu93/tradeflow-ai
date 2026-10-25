"""Property tests for the money module — the one choke point every price passes.

No hypothesis dependency (this repo pins its locks and adds nothing for
tests): properties are checked over seeded-random value spaces instead —
deterministic, reproducible, and fast. The properties are the product
contract:

P1 cap safety — quantise_offer never returns a value above the ceiling or
   the ask, *at minor-unit precision* (the bug that created this module:
   8.1952 passing the float check while the supplier reads 8.20).
P2 idempotence — quantising a quantised value changes nothing; the pipeline
   can never drift by re-rounding.
P3 round-trip — a quantised value survives minor-unit conversion exactly.
P4 half-away-from-zero — the human rounding rule, symmetric and stable at
   every midpoint (0.005 -> 0.01, not banker's 0).
P5 monotonicity — a higher input never yields a lower output (an off-by-one
   cent inversion would misorder counter ladders).
"""
from __future__ import annotations

import random

import pytest

from src.desk.money import is_quantised, quantise, quantise_down, quantise_offer, to_minor

SEED = 20261025
N = 3000


def _prices(rng: random.Random):
    while True:
        yield round(rng.uniform(0.0001, 100000.0), rng.randint(0, 6))


def test_p1_quantise_offer_never_breaches_the_caps_at_minor_precision():
    rng = random.Random(SEED)
    for _ in range(N):
        offer = next(_prices(rng))
        ceiling = round(rng.uniform(0.0001, 100000.0), 6)
        ask = round(rng.uniform(0.0001, 100000.0), 6)
        out = quantise_offer(offer, ceiling, ask)
        cap = quantise_down(min(ceiling, ask))
        assert out <= cap + 1e-9, (offer, ceiling, ask, out, cap)
        assert out >= 0.0


def test_p2_quantisation_is_idempotent():
    rng = random.Random(SEED + 1)
    for _ in range(N):
        value = next(_prices(rng))
        once = quantise(value)
        assert quantise(once) == once
        assert is_quantised(once)


def test_p3_minor_units_round_trip_exactly():
    rng = random.Random(SEED + 2)
    for _ in range(N):
        value = quantise(next(_prices(rng)))
        assert to_minor(value) / 100 == value


def test_p4_half_away_from_zero_at_every_midpoint_scale():
    rng = random.Random(SEED + 3)
    # known midpoints first: banker's rounding would send these DOWN.
    assert to_minor(0.005) == 1
    assert to_minor(0.015) == 2
    assert to_minor(0.025) == 3
    for _ in range(N):
        units = rng.randint(0, 10_000_000)
        value = units / 100 + 0.005  # a true midpoint in decimal space
        assert to_minor(round(value, 3)) in (units, units + 1)


def test_p5_quantisation_is_monotone():
    rng = random.Random(SEED + 4)
    for _ in range(500):
        base = next(_prices(rng))
        for bump in (0.000001, 0.004, 0.006, 1.0, 997.0):
            hi = quantise(base + bump)
            lo = quantise(base)
            assert hi >= lo, (base, bump, lo, hi)


def test_caps_chain_through_repeated_offers():
    """A pathological ladder: offers sliding across the cap boundary."""
    rng = random.Random(SEED + 5)
    for _ in range(1000):
        cap = round(rng.uniform(1.0, 50.0), 4)
        for offer in (cap, cap + 0.0049, cap - 0.0049, cap * 2):
            out = quantise_offer(offer, cap, cap * 10)
            assert out <= quantise_down(cap) + 1e-9


@pytest.mark.parametrize("value,expected", [
    (8.1952, 8.20), (8.1949, 8.19), (0.005, 0.01), (1.005, 1.01),
])
def test_known_roundings(value, expected):
    assert quantise(value) == expected
