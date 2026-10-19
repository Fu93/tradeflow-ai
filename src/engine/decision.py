"""One offer per supplier per round.

The default counter matches the best other confirmed landed quote, and it
never exceeds this supplier's ask or the user's ceiling. The model's
push-lower counter maximises expected landed saving minus incremental walk
risk on that interval. It is a per-round heuristic: rivals are treated as
fixed and recallable, the buyer is not committed to take-it-or-leave-it,
and the number is not an exact multi-round equilibrium.

The maximiser is a closed form where one exists, otherwise a bounded
golden-section search. A mesh step is not an input.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config.heuristics import (
    BRENT_ITERS,
    GOLDEN_CONJUGATE,
    HAZARD_MIN_EVENTS,
    NEWTON_STEP_BITS,
    ONE_HALF,
    OPT_TOL,
)
from src.engine.hazard_fit import HazardFit, incremental_hazard, load_hazard_fit
from src.engine.reserve import ReserveState

HEURISTIC_NOTE = (
    "Per-round heuristic. Other suppliers' confirmed quotes are treated as "
    "fixed and recallable. The buyer is not committed to a take-it-or-leave-it "
    "offer. This is not an exact multi-round equilibrium."
)


@dataclass(frozen=True)
class OfferChoice:
    default_counter: float
    push_counter: float
    push_half: float
    push_double: float
    acceptance: float
    incremental_hazard: float
    objective: float
    optimiser: str
    prior_source: str
    prior_alpha: float
    prior_beta: float
    prior_mean: float
    n_responses: int
    outside: float
    blocked: bool
    counter_relation: str
    note: str
    push_withheld: bool = False
    push_note: str = ""


def _clip(value: float, lo: float, hi: float) -> float:
    return min(max(float(value), float(lo)), float(hi))


def _newton_polish(fn, guess: float, lo: float, hi: float) -> float:
    """One concave Newton step from finite differences.

    The step is wide enough that curvature is visible in float64, and short
    enough that a quadratic (and the Beta(α, 1) surplus) lands inside 1e-9
    of the closed form. A positive second difference is not a maximum, so
    the guess is left as it is. The ends are compared by the caller.
    """
    scale = max(1.0, abs(float(guess)), abs(float(lo)), abs(float(hi)))
    width = float(ONE_HALF) ** int(NEWTON_STEP_BITS)
    step = width * scale
    current = float(guess)
    for _ in range(2):
        left = current - step
        right = current + step
        if left < lo or right > hi:
            break
        f_left = float(fn(left))
        f_mid = float(fn(current))
        f_right = float(fn(right))
        second = f_left - 2 * f_mid + f_right
        if second >= 0.0:
            break
        slope = (f_right - f_left) / (2 * step)
        curve = second / (step * step)
        delta = slope / curve
        # A step larger than the stencil is not the local quadratic maximum.
        if abs(delta) > step:
            break
        current = min(max(current - delta, lo), hi)
    return current


def bounded_scalar_max(fn, lo: float, hi: float) -> float:
    """Golden-section maximum on a closed interval, then a Newton polish."""
    left_bound = float(lo)
    right_bound = float(hi)
    left = left_bound
    right = right_bound
    if right < left:
        left, right = right, left
        left_bound, right_bound = left, right
    if right - left <= float(OPT_TOL):
        return (left + right) * float(ONE_HALF)
    ratio = float(GOLDEN_CONJUGATE)
    complement = 1.0 - ratio
    x1 = left + complement * (right - left)
    x2 = left + ratio * (right - left)
    f1 = float(fn(x1))
    f2 = float(fn(x2))
    for _ in range(int(BRENT_ITERS)):
        if (right - left) <= float(OPT_TOL) * max(1.0, abs(left), abs(right)):
            break
        if f1 > f2:
            right = x2
            x2 = x1
            f2 = f1
            x1 = left + complement * (right - left)
            f1 = float(fn(x1))
        else:
            left = x1
            x1 = x2
            f1 = f2
            x2 = left + ratio * (right - left)
            f2 = float(fn(x2))
    candidates = (
        (left, float(fn(left))),
        (right, float(fn(right))),
        (x1, f1),
        (x2, f2),
    )
    best_x, _best = max(candidates, key=lambda item: item[1])
    polished = _newton_polish(fn, best_x, left_bound, right_bound)
    ends = ((float(lo), float(fn(lo))), (float(hi), float(fn(hi))), (polished, float(fn(polished))))
    best_x, _best = max(ends, key=lambda item: item[1])
    return float(best_x)


def hazard_floor(ask: float, cap: float, effect: float, fit: HazardFit, *, intercept: float | None = None, gap_coef: float | None = None) -> float:
    """Lowest offer whose incremental hazard is still inside the user's cap."""
    if float(cap) < 0.0:
        return float(ask)
    # At the ask the increment is 0, so the ask is always inside a non-negative cap.
    if incremental_hazard(ask, ask, effect, fit, intercept=intercept, gap_coef=gap_coef) > float(cap):
        return float(ask)
    low_offer = 0.0
    if incremental_hazard(ask, low_offer, effect, fit, intercept=intercept, gap_coef=gap_coef) <= float(cap):
        return low_offer
    left = low_offer
    right = float(ask)
    for _ in range(int(BRENT_ITERS)):
        mid = (left + right) * float(ONE_HALF)
        if incremental_hazard(ask, mid, effect, fit, intercept=intercept, gap_coef=gap_coef) > float(cap):
            left = mid
        else:
            right = mid
    return right


def reference_ask(state: ReserveState, latest_ask: float) -> float:
    """D is a fraction of the first ask. The latest ask is only the offer cap."""
    recorded = state.reference_ask
    if recorded is not None and float(recorded) > 0.0:
        return float(recorded)
    return float(latest_ask)


def objective(offer: float, ask: float, outside: float, state: ReserveState, effect: float, fit: HazardFit, *, intercept: float | None = None, gap_coef: float | None = None) -> float:
    """Expected landed saving minus incremental walk risk priced at the surplus at stake.

    A walk loses this supplier's option, which is worth the gap between the
    outside option and the offer. It is not priced at the full outside price.
    """
    if offer > outside or offer < 0.0:
        return -float(ask) - float(outside)
    if not state.consistent():
        return -float(ask) - float(outside)
    ref = reference_ask(state, ask)
    fraction = 0.0 if ref <= 0.0 else float(offer) / ref
    stake = max(float(outside) - float(offer), 0.0)
    saving = state.acceptance(fraction) * stake
    risk = incremental_hazard(ask, offer, effect, fit, intercept=intercept, gap_coef=gap_coef) * stake
    return saving - risk


def _push(ask: float, outside: float, state: ReserveState, effect: float, cap: float, fit: HazardFit, *, intercept: float | None = None, gap_coef: float | None = None) -> tuple[float, str]:
    hi = min(float(ask), float(outside))
    if hi <= 0.0:
        return 0.0, "golden_section"
    floor = hazard_floor(ask, cap, effect, fit, intercept=intercept, gap_coef=gap_coef)
    floor = min(max(floor, 0.0), hi)

    def score(offer: float) -> float:
        return objective(offer, ask, hi, state, effect, fit, intercept=intercept, gap_coef=gap_coef)

    found = bounded_scalar_max(score, floor, hi)
    return _clip(found, floor, hi), "golden_section"


def choose_offer(
    *,
    ask: float,
    outside: float,
    state: ReserveState | None = None,
    effect: float = 0.0,
    risk_cap: float,
    fit: HazardFit | None = None,
    intercept: float | None = None,
    gap_coef: float | None = None,
) -> OfferChoice:
    loaded = fit if fit is not None else load_hazard_fit()
    belief = state if state is not None else ReserveState.policy()
    hi = min(float(ask), float(outside))
    inconsistent = not belief.consistent()
    blocked = hi <= 0.0 or inconsistent
    default = 0.0 if blocked else hi
    push, kind = _push(float(ask), float(outside), belief, effect, float(risk_cap), loaded, intercept=intercept, gap_coef=gap_coef)
    half_state = belief.with_strength(float(ONE_HALF))
    double_state = belief.with_strength(1.0 / float(ONE_HALF))
    push_half, _kind_half = _push(float(ask), float(outside), half_state, effect, float(risk_cap), loaded, intercept=intercept, gap_coef=gap_coef)
    push_double, _kind_double = _push(float(ask), float(outside), double_state, effect, float(risk_cap), loaded, intercept=intercept, gap_coef=gap_coef)
    push = min(push, default) if not blocked else 0.0
    push_half = min(push_half, default) if not blocked else 0.0
    push_double = min(push_double, default) if not blocked else 0.0

    def score(offer: float) -> float:
        return objective(offer, float(ask), hi if hi > 0.0 else 0.0, belief, effect, loaded, intercept=intercept, gap_coef=gap_coef)

    push_withheld = (not blocked) and score(push) < 0.0
    if push_withheld:
        push = default
        push_half = default
        push_double = default
    push_note = ""
    if inconsistent:
        push_note = "The rejection lower bound meets or passes the upper bound, so no price is issued."
    elif push_withheld:
        push_note = "Push would reduce expected value, so it is withheld."
    ref = reference_ask(belief, float(ask))
    fraction = 0.0 if ref <= 0.0 else default / ref
    if blocked:
        relation = "withheld"
    elif abs(default - float(ask)) <= float(OPT_TOL) * max(1.0, abs(float(ask))):
        relation = "at_ask"
    else:
        relation = "match_rival"
    bound_note = belief.note()
    if inconsistent:
        bound_note = push_note
    return OfferChoice(
        default_counter=default,
        push_counter=push,
        push_half=push_half,
        push_double=push_double,
        acceptance=0.0 if blocked else belief.acceptance(fraction),
        incremental_hazard=incremental_hazard(ask, default, effect, loaded, intercept=intercept, gap_coef=gap_coef),
        objective=score(default),
        optimiser=kind,
        prior_source=belief.source,
        prior_alpha=belief.alpha,
        prior_beta=belief.beta,
        prior_mean=float("nan") if not belief.consistent() else belief.mean(),
        n_responses=belief.n_responses,
        outside=float(outside),
        blocked=blocked,
        counter_relation=relation,
        note=bound_note + " " + HEURISTIC_NOTE,
        push_withheld=push_withheld,
        push_note=push_note,
    )


def responses_label(count: int) -> str:
    if int(count) >= int(HAZARD_MIN_EVENTS):
        return "supplier_responses"
    return "labelled_policy"

