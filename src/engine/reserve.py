"""Per-supplier reserve: reserve = reference_ask × D, with D in [0, 1].

Each supplier has their own Beta prior. Another supplier's ask does not
enter it. A later ask, an acceptance, and a closed-deal price are upper
bounds on D. A rejection is a lower bound on D. The posterior on an
interval [L, U] is the Beta prior truncated to that interval, in closed
form. The level of the prior is labelled policy until this supplier has
enough of their own responses. The eBay auto-decline ratio is not the prior.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from src.config.heuristics import (
    BETA_CF_ITERS,
    D_PRIOR_MEAN,
    D_PRIOR_STRENGTH,
    HAZARD_MIN_EVENTS,
    ONE_HALF,
    OPT_TOL,
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
    reference_ask: float | None = None

    @classmethod
    def policy(cls) -> "ReserveState":
        alpha, beta = policy_alpha_beta()
        return cls(alpha=alpha, beta=beta, upper=1.0, n_responses=0)

    @classmethod
    def from_moments(cls, mean: float, std: float | None = None) -> "ReserveState":
        """User policy. Mean is a fraction of the reference ask. Spread is the standard deviation of D."""
        centre = min(max(float(mean), _TINY), 1.0 - _TINY)
        if std is None or float(std) <= 0.0:
            strength = float(D_PRIOR_STRENGTH)
        else:
            variance = float(std) * float(std)
            strength = centre * (1.0 - centre) / variance - 1.0
            if strength <= _TINY:
                strength = float(D_PRIOR_STRENGTH)
        return cls(alpha=centre * strength, beta=(1.0 - centre) * strength, upper=1.0, n_responses=0)

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
                "A rejection is a lower bound on D. "
                f"{self.n_responses} responses are on file; "
                f"{int(HAZARD_MIN_EVENTS)} would retire the policy label."
            )
        return (
            "Reserve prior is labelled policy: D is Beta with the displayed "
            "mean and strength. No response from this supplier has updated it. "
            "Another supplier's ask is not used."
        )

    def _spawn(self, **changes: object) -> "ReserveState":
        payload = {
            "alpha": self.alpha,
            "beta": self.beta,
            "upper": self.upper,
            "reject_ratios": self.reject_ratios,
            "n_responses": self.n_responses,
            "reference_ask": self.reference_ask,
        }
        payload.update(changes)
        return ReserveState(**payload)

    def with_strength(self, factor: float) -> "ReserveState":
        alpha, beta = scale_strength(self.alpha, self.beta, factor)
        return self._spawn(alpha=alpha, beta=beta)

    def with_reference(self, ask: float) -> "ReserveState":
        """The ask D is a fraction of. Later asks do not replace it."""
        return self._spawn(reference_ask=float(ask))

    def tighten(self, ratio: float) -> "ReserveState":
        """Record an upper bound. A ratio above 1 does not loosen a tighter bound."""
        bound = min(float(self.upper), max(float(ratio), _TINY))
        bound = min(bound, 1.0)
        return self._spawn(upper=bound, n_responses=self.n_responses + 1)

    def record(self) -> "ReserveState":
        """Count a response that does not move D. Continue is this case."""
        return self._spawn(n_responses=self.n_responses + 1)

    def add_rejection(self, ratio: float) -> "ReserveState":
        """A rejected offer is a lower bound: D sits at or above that fraction of the reference ask."""
        ratios = self.reject_ratios + (min(max(float(ratio), _TINY), 1.0),)
        return self._spawn(reject_ratios=ratios, n_responses=self.n_responses + 1)

    def bounds(self) -> tuple[float, float]:
        """Lower bound from rejections, upper bound from later asks and closed deals."""
        lower = 0.0
        if self.reject_ratios:
            lower = max(float(ratio) for ratio in self.reject_ratios)
        upper = min(max(float(self.upper), 0.0), 1.0)
        return lower, upper

    def consistent(self) -> bool:
        """False when a rejection sits at or above the tightest upper bound."""
        lower, upper = self.bounds()
        return lower < upper

    def acceptance(self, x: float) -> float:
        """P(accept an offer at fraction x of the reference ask)."""
        return self.cdf(x)

    def cdf(self, x: float) -> float:
        """P(D ≤ x | L ≤ D ≤ U) from the regularized incomplete beta."""
        lower, upper, mass, ok = self._interval(self.alpha, self.beta)
        if not ok:
            return 0.0
        point = float(x)
        if point < lower:
            return 0.0
        if point >= upper:
            return 1.0
        passed = regularized_beta(point, self.alpha, self.beta) - regularized_beta(lower, self.alpha, self.beta)
        return min(max(passed / mass, 0.0), 1.0)

    def mean(self) -> float:
        lower, upper, mass, ok = self._interval(self.alpha, self.beta)
        if not ok:
            return float("nan")
        moment = regularized_beta(upper, self.alpha + 1.0, self.beta) - regularized_beta(
            lower, self.alpha + 1.0, self.beta
        )
        return (moment / mass) * beta_mean(self.alpha, self.beta)

    def std(self) -> float:
        lower, upper, mass, ok = self._interval(self.alpha, self.beta)
        if not ok:
            return float("nan")
        if lower <= _TINY and upper >= 1.0 - float(OPT_TOL):
            return math.sqrt(max(beta_variance(self.alpha, self.beta), 0.0))
        total = float(self.alpha) + float(self.beta)
        factor = (float(self.alpha) * (float(self.alpha) + 1.0)) / (total * (total + 1.0))
        second_mass = regularized_beta(upper, self.alpha + 2, self.beta) - regularized_beta(
            lower, self.alpha + 2, self.beta
        )
        second = factor * second_mass / mass
        variance = second - self.mean() ** 2
        return math.sqrt(max(variance, 0.0))

    def analytic_cdf(self, x: float) -> float:
        """Same truncated CDF. Kept so older callers do not grow a second formula."""
        return self.cdf(x)

    def _interval(self, alpha: float, beta: float) -> tuple[float, float, float, bool]:
        lower, upper = self.bounds()
        if not (lower < upper):
            return lower, upper, 0.0, False
        mass = regularized_beta(upper, alpha, beta) - regularized_beta(lower, alpha, beta)
        if mass <= _TINY:
            return lower, upper, 0.0, False
        return lower, upper, mass, True


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
