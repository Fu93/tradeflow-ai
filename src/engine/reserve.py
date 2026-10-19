"""Per-supplier reserve: reserve = ask × D, with D in [0, 1].

Each supplier has their own Beta prior. Another supplier's ask does not
enter it. A later ask, an acceptance, and a closed-deal price are upper
bounds on D. A rejection is a soft likelihood, not a hard cut. The level of
the prior is labelled policy until this supplier has enough of their own
responses. The eBay auto-decline ratio is not the prior.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from src.config.heuristics import (
    BETA_CF_ITERS,
    D_PRIOR_MEAN,
    D_PRIOR_STRENGTH,
    HAZARD_LOGIT_CLIP,
    HAZARD_MIN_EVENTS,
    ONE_HALF,
    OPT_TOL,
    QUAD_NODES,
    REJECT_SOFTNESS,
)

_TINY = float(np.finfo(np.float64).tiny)


def policy_alpha_beta() -> tuple[float, float]:
    """Labelled prior. Mean and strength are the user's policy knobs."""
    strength = float(D_PRIOR_STRENGTH)
    mean = min(max(float(D_PRIOR_MEAN), _TINY), 1.0 - _TINY)
    return mean * strength, (1.0 - mean) * strength


def scale_strength(alpha: float, beta: float, factor: float) -> tuple[float, float]:
    """Same mean, different strength. Half is ONE_HALF. Double is 1/ONE_HALF."""
    scaled = max(float(factor), _TINY)
    return max(float(alpha) * scaled, _TINY), max(float(beta) * scaled, _TINY)


def _log_beta(alpha: float, beta: float) -> float:
    return math.lgamma(alpha) + math.lgamma(beta) - math.lgamma(alpha + beta)


def _betacf(alpha: float, beta: float, x: float) -> float:
    """Lentz continued fraction for the incomplete-beta tail series."""
    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    height = d
    for m in range(1, int(BETA_CF_ITERS) + 1):
        m2 = 2 * m
        aa = m * (beta - m) * x / ((qam + m2) * (alpha + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        height *= d * c
        aa = -(alpha + m) * (qab + m) * x / ((alpha + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        height *= delta
        if abs(delta - 1.0) < float(OPT_TOL):
            break
    return height


def regularized_beta(x: float, alpha: float, beta: float) -> float:
    """Beta CDF. Beta(α, 1) is x^α. The symmetry identity covers the other tail."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if abs(beta - 1.0) <= float(OPT_TOL) and alpha > 0.0:
        return float(x ** alpha)
    if abs(alpha - 1.0) <= float(OPT_TOL) and beta > 0.0:
        return 1.0 - float((1.0 - x) ** beta)
    log_front = (
        alpha * math.log(x)
        + beta * math.log(1.0 - x)
        - _log_beta(alpha, beta)
    )
    front = math.exp(log_front)
    if x < (alpha + 1.0) / (alpha + beta + 2):
        return front * _betacf(alpha, beta, x) / alpha
    return 1.0 - front * _betacf(beta, alpha, 1.0 - x) / beta


def beta_mean(alpha: float, beta: float) -> float:
    return float(alpha) / (float(alpha) + float(beta))


def beta_variance(alpha: float, beta: float) -> float:
    total = float(alpha) + float(beta)
    return (float(alpha) * float(beta)) / (total * total * (total + 1.0))


@dataclass
class ReserveState:
    """One supplier. ``upper`` is an upper bound on D, never a point sample."""

    alpha: float
    beta: float
    upper: float = 1.0
    reject_ratios: tuple[float, ...] = field(default_factory=tuple)
    n_responses: int = 0

    @classmethod
    def policy(cls) -> "ReserveState":
        alpha, beta = policy_alpha_beta()
        return cls(alpha=alpha, beta=beta, upper=1.0, n_responses=0)

    @property
    def source(self) -> str:
        if self.n_responses >= int(HAZARD_MIN_EVENTS):
            return "supplier_responses"
        return "labelled_policy"

    def note(self) -> str:
        if self.n_responses >= int(HAZARD_MIN_EVENTS):
            return (
                "Reserve is this supplier's Beta posterior. "
                "Closed prices and later asks entered as upper bounds on D."
            )
        if self.upper < 1.0 or self.reject_ratios:
            return (
                "Reserve prior is labelled policy. This supplier's later asks, "
                "acceptances, and closed prices tighten D from above. "
                "A rejection is a soft likelihood. "
                f"{self.n_responses} responses are on file; "
                f"{int(HAZARD_MIN_EVENTS)} would retire the policy label."
            )
        return (
            "Reserve prior is labelled policy: D is Beta with the displayed "
            "mean and strength. No response from this supplier has updated it. "
            "Another supplier's ask is not used."
        )

    def with_strength(self, factor: float) -> "ReserveState":
        alpha, beta = scale_strength(self.alpha, self.beta, factor)
        return ReserveState(
            alpha=alpha,
            beta=beta,
            upper=self.upper,
            reject_ratios=self.reject_ratios,
            n_responses=self.n_responses,
        )

    def tighten(self, ratio: float) -> "ReserveState":
        """Record an upper bound. A ratio above 1 does not loosen a tighter bound."""
        bound = min(float(self.upper), max(float(ratio), _TINY))
        bound = min(bound, 1.0)
        return ReserveState(
            alpha=self.alpha,
            beta=self.beta,
            upper=bound,
            reject_ratios=self.reject_ratios,
            n_responses=self.n_responses + 1,
        )

    def record(self) -> "ReserveState":
        """Count a response that does not move D. Continue is this case."""
        return ReserveState(
            alpha=self.alpha,
            beta=self.beta,
            upper=self.upper,
            reject_ratios=self.reject_ratios,
            n_responses=self.n_responses + 1,
        )

    def add_rejection(self, ratio: float) -> "ReserveState":
        ratios = self.reject_ratios + (min(max(float(ratio), _TINY), 1.0),)
        return ReserveState(
            alpha=self.alpha,
            beta=self.beta,
            upper=self.upper,
            reject_ratios=ratios,
            n_responses=self.n_responses + 1,
        )

    def acceptance(self, x: float) -> float:
        """P(accept an offer at fraction x of the first ask)."""
        if self.reject_ratios:
            return self.cdf(x)
        return self.analytic_cdf(x)

    def cdf(self, x: float) -> float:
        """P(D ≤ x) under the censored, reweighted prior."""
        grid, weights = self._quadrature()
        if not grid:
            return 0.0
        total = sum(weights)
        if total <= 0.0:
            return 0.0
        passed = sum(weight for point, weight in zip(grid, weights) if point <= float(x) + float(OPT_TOL))
        return min(max(passed / total, 0.0), 1.0)

    def mean(self) -> float:
        if not self.reject_ratios:
            cap = regularized_beta(self.upper, self.alpha, self.beta)
            if cap <= _TINY:
                return min(max(self.upper, 0.0), 1.0)
            moment = regularized_beta(self.upper, self.alpha + 1.0, self.beta)
            return (moment / cap) * beta_mean(self.alpha, self.beta)
        grid, weights = self._quadrature()
        total = sum(weights)
        if total <= 0.0:
            return beta_mean(self.alpha, self.beta)
        return sum(point * weight for point, weight in zip(grid, weights)) / total

    def std(self) -> float:
        if not self.reject_ratios and self.upper >= 1.0 - float(OPT_TOL):
            return math.sqrt(max(beta_variance(self.alpha, self.beta), 0.0))
        grid, weights = self._quadrature()
        total = sum(weights)
        if total <= 0.0:
            return math.sqrt(max(beta_variance(self.alpha, self.beta), 0.0))
        center = self.mean()
        second = sum((point - center) ** 2 * weight for point, weight in zip(grid, weights)) / total
        return math.sqrt(max(second, 0.0))

    def analytic_cdf(self, x: float) -> float:
        """Unweighted Beta CDF, renormalised onto [0, upper]. Used when nothing was rejected."""
        if self.reject_ratios:
            return self.cdf(x)
        cap = regularized_beta(self.upper, self.alpha, self.beta)
        if cap <= _TINY:
            return 1.0 if x >= self.upper else 0.0
        if x >= self.upper:
            return 1.0
        return regularized_beta(max(x, 0.0), self.alpha, self.beta) / cap

    def _quadrature(self) -> tuple[list[float], list[float]]:
        cap = min(max(float(self.upper), _TINY), 1.0)
        count = int(QUAD_NODES)
        if count < 2:
            count = 2
        step = cap / (count - 1)
        grid = [step * index for index in range(count)]
        grid[0] = min(max(grid[0], _TINY), cap)
        grid[-1] = cap
        logs = []
        for point in grid:
            if point <= 0.0 or point >= 1.0:
                logs.append(None)
                continue
            log_density = (
                (self.alpha - 1.0) * math.log(point)
                + (self.beta - 1.0) * math.log(max(1.0 - point, _TINY))
                - _log_beta(self.alpha, self.beta)
            )
            for ratio in self.reject_ratios:
                # Soft evidence that D sits above the rejected offer. Mass below it stays.
                gap = float(REJECT_SOFTNESS) * (point - float(ratio))
                gap = max(min(gap, float(HAZARD_LOGIT_CLIP)), -float(HAZARD_LOGIT_CLIP))
                soft = 1.0 / (1.0 + math.exp(-gap))
                log_density += math.log(max(soft, _TINY))
            logs.append(log_density)
        finite = [item for item in logs if item is not None]
        peak = max(finite) if finite else 0.0
        raw = []
        for item in logs:
            if item is None:
                raw.append(0.0)
            else:
                raw.append(math.exp(item - peak))
        weights = []
        for index, density in enumerate(raw):
            if index == 0 or index == len(raw) - 1:
                weights.append(density * step * float(ONE_HALF))
            else:
                weights.append(density * step)
        return grid, weights


def uniform_offer(outside: float, ask: float, low: float) -> float:
    """Closed form when D is uniform on [low, 1]. b* = (W + low·ask) / 2."""
    raw = (float(outside) + float(low) * float(ask)) * float(ONE_HALF)
    floor = float(low) * float(ask)
    cap = min(float(ask), float(outside))
    return min(max(raw, floor), cap)


def beta_alpha_one_offer(outside: float, alpha: float) -> float:
    """Closed form when D ~ Beta(α, 1) and the outside option is the only cap.

    The first-order condition W = b + q·G/g with G(x) = x^α gives b* = α W / (1+α).
    The caller clips to the ask and to the hazard floor.
    """
    return float(alpha) * float(outside) / (float(alpha) + 1.0)
