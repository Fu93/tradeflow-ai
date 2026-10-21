"""Hazard-transfer sensitivity: what does the eBay fit being wrong cost?

The desk's walk-away hazard is an eBay Best Offer 2012-13 fit (Backus,
Blake, Larsen, and Tadelis 2020, QJE) transferred to factory negotiation
and labelled as such everywhere. robustness.py quantifies prior-mean,
prior-std, and misbelief sensitivity; LIMITATIONS section 13 catalogues
every engine constant. The hazard transfer itself had no measured
sensitivity anywhere in the repository — this script is that number.

Two families of perturbation, on the seller-ended logistic:

  1. Within-statistical-uncertainty: intercept shifted by the fit's own
     clustered standard errors (±1 SE = 0.031, ±2 SE = 0.062). If the
     counter is stable here, the transfer's *statistical* error is
     already priced in.
  2. Transfer-scale misfit: the Chinese-factory world differs from eBay
     2012-13 by more than sampling noise. Intercept shifts of ±0.5, ±1.0
     logit and gap-coefficient scales of x0.5 .. x2.0 model a supplier
     that walks much more or less readily than an eBay seller did.

For each scenario the engine plans on the baseline fit (as it does in
production), the chosen counter is then evaluated in the scenario world,
and the loss is the expected-value gap against the counter that would
have been optimal in that world. That number is the price of the
transfer, in objective units, for one representative decision.

Run:  python scripts/hazard_sensitivity.py
Out:  console table + results/hazard_sensitivity.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.decision import choose_offer, load_hazard_fit, objective  # noqa: E402
from src.engine.hazard_fit import HazardFit  # noqa: E402
from src.engine.reserve import ReserveState  # noqa: E402

# One representative desk decision: a confirmed 15.60 ask, a 14.00 rival
# outside option, the policy belief anchored on that ask. The same setting
# the engine tests use; scenario numbers stay out of production modules.
ASK = 15.6
OUTSIDE = 14.0
RISK_CAP = 0.5
STATE = ReserveState.policy().with_reference(ASK)

SCENARIOS = [
    # (label, intercept_delta, gap_scale, family)
    ("baseline", 0.0, 1.0, "baseline"),
    ("se+1", +0.0309, 1.0, "statistical"),
    ("se-1", -0.0309, 1.0, "statistical"),
    ("se+2", +0.0618, 1.0, "statistical"),
    ("se-2", -0.0618, 1.0, "statistical"),
    ("walk+0.5", +0.5, 1.0, "transfer"),
    ("walk+1.0", +1.0, 1.0, "transfer"),
    ("walk-0.5", -0.5, 1.0, "transfer"),
    ("walk-1.0", -1.0, 1.0, "transfer"),
    ("gap x0.5", 0.0, 0.5, "transfer"),
    ("gap x0.75", 0.0, 0.75, "transfer"),
    ("gap x1.5", 0.0, 1.5, "transfer"),
    ("gap x2.0", 0.0, 2.0, "transfer"),
]


def _params(fit: HazardFit, intercept_delta: float, gap_scale: float) -> tuple[float, float]:
    seller = fit.seller_ended
    base_intercept = float(seller.intercept)
    base_gap = float(seller.gap)
    return base_intercept + intercept_delta, base_gap * gap_scale


def _value(offer: float, intercept: float, gap: float, fit: HazardFit) -> float:
    return objective(
        offer, ASK, OUTSIDE, STATE, 0.0, fit,
        intercept=intercept, gap_coef=gap,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="results/hazard_sensitivity.json",
                        help="output JSON path (default results/hazard_sensitivity.json)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    fit = load_hazard_fit()
    base_intercept, base_gap = _params(fit, 0.0, 1.0)

    # The engine's production behaviour: plan on the baseline fit.
    planned = choose_offer(ask=ASK, outside=OUTSIDE, state=STATE, risk_cap=RISK_CAP)
    planned_counter = planned.push_counter

    rows = []
    for label, di, gs, family in SCENARIOS:
        intercept, gap = _params(fit, di, gs)
        # Counter that would be optimal in the scenario world.
        scenario_offer = choose_offer(
            ask=ASK, outside=OUTSIDE, state=STATE, risk_cap=RISK_CAP,
            intercept=intercept, gap_coef=gap,
        )
        opt_counter = scenario_offer.push_counter
        v_opt = _value(opt_counter, intercept, gap, fit)
        v_plan = _value(planned_counter, intercept, gap, fit)
        loss = v_opt - v_plan
        rows.append(
            {
                "scenario": label,
                "family": family,
                "intercept": round(intercept, 4),
                "gap_coef": round(gap, 4),
                "counter_planned": round(planned_counter, 4),
                "counter_scenario_optimal": round(opt_counter, 4),
                "value_optimal": round(v_opt, 6),
                "value_planned": round(v_plan, 6),
                "loss_vs_planned": round(loss, 6),
            }
        )

    payload = {
        "setting": {
            "ask": ASK,
            "outside": OUTSIDE,
            "risk_cap": RISK_CAP,
            "belief": "policy prior anchored on the confirmed ask",
            "engine_plan": "baseline eBay fit, as in production",
        },
        "baseline_intercept": base_intercept,
        "baseline_gap_coef": base_gap,
        "rows": rows,
        "note": (
            "Loss = expected value of the scenario-optimal counter minus the "
            "baseline-planned counter, both evaluated in the scenario world. "
            "Positive loss is the price of the eBay->factory transfer for one "
            "representative decision. Statistical rows use the fit's own "
            "clustered standard errors; transfer rows model a world that "
            "differs beyond sampling noise."
        ),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Setting: ask {ASK}, outside {OUTSIDE}, risk cap {RISK_CAP}, policy belief on the ask")
    print(f"Engine plans on the baseline fit; its planned push counter: {planned_counter:.4f}")
    print()
    print(f"{'scenario':12s} {'family':11s} {'scenario-counter':>17s} {'loss':>10s}")
    for row in rows:
        print(
            f"{row['scenario']:12s} {row['family']:11s} "
            f"{row['counter_scenario_optimal']:>17.4f} {row['loss_vs_planned']:>10.6f}"
        )
    print()
    stat_max = max((r["loss_vs_planned"] for r in rows if r["family"] == "statistical"), default=0.0)
    transfer_max = max((r["loss_vs_planned"] for r in rows if r["family"] == "transfer"), default=0.0)
    print(f"max loss within statistical uncertainty (±2 SE): {stat_max:.6f}")
    print(f"max loss across transfer-scale misfit:            {transfer_max:.6f}")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
