"""Independent, offline SciPy oracle for the decision core (optional audit dependency).

Run with `pip install scipy`, then `python scripts/audit_decision_numerics.py`.
This is a numerical cross-check, not a model-accuracy or real-savings benchmark.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import betainc, expit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.desk.decide import issued_offer  # noqa: E402
from src.engine.decision import choose_offer, hazard_floor  # noqa: E402
from src.engine.hazard_fit import load_hazard_fit  # noqa: E402
from src.engine.reserve import ReserveState  # noqa: E402


def audit(cases=300, seed=76123, shape_low=-1, shape_high=2):
    rng = np.random.default_rng(seed)
    fit = load_hazard_fit()
    compared = blocked = unevaluable = 0
    worst_loss = 0.0
    failures = []
    for index in range(cases):
        alpha, beta = map(float, 10 ** rng.uniform(shape_low, shape_high, 2))
        lower = float(rng.uniform(0, .8))
        upper = float(rng.uniform(lower + .001, 1))
        state = ReserveState(alpha, beta, upper=upper, reject_ratios=(lower,), reference_ask=30)
        if not state.numerically_valid():
            unevaluable += 1
            continue
        if not lower <= state.mean() <= upper or not 0 <= state.std() <= (upper-lower)/2 + 1e-9:
            failures.append({'case': index, 'reason': 'posterior moments outside interval bounds'})
        ask = 30 * upper
        outside = ask * float(rng.uniform(.6, 1))
        cap = float(rng.uniform(.01, .5))
        choice = choose_offer(ask=ask, outside=outside, risk_cap=cap, state=state)
        if choice.blocked:
            blocked += 1
            continue
        lo = hazard_floor(ask, cap, 0, fit)
        hi = min(ask, outside)

        def cdf(x):
            if lower > alpha / (alpha + beta):
                mass = betainc(beta, alpha, 1-lower) - betainc(beta, alpha, 1-upper)
                value = (betainc(beta, alpha, 1-lower) - betainc(beta, alpha, 1-x)) / mass
            else:
                mass = betainc(alpha, beta, upper) - betainc(alpha, beta, lower)
                value = (betainc(alpha, beta, x) - betainc(alpha, beta, lower)) / mass
            return np.clip(value, 0, 1)

        def score(x):
            increment = np.maximum(expit(fit.intercept + fit.gap_coef*(ask-x)/ask)-expit(fit.intercept), 0)
            return (cdf(x/30)-increment)*(hi-x)

        xs = np.linspace(lo, hi, 2001)
        ys = score(xs)
        if not np.isfinite(ys).all():
            unevaluable += 1
            continue
        best = int(np.argmax(ys))
        optimum = minimize_scalar(lambda x: -score(x), bounds=(xs[max(best-1, 0)], xs[min(best+1, 2000)]),
                                  method='bounded')
        reference = max(float(ys[best]), float(-optimum.fun))
        loss = reference - float(score(choice.push_counter))
        compared += 1
        worst_loss = max(worst_loss, loss)
        if loss > 1e-5:
            failures.append({'case': index, 'reason': 'objective loss against independent search', 'loss': loss})
        for mode in ('default', 'push'):
            offer, _ = issued_offer(choice, outside, ask, mode)
            if offer is None:
                continue  # A continuous interval need not contain a whole-cent price.
            risk = max(float(expit(fit.intercept + fit.gap_coef*(ask-offer)/ask)-expit(fit.intercept)), 0)
            if not (0 < offer <= hi and risk <= cap + 1e-9):
                failures.append({'case': index, 'reason': 'issued price/risk constraint', 'mode': mode})
    return {'kind': 'offline numerical cross-check, not a supplier benchmark', 'seed': seed, 'cases': cases,
            'shape_exponents': [shape_low, shape_high],
            'compared': compared, 'blocked': blocked, 'unevaluable': unevaluable,
            'worst_objective_loss': worst_loss, 'failures': failures}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=int, default=300)
    parser.add_argument('--seed', type=int, default=76123)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--shape-low', type=float, default=-1)
    parser.add_argument('--shape-high', type=float, default=2)
    args = parser.parse_args()
    result = audit(args.cases, args.seed, args.shape_low, args.shape_high)
    encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.out:
        args.out.write_text(encoded)
    print(encoded)
    raise SystemExit(bool(result['failures']))
