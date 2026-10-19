"""Walk-away hazard: the probability a supplier ends the negotiation.

This is not ``1 - p_deal``. ``p_deal`` is whether the remaining gap would
still close. The hazard is the chance the supplier stops talking after the
offer we are about to make.

The logistic coefficients live in ``src.config.heuristics``. They are
assumptions. Recorded continue / walk-away events update only the supplier
random effect. They do not fit the intercept, the slopes, or the spread.
The eBay Best Offer replication (doi:10.7910/DVN/ZOWWO7) was not downloaded
and is not a source of these numbers.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from src.config.heuristics import (
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
    HAZARD_U_STEP,
    HAZARD_U_TAILS,
)

PROVENANCE = {
    "status": "prior-driven",
    "fitted_dataset": None,
    "candidate_dataset_not_used": "doi:10.7910/DVN/ZOWWO7",
    "source": "src/config/heuristics.py",
    "note": (
        "Coefficients stay the labelled policy. Recorded outcomes update "
        "only the supplier effect. Do not read the number as a calibrated "
        "probability from factory negotiations."
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
    reciprocity=0.0,
    round_index=0.0,
    n_alt=0.0,
    effect=0.0,
    intercept=None,
    quote_gap_coef=None,
    reserve_gap_coef=None,
):
    """Linear predictor. Slopes are the labelled coefficients, not a fit.

    There is no bluff term. A bluff weight that never changed an accepted
    offer was removed.
    """
    xp = _as_xp(xp)
    b0 = HAZARD_INTERCEPT if intercept is None else intercept
    bq = HAZARD_QUOTE_GAP if quote_gap_coef is None else quote_gap_coef
    br = HAZARD_RESERVE_GAP if reserve_gap_coef is None else reserve_gap_coef
    return (
        b0
        + br * g_reserve
        + bq * g_quote
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
    reciprocity=0.0,
    round_index=0.0,
    n_alt=0.0,
    effect=0.0,
    intercept=None,
    quote_gap_coef=None,
    reserve_gap_coef=None,
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
        reciprocity=reciprocity,
        round_index=round_index,
        n_alt=n_alt,
        effect=effect,
        intercept=intercept,
        quote_gap_coef=quote_gap_coef,
        reserve_gap_coef=reserve_gap_coef,
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

    def __init__(
        self,
        *,
        intercept: float | None = None,
        tau: float | None = None,
        quote_gap_coef: float | None = None,
        reserve_gap_coef: float | None = None,
    ) -> None:
        self.intercept = float(HAZARD_INTERCEPT if intercept is None else intercept)
        self.tau = float(HAZARD_SUPPLIER_TAU if tau is None else tau)
        self.quote_gap_coef = float(HAZARD_QUOTE_GAP if quote_gap_coef is None else quote_gap_coef)
        self.reserve_gap_coef = float(HAZARD_RESERVE_GAP if reserve_gap_coef is None else reserve_gap_coef)
        half = max(float(HAZARD_U_HALF), float(HAZARD_U_TAILS) * max(self.tau, 0.0))
        step = float(HAZARD_U_STEP)
        count = max(int(HAZARD_U_POINTS), int(np.ceil((2 * half) / step)) + 1)
        self.u = np.linspace(-half, half, count)
        tau = max(self.tau, float(np.finfo(np.float64).tiny))
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
        """Inverse CDF with linear interpolation between grid masses."""
        order = np.argsort(self.u)
        grid = self.u[order]
        cumulative = np.cumsum(self.weights()[order])
        total = float(cumulative[-1]) if cumulative.size else 1.0
        if total <= 0.0:
            return 0.0
        cumulative = cumulative / total
        probability = float(min(max(probability, 0.0), 1.0))
        if probability <= float(cumulative[0]):
            return float(grid[0])
        index = int(np.searchsorted(cumulative, probability, side="left"))
        index = min(max(index, 1), grid.size - 1)
        c0 = float(cumulative[index - 1])
        c1 = float(cumulative[index])
        u0 = float(grid[index - 1])
        u1 = float(grid[index])
        if c1 <= c0:
            return u1
        share = (probability - c0) / (c1 - c0)
        return u0 + share * (u1 - u0)

    @property
    def prior_driven(self) -> bool:
        """Coefficients are the labelled policy. Outcomes do not fit them."""
        return True

    def _logits(self, g_quote: float, g_reserve: float, reciprocity: float, round_index: float, n_alt: float) -> np.ndarray:
        return hazard_logit(
            np,
            g_reserve=g_reserve,
            g_quote=g_quote,
            reciprocity=reciprocity,
            round_index=round_index,
            n_alt=n_alt,
            effect=self.u,
            intercept=self.intercept,
            quote_gap_coef=self.quote_gap_coef,
            reserve_gap_coef=self.reserve_gap_coef,
        )

    def _logit_at(self, effect: float, g_quote: float, g_reserve: float, reciprocity: float, round_index: float, n_alt: float) -> float:
        return float(hazard_logit(
            np,
            g_reserve=g_reserve,
            g_quote=g_quote,
            reciprocity=reciprocity,
            round_index=round_index,
            n_alt=n_alt,
            effect=effect,
            intercept=self.intercept,
            quote_gap_coef=self.quote_gap_coef,
            reserve_gap_coef=self.reserve_gap_coef,
        ))

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
        lo = float(sigmoid(np, self._logit_at(self.quantile(tail), g_q, g_r, reciprocity, round_index, n_alt)))
        hi = float(sigmoid(np, self._logit_at(self.quantile(1.0 - tail), g_q, g_r, reciprocity, round_index, n_alt)))
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
            "prior_driven": True,
            "coefficients": "labelled policy",
            "effect_events": self.n_events,
            "min_events": int(HAZARD_MIN_EVENTS),
            "intercept": self.intercept,
            "tau": self.tau,
            "provenance": dict(PROVENANCE),
        }


def baseline_rates(intercept: float, tau: float) -> dict[str, float]:
    """Labelled sensitivity of the walk-away rate at a zero gap."""
    return {
        "baseline": float(sigmoid(np, intercept)),
        "low": float(sigmoid(np, intercept - tau)),
        "high": float(sigmoid(np, intercept + tau)),
    }
