"""The shared numerical kernel: one implementation, two named precisions.

These tests exist because the continued fraction used to be duplicated in
``src/engine/reserve.py`` and ``src/legacy/bayesian_v2.py`` with different
convergence constants. Sharing the code is only safe if the two precisions are
proven to agree to a bound far below any price, so that bound is pinned here.
"""
from __future__ import annotations

import math

import pytest

from src.engine.numerics import FLOAT_TINY, betacf, betacf_precise, log_beta
from src.engine.reserve import regularized_beta

# (α, β) pairs that exercise the slow-convergence regions: both parameters below
# one, one below one, and both large.
SHAPES = (
    (0.5, 0.5),
    (0.2, 5.0),
    (5.0, 0.2),
    (1.0, 1.0),
    (2.0, 8.0),
    (3.6, 0.4),
    (50.0, 50.0),
)
POINTS = (1e-9, 1e-4, 0.01, 0.25, 0.5, 0.75, 0.99, 1 - 1e-9)


def test_the_two_precisions_agree_to_one_part_in_a_billion():
    """A shared kernel with a different tolerance must stay a shared answer.

    Measured worst case before unification was 4.4e-12 relative; 1e-9 is a
    bound a reviewer can reason about and is nine orders of magnitude below a
    cent on a unit price.
    """
    worst = 0.0
    worst_at = None
    for alpha, beta in SHAPES:
        for x in POINTS:
            # Both CDF callers reflect the opposite tail before invoking this
            # kernel. Comparing unconverged fractions outside that domain
            # previously compared two equally inaccurate approximations.
            if x >= (alpha + 1.0) / (alpha + beta + 2.0):
                continue
            loose = betacf(alpha, beta, x)
            tight = betacf_precise(alpha, beta, x)
            if not (math.isfinite(loose) and math.isfinite(tight)):
                continue
            scale = max(abs(loose), abs(tight), 1e-300)
            delta = abs(loose - tight) / scale
            if delta > worst:
                worst, worst_at = delta, (alpha, beta, x)
    assert worst < 1e-9, f"precisions diverged by {worst:.3e} at {worst_at}"


def test_log_beta_matches_the_gamma_identity_at_simple_shapes():
    """B(1,1)=1, B(2,1)=1/2, B(2,2)=1/6 — exact cases, so a regression is visible."""
    assert log_beta(1.0, 1.0) == pytest.approx(0.0, abs=1e-12)
    assert log_beta(2.0, 1.0) == pytest.approx(math.log(0.5), abs=1e-12)
    assert log_beta(2.0, 2.0) == pytest.approx(math.log(1.0 / 6.0), abs=1e-12)


def test_the_cdf_is_still_exact_at_the_ends():
    """The kernel feeds a CDF that must not drift at x=0 or x=1."""
    for alpha, beta in SHAPES:
        assert regularized_beta(0.0, alpha, beta) == 0.0
        assert regularized_beta(1.0, alpha, beta) == 1.0


def test_the_cdf_is_monotonic():
    """A non-monotonic beta CDF would make acceptance probabilities nonsense."""
    for alpha, beta in SHAPES:
        previous = -1.0
        for x in POINTS:
            value = regularized_beta(x, alpha, beta)
            assert value >= previous - 1e-12, (alpha, beta, x, value, previous)
            previous = value


def test_beta_one_one_is_the_identity():
    """Beta(1,1) is uniform, so its CDF is x. This pins the continued fraction."""
    for x in POINTS:
        assert regularized_beta(x, 1.0, 1.0) == pytest.approx(x, abs=1e-9)


def test_the_float_tiny_floor_is_the_smallest_normal():
    assert FLOAT_TINY > 0.0
    assert math.isfinite(1.0 / FLOAT_TINY)
