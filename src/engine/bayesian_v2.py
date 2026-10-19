"""Reserve update with a heavy-tailed observation.

The observation model is a Student-t around the prior mean plus the user's
markup. A quote far from that centre is down-weighted by the usual
Student-t weight ``(ν+1)/(ν+z²)`` and still moves the mean. The reported
interval and P(deal) use that same Student-t, not a normal interval around
a mixture that has already discarded the quote.

Markup and reserve are not separately identified from quotes alone. The
markup is zero unless the user sets it or a closed deal supplies a price.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from src.config.heuristics import (
    BAYES_OBS_NOISE,
    CREDIBLE_MASS,
    MAD_NORMAL_CONSISTENCY,
    ONE_HALF,
    STUDENT_T_BRACKET,
    STUDENT_T_DF,
)


class BayesianBeliefState(BaseModel):
    posterior_mean: float = Field(..., description="Posterior mean of the reserve multiple")
    posterior_std: float = Field(..., description="Posterior scale of that reserve")
    clean_responsibility: float = Field(..., description="Student-t influence of this quote, in (0, 1]")
    credible_interval_95: Tuple[float, float] = Field(..., description="Central 95% Student-t interval")
    info_gain_bits: float = Field(..., description="Gaussian KL approximation of the update, in bits")
    deal_probability: Optional[float] = Field(
        default=None,
        description="Student-t probability that the reserve is at or under the offer",
    )
    interval_model: str = "student_t"
    robust_weight: float = 1.0
    markup_identified: bool = False


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """Lentz continued fraction for the incomplete beta."""
    max_iter = 200
    eps = 3e-12
    am = 1.0
    a0 = 1.0
    b0 = 1.0
    a1 = 1.0
    b1 = 1.0 - (a + b) * x / (a + 1.0)
    if abs(b1) < 1e-30:
        b1 = 1e-30
    fraction = a1 / b1
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((a + m2 - 1.0) * (a + m2))
        a0 = (a1 + aa * a0)
        b0 = (b1 + aa * b0)
        if abs(b0) < 1e-30:
            b0 = 1e-30
        aa = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1.0))
        a1 = a0 + aa * a1
        b1 = b0 + aa * b1
        if abs(b1) < 1e-30:
            b1 = 1e-30
        a0 /= b1
        b0 /= b1
        a1 /= b1
        b1 = 1.0
        delta = a1 / a0 if a0 != 0.0 else 1.0
        fraction *= delta
        if abs(delta - 1.0) < eps:
            break
        am = a1
    return fraction


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = a * math.log(x) + b * math.log(1.0 - x) - _log_beta(a, b)
    front = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """CDF of a standard Student-t."""
    if df <= 0.0:
        return 1.0 if t >= 0.0 else 0.0
    x = df / (df + t * t)
    ib = regularized_incomplete_beta(df / 2, ONE_HALF, x)
    if t >= 0.0:
        return 1.0 - ONE_HALF * ib
    return ONE_HALF * ib


def student_t_ppf(probability: float, df: float) -> float:
    """Quantile of a standard Student-t by bisection."""
    if probability <= 0.0:
        return -1e6
    if probability >= 1.0:
        return 1e6
    lo, hi = -STUDENT_T_BRACKET, STUDENT_T_BRACKET
    for _ in range(80):
        mid = (lo + hi) / 2
        if student_t_cdf(mid, df) < probability:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _student_interval(mean: float, scale: float, df: float) -> tuple[float, float]:
    z = student_t_ppf(1.0 - (1.0 - float(CREDIBLE_MASS)) / 2, df)
    width = abs(scale) * z
    return max(0.0, mean - width), mean + width


def _deal_probability(offer: float, mean: float, scale: float, df: float) -> float:
    if scale <= 1e-8:
        return 1.0 if offer >= mean else 0.0
    return student_t_cdf((offer - mean) / scale, df)


def robust_location(values: Sequence[float], df: float = STUDENT_T_DF) -> tuple[float, float]:
    """IRLS location and scale of the user's own numbers. Markup is not split out."""
    clean = [float(item) for item in values if item is not None]
    if not clean:
        raise ValueError("robust_location needs at least one number")
    loc = float(sorted(clean)[len(clean) // 2])
    deviations = [abs(item - loc) for item in clean]
    mad = float(sorted(deviations)[len(deviations) // 2])
    scale = max(mad * float(MAD_NORMAL_CONSISTENCY), float(BAYES_OBS_NOISE))
    nu = float(df)
    for _ in range(20):
        weights = []
        for item in clean:
            z = (item - loc) / scale
            weights.append((nu + 1.0) / (nu + z * z))
        total = sum(weights)
        loc = sum(weight * item for weight, item in zip(weights, clean)) / total
        variance = sum(weight * (item - loc) ** 2 for weight, item in zip(weights, clean))
        scale = math.sqrt(max(variance / max(len(clean), 1), 1e-12))
        scale = max(scale, float(BAYES_OBS_NOISE))
    return loc, scale


class RobustBayesianInferenceEngine:
    """Student-t observation, one robust Kalman step."""

    def __init__(
        self,
        obs_noise_std: float = BAYES_OBS_NOISE,
        degrees_of_freedom: float = STUDENT_T_DF,
    ):
        self.obs_noise_std = float(obs_noise_std)
        self.degrees_of_freedom = float(degrees_of_freedom)

    @classmethod
    def normal_cdf(cls, x: float, mean: float, std: float) -> float:
        """Normal CDF kept for callers that still ask for the Gaussian tail."""
        if std <= 1e-7:
            return 1.0 if x >= mean else 0.0
        z = (x - mean) / (std * math.sqrt(2))
        return (1.0 + math.erf(z)) / 2

    def update(
        self,
        prior_mean: float,
        prior_std: float,
        observed_offer: Optional[float] = None,
        estimated_markup: float = 0.0,
        our_next_offer: Optional[float] = None,
        markup_identified: bool = False,
    ) -> BayesianBeliefState:
        """One robust step. ``estimated_markup`` defaults to zero.

        A caller who passes the old labelled markup must say so. The engine
        does not assume 8 percent.
        """
        df = self.degrees_of_freedom
        if observed_offer is None:
            low, high = _student_interval(prior_mean, prior_std, df)
            deal_prob = (
                _deal_probability(our_next_offer, prior_mean, prior_std, df)
                if our_next_offer is not None else None
            )
            return BayesianBeliefState(
                posterior_mean=round(prior_mean, 4),
                posterior_std=round(prior_std, 4),
                clean_responsibility=1.0,
                credible_interval_95=(round(low, 4), round(high, 4)),
                info_gain_bits=0.0,
                deal_probability=None if deal_prob is None else round(deal_prob, 4),
                interval_model="student_t",
                robust_weight=1.0,
                markup_identified=markup_identified,
            )

        pred_mean = prior_mean + float(estimated_markup)
        pred_var = (prior_std ** 2) + (self.obs_noise_std ** 2)
        pred_std = math.sqrt(max(pred_var, 1e-12))
        z = (float(observed_offer) - pred_mean) / pred_std
        weight = (df + 1.0) / (df + z * z)
        obs_var = (self.obs_noise_std ** 2) / max(weight, 1e-8)
        kalman = (prior_std ** 2) / ((prior_std ** 2) + obs_var)
        y_tilde = float(observed_offer) - float(estimated_markup)
        post_mean = prior_mean + kalman * (y_tilde - prior_mean)
        post_var = max((1.0 - kalman) * (prior_std ** 2), 1e-12)
        post_std = math.sqrt(post_var)
        influence = min(1.0, weight)

        kl_nats = (
            math.log(prior_std / post_std)
            + (post_var + ((post_mean - prior_mean) ** 2)) / (2 * (prior_std ** 2))
            - ONE_HALF
        )
        info_gain_bits = max(0.0, kl_nats / math.log(2))
        low, high = _student_interval(post_mean, post_std, df)
        eval_offer = our_next_offer if our_next_offer is not None else observed_offer
        deal_prob = _deal_probability(float(eval_offer), post_mean, post_std, df)
        return BayesianBeliefState(
            posterior_mean=round(post_mean, 4),
            posterior_std=round(post_std, 4),
            clean_responsibility=round(influence, 4),
            credible_interval_95=(round(low, 4), round(high, 4)),
            info_gain_bits=round(info_gain_bits, 4),
            deal_probability=round(deal_prob, 4),
            interval_model="student_t",
            robust_weight=round(weight, 4),
            markup_identified=markup_identified,
        )


_default_engine = RobustBayesianInferenceEngine()


def robust_bayes_update(
    prior_mean: float,
    prior_std: float,
    observed_offer: Optional[float] = None,
    estimated_markup: float = 0.0,
    our_next_offer: Optional[float] = None,
    markup_identified: bool = False,
) -> BayesianBeliefState:
    """Student-t reserve update. Markup defaults to zero, not to 0.08."""
    return _default_engine.update(
        prior_mean=prior_mean,
        prior_std=prior_std,
        observed_offer=observed_offer,
        estimated_markup=estimated_markup,
        our_next_offer=our_next_offer,
        markup_identified=markup_identified,
    )


def compute_calibrated_deal_prob(offer: float, reserve_mean: float, reserve_std: float) -> float:
    """P(reserve <= offer) under the Student-t belief, not a normal CDF."""
    return _deal_probability(offer, reserve_mean, reserve_std, STUDENT_T_DF)


def empirical_bayes_prior(closed_multiples: Sequence[float]) -> tuple[float, float]:
    """Grand mean of the user's closed prices divided by their targets."""
    return robust_location(closed_multiples)
