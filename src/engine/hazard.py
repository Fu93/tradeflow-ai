"""Walk-away hazard: the probability a supplier ends the negotiation.

This is not ``1 - p_deal``. ``p_deal`` is whether the remaining gap would
still close. The hazard is the chance the supplier stops talking after the
offer we are about to make.

The logistic coefficients live in ``src.config.heuristics``. They are
assumptions. No public bargaining file was fitted for this build, so every
result stays labelled prior-driven until real continue / walk-away events
are recorded. The eBay Best Offer replication (doi:10.7910/DVN/ZOWWO7) was
not downloaded and is not a source of these numbers.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from src.config.heuristics import (
    HAZARD_BLUFF,
    HAZARD_CREDIBLE_ALPHA,
    HAZARD_INTERCEPT,
    HAZARD_LOGIT_CLIP,
    HAZARD_MIN_EVENTS,
    HAZARD_N_ALT,
    HAZARD_QUOTE_GAP,
    HAZARD_RECIPROCITY,
    HAZARD_RESERVE_GAP,
    HAZARD_ROUND,
    HAZARD_SUPPLIER_TAU,
    HAZARD_U_HALF,
    HAZARD_U_POINTS,
)

PROVENANCE = {
    "status": "prior-driven",
    "fitted_dataset": None,
    "candidate_dataset_not_used": "doi:10.7910/DVN/ZOWWO7",
    "source": "src/config/heuristics.py",
    "note": (
        "Prior-driven estimate. Coefficients are assumptions, not a fit. "
        "No walk-away events have been observed. Do not read the number as "
        "a calibrated probability from factory negotiations."
    ),
}


def _as_xp(xp):
    return np if xp is None else xp


def sigmoid(xp, logit):
    """Numerically clipped logistic. ``xp`` is NumPy or CuPy."""
    xp = _as_xp(xp)
    clipped = xp.clip(logit, -HAZARD_LOGIT_CLIP, HAZARD_LOGIT_CLIP)
    return 1.0 / (1.0 + xp.exp(-clipped))


def reserve_gap(reserve, offer):
    """Signed gap max(reserve - offer, 0) / reserve. Zero when we meet the reserve."""
    floor = np.finfo(np.float64).tiny
    reserve = np.maximum(np.asarray(reserve, dtype=np.float64), floor)
    offer = np.asarray(offer, dtype=np.float64)
    return np.maximum(reserve - offer, 0.0) / reserve


def quote_gap(quote, offer):
    """Aggressiveness against their ask: max(quote - offer, 0) / quote."""
    floor = np.finfo(np.float64).tiny
    quote = np.maximum(np.asarray(quote, dtype=np.float64), floor)
    offer = np.asarray(offer, dtype=np.float64)
    return np.maximum(quote - offer, 0.0) / quote


def hazard_logit(
    xp,
    *,
    g_reserve,
    g_quote,
    bluff=0.0,
    reciprocity=0.0,
    round_index=0.0,
    n_alt=0.0,
    effect=0.0,
):
    """Linear predictor. Every slope on a gap is positive in the config."""
    xp = _as_xp(xp)
    return (
        HAZARD_INTERCEPT
        + HAZARD_RESERVE_GAP * g_reserve
        + HAZARD_QUOTE_GAP * g_quote
        + HAZARD_BLUFF * bluff * g_reserve
        + HAZARD_RECIPROCITY * reciprocity
        + HAZARD_ROUND * round_index
        + HAZARD_N_ALT * n_alt
        + effect
    )


def walk_away_hazard(
    xp,
    *,
    reserve,
    offer,
    quote=None,
    bluff=0.0,
    reciprocity=0.0,
    round_index=0.0,
    n_alt=0.0,
    effect=0.0,
):
    """Hazard of the next offer. Monotone in aggressiveness and in reserve.

    When ``quote`` is omitted the quote gap equals the reserve gap, which is
    the single-supplier opening search (the latent ask is the reserve).
    """
    xp = _as_xp(xp)
    g_reserve = reserve_gap(reserve, offer)
    if quote is None:
        g_quote = g_reserve
    else:
        g_quote = quote_gap(quote, offer)
    # NumPy gaps are ndarray. CuPy callers pass xp arrays into reserve_gap,
    # which uses NumPy. The single-supplier kernel builds the logit itself
    # so it can stay on the array backend. This helper is the NumPy path.
    logit = hazard_logit(
        np,
        g_reserve=g_reserve,
        g_quote=g_quote,
        bluff=bluff,
        reciprocity=reciprocity,
        round_index=round_index,
        n_alt=n_alt,
        effect=effect,
    )
    return sigmoid(np, logit)


def _logsumexp(values: np.ndarray) -> float:
    peak = float(np.max(values))
    return peak + float(np.log(np.sum(np.exp(values - peak))))


class HazardBelief:
    """Grid posterior for one supplier's random effect.

    The prior is normal and centred at zero. Each continue or walk-away
    multiplies the grid by the logistic likelihood. Until enough events
    exist, ``prior_driven`` stays true.
    """

    def __init__(self) -> None:
        self.u = np.linspace(-HAZARD_U_HALF, HAZARD_U_HALF, int(HAZARD_U_POINTS))
        tau = HAZARD_SUPPLIER_TAU
        log_prior = -(self.u * self.u) / (2 * tau * tau)
        self.log_w = log_prior - _logsumexp(log_prior)
        self.n_events = 0
        self.n_walks = 0

    def weights(self) -> np.ndarray:
        shifted = self.log_w - np.max(self.log_w)
        raw = np.exp(shifted)
        total = np.sum(raw)
        return raw / total

    def mean_effect(self) -> float:
        return float(np.sum(self.weights() * self.u))

    def quantile(self, probability: float) -> float:
        order = np.argsort(self.u)
        cumulative = np.cumsum(self.weights()[order])
        index = int(np.searchsorted(cumulative, probability, side="left"))
        index = min(max(index, 0), self.u.size - 1)
        return float(self.u[order][index])

    @property
    def prior_driven(self) -> bool:
        return self.n_events < int(HAZARD_MIN_EVENTS)

    def _logits(self, g_quote: float, g_reserve: float, reciprocity: float, round_index: float, n_alt: float) -> np.ndarray:
        return hazard_logit(
            np,
            g_reserve=g_reserve,
            g_quote=g_quote,
            reciprocity=reciprocity,
            round_index=round_index,
            n_alt=n_alt,
            effect=self.u,
        )

    def predictive(self, *, quote: float, offer: float, reserve: float, reciprocity: float = 0.0, round_index: float = 0.0, n_alt: float = 0.0) -> dict[str, float]:
        """Mean hazard and the central credible interval at this offer."""
        g_q = float(quote_gap(quote, offer))
        g_r = float(reserve_gap(reserve, offer))
        logits = self._logits(g_q, g_r, reciprocity, round_index, n_alt)
        hazards = sigmoid(np, logits)
        weights = self.weights()
        mean = float(np.sum(weights * hazards))
        # The logit is increasing in u, so hazard quantiles follow u quantiles.
        tail = HAZARD_CREDIBLE_ALPHA / 2
        lo = float(sigmoid(np, self._logits(g_q, g_r, reciprocity, round_index, n_alt)[np.argmin(np.abs(self.u - self.quantile(tail)))]))
        hi = float(sigmoid(np, self._logits(g_q, g_r, reciprocity, round_index, n_alt)[np.argmin(np.abs(self.u - self.quantile(1.0 - tail)))]))
        if hi < lo:
            lo, hi = hi, lo
        return {
            "mean": mean,
            "lower": lo,
            "upper": hi,
            "g_quote": g_q,
            "g_reserve": g_r,
            "prior_driven": self.prior_driven,
            "n_events": self.n_events,
            "n_walks": self.n_walks,
        }

    def update(self, *, walked: bool, quote: float, offer: float, reserve: float, reciprocity: float = 0.0, round_index: float = 0.0, n_alt: float = 0.0) -> None:
        """One observed outcome. Call this only with past events, before the next decision."""
        g_q = float(np.asarray(quote_gap(quote, offer)).reshape(-1)[0])
        g_r = float(np.asarray(reserve_gap(reserve, offer)).reshape(-1)[0])
        hazards = sigmoid(np, self._logits(g_q, g_r, reciprocity, round_index, n_alt))
        likelihood = hazards if walked else (1.0 - hazards)
        floor = np.finfo(np.float64).tiny
        self.log_w = self.log_w + np.log(np.maximum(likelihood, floor))
        self.log_w = self.log_w - _logsumexp(self.log_w)
        self.n_events += 1
        if walked:
            self.n_walks += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "mean_effect": self.mean_effect(),
            "n_events": self.n_events,
            "n_walks": self.n_walks,
            "prior_driven": self.prior_driven,
            "provenance": dict(PROVENANCE),
        }
