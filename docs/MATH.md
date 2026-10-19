# Decision math

A price on the live desk, on `POST /negotiate`, on `POST /api/v1/sessions/{id}/round`, and on `POST /api/v1/multi-auction/run` is a formula applied to the caller's target, ceiling, and quotes, plus labelled policy the caller can see. It is not a stored scenario price. The labelled demo path is `src/app/demo_data.py`. Nothing else in `src/engine`, `src/desk`, or `src/app` imports `src.sim`.

The constant registry is [constants_registry.json](constants_registry.json). `scripts/check_decision_literals.py` fails when a heuristic assignment is missing from that file, or when a decision module grows a numeric literal that is not 0, 1, −1, 2, 3, 4 or an entry under `inline`.

## What still cannot be derived from data

These stay labelled assumptions until the user records the missing observations. They are not silent factory facts.

- Walk-away intercept, quote-gap coefficient, reserve-gap coefficient, and supplier spread. Quote text is not an outcome. The posterior moves only after a person records continue, walk-away, or agreed.
- The split between markup and reserve. Quotes identify their sum. An agreed price on a closed deal is what separates them. Until then the markup is 0 unless the user sets it.
- Opponent archetypes and concession schedules. They exist only on the legacy CLI grid (`src/engine/solver.py`). `/round` does not call that grid.
- Total-cost crisis and markdown ratios. They apply only when the caller supplies a lead-time or MOQ baseline. A missing baseline skips the term.
- Risk tolerance, search cost, and the ceiling. Those are preferences.

## One-step counter

Code: `src/engine/one_step.py`, `src/engine/coupled.py`.

One shared sample of scenarios is drawn. For each supplier the search scores offers on a grid and keeps the offer with the highest expected surplus among those whose credible walk-away hazard is at or under the user's cap.

Surplus of an offer, in target units, is the mean of:

- accept: `ceiling_norm − offer − landed_adder`, when the offer clears the sampled reserve and the sampled walk-away draw says the supplier stays
- otherwise: the outside option

On the desk the outside option is 0 (`snap_to_grid=True` does not add the rival's surplus back). Rejection and walking away therefore score the same. Among offers the cap allows, a higher offer has a higher surplus, so the chosen counter is the highest grid point at or under `min(quote, ceiling, best rival)`. The auction path (`snap_to_grid=False`) still scores the rival cap, including a cap that falls between grid points.

Remeasured on this code, not copied from the earlier audit. Design: 3 labelled mandates (12/16.2, 28/36.5, 185/240), one 1.15× opening plus 15 random sets of 4–5 suppliers with quotes uniform on 1.00–1.45× target, seed 42, desk settings 5000 samples and 20 grid points, risk cap 0.15, no recorded walk-away outcomes. 217 rows. 119 (54.8%) are blocked because the credible hazard upper bound is over the cap. All 98 unblocked counters sit within one grid step of that cap; 3 land on the cap exactly. The baseline rate `σ(−2.9)` is about 5.2%. The credible upper adds the supplier spread, and a large quote gap (`βq = 3.4`) pushes aggressive offers over 0.15, which is what blocks the row. There is no continuation value for rejection, so an allowed offer is taken at the top of the grid.

The score does not contain a CVaR term. CVaR is reported on the joint offer vector and labelled as a report. The old bluff term is gone: an accepted offer is already at or above the reserve, so a bluff gap on that region was zero.

Search cost enters the Weitzman index as the cost of one more sample. The index uses the absolute posterior scale, `σ = |std|`, not `std × mean`.

## Reserve belief

Code: `src/engine/bayesian_v2.py`.

The observation model is Student-t with `STUDENT_T_DF` degrees of freedom (a numerical robustness choice, ν = 4). A quote `y` with the user's markup `μ` has predictive centre `m + μ` and weight

```
w = (ν + 1) / (ν + z²),   z = (y − m − μ) / s_pred
```

The Kalman step uses observation variance `σ_obs² / w`. A quote near 2.5 times a prior near 1.16 keeps a small positive weight, so the mean moves. The old uniform-clutter rule sent that weight to 0 and left the mean at the prior.

The reported interval is the central Student-t interval at `CREDIBLE_MASS` (95%). `P(deal)` is the Student-t CDF of `(offer − mean) / scale`. Both use the same belief. They are not a normal interval around a mixture.

If the user sets a prior, that prior is the start and each of this supplier's quotes is one update. If other deals have agreed prices, the prior is the robust location of `agreed / target` (empirical Bayes) and the quotes update it. Otherwise the posterior is the robust location of this supplier's own normalised quotes, with markup removed only when the user set a markup. The location is the median, then IRLS with the same Student-t weights, and the scale starts from `MAD × MAD_NORMAL_CONSISTENCY`.

## Walk-away hazard

Code: `src/engine/hazard.py`, replay in `src/desk/recompute.py`.

```
η = β0 + βq · g_quote + βr · g_reserve + β_reciprocity · reciprocity + β_round · round + β_alt · n_alt + u
P(walk away) = 1 / (1 + exp(−η))
```

`u` is a supplier effect. Its prior is normal, mean 0, scale `τ`. Recorded outcomes multiply the grid likelihood. `prior_driven` stays true until `HAZARD_MIN_EVENTS` outcomes. Agreed counts as continue. The credible upper bound interpolates the grid CDF, and the predictive probability evaluates the logistic at that interpolated effect.

The baseline rate the desk shows is `σ(β0)`, with a band `σ(β0 ± τ)`. Those three numbers are labelled assumptions until enough outcomes exist. The desk stores them on the deal when the user saves the assumption form, and replays `hazard_outcomes` before the next solve.

Why a labelled baseline blocks rows: `σ(−2.9)` is about 5%. An aggressive offer has a large quote gap, and `βq` is 3.4, so the credible upper bound (the `τ` quantile of the effect, not only the mean) crosses the default 0.15 cap. The row is then blocked and no counter is issued. Halving the intercept, doubling `τ`, or tightening the cap moves that bound. The desk shows the band so the sensitivity is visible before a solve.

## Shared reserve factor

Code: `sample_joint_scenarios` in `src/engine/coupled.py`.

Idiosyncratic loading `sqrt(1 − ρ)`, common loading `sqrt(ρ)`. Each shock then has variance 1 and the correlation of two suppliers' shocks is `ρ`. The previous loadings `sqrt(1 − ρ²)` and `sqrt(ρ)` produced correlation `ρ / (1 − ρ² + ρ)`, which is about 0.248 when `ρ` is 0.3. The reserve draw is `mean + std × shock`, and `std` is the absolute posterior scale.

## Clopper-Pearson upper bound

Code: `scripts/benchmark_stats.py`.

For `s` successes in `n` trials the upper bound is the `p` such that `P(X ≤ s | p) = α`. That is the `1 − α` quantile of `Beta(s + 1, n − s)`. Zero events stay `1 − α^(1/n)`. The search moves upward while the lower-tail CDF is above `α`. The old search started at `s/n`, where the upper tail is already about one half, and then only searched upward, so any `s ≥ 1` returned about 1.

## Landed cost

Code: `src/engine/landed.py`. A missing freight, duty, or fee is "no data". It is not filled with zero. Ranking uses the unit price when any comparable supplier is missing a component.

## Legacy grid

`src/engine/solver.py` still scores one opening and one concession schedule for the CLI. It requires an explicit ceiling and opponent rows installed by `bind_archetypes`. HTTP `/round` returns 404 when the session does not exist and otherwise calls `one_step_counter`. The README describes that one-step lookahead, not a multi-ply expectimax.

## Categories

| Category | Meaning |
| --- | --- |
| c | Cited identity or statistical constant |
| d | Labelled policy or labelled assumption the user can replace |
| n | Numerical or display guard |

Counts are whatever `docs/constants_registry.json` holds when the checker runs. The checker is the list.
