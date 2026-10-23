"""Shared numerical kernels. One implementation, explicitly parameterised.

Why this module exists
----------------------
``_betacf`` — the Lentz continued fraction for the incomplete beta — existed
twice, byte-for-byte identical except for its three convergence constants:

* ``src/engine/reserve.py`` used ``BETA_CF_ITERS`` / ``OPT_TOL`` / ``finfo.tiny``
  (80 iterations, 1e-12, 2.2e-308).
* ``src/legacy/bayesian_v2.py`` hardcoded 200 / 3e-14 / 1e-30.

Two copies of a numerical kernel that feeds price decisions is a maintenance
hazard: a fix applied to one silently leaves the other wrong, and a reviewer
cannot tell which is authoritative. The duplication was the defect.

The tolerance *difference* is not a defect. The legacy belief update renders a
Student-t interval to a person; the reserve CDF sits inside a golden-section
search whose convergence is already bounded by ``OPT_TOL``. So this module keeps
one implementation and makes the precision an explicit, named argument instead of
an accident of which file you are reading. ``src/engine/numerics.py`` is now the
only place the continued fraction is written, and
``tests/test_numerics.py`` pins that the two precisions agree to 1e-9 relative —
which is what "same kernel, different tolerance" is supposed to mean, and is
twelve orders of magnitude below any price difference a supplier could observe.

No number changes. The decision path keeps its tolerance and the legacy path
keeps its own; only the code is shared.
"""
from __future__ import annotations

import math

import numpy as np

from src.config.heuristics import (
    BETA_CF_ITERS,
    LEGACY_BETA_CF_EPS,
    LEGACY_BETA_CF_ITERS,
    LEGACY_BETA_CF_TINY,
    OPT_TOL,
)

# The float64 smallest normal. Keeps the continued fraction from dividing by zero
# without perturbing any representable result.
FLOAT_TINY = float(np.finfo(np.float64).tiny)


def log_beta(alpha: float, beta: float) -> float:
    """log B(α, β) via lgamma. The cancellation is why α+β must stay bounded."""
    return math.lgamma(alpha) + math.lgamma(beta) - math.lgamma(alpha + beta)


def betacf(
    alpha: float,
    beta: float,
    x: float,
    *,
    max_iter: int = BETA_CF_ITERS,
    eps: float = OPT_TOL,
    tiny: float = FLOAT_TINY,
) -> float:
    """Lentz continued fraction for the incomplete-beta tail series.

    The defaults are the decision path's constants. A caller that needs a
    different convergence budget passes it explicitly — see ``betacf_precise``.
    """
    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    height = d
    for m in range(1, int(max_iter) + 1):
        m2 = 2 * m
        aa = m * (beta - m) * x / ((qam + m2) * (alpha + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        height *= d * c
        aa = -(alpha + m) * (qab + m) * x / ((alpha + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        height *= delta
        if abs(delta - 1.0) < eps:
            break
    return height


def betacf_precise(alpha: float, beta: float, x: float) -> float:
    """The tighter precision: 200 iterations at 3e-14.

    A named function rather than a call-site argument so the legacy module reads
    the way it did before, and so the tighter budget has something to grep for.
    """
    return betacf(
        alpha,
        beta,
        x,
        max_iter=LEGACY_BETA_CF_ITERS,
        eps=LEGACY_BETA_CF_EPS,
        tiny=LEGACY_BETA_CF_TINY,
    )
