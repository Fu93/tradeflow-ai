# Decision math

A price on the live desk, on `POST /negotiate`, on `POST /api/v1/sessions/{id}/round`, and on `POST /api/v1/multi-auction/run` is a formula applied to the caller's target, ceiling, and quotes, plus labelled policy the caller can see. It is not a stored scenario price. The labelled demo path is `src/app/demo_data.py`. Nothing else in `src/engine`, `src/desk`, or `src/app` imports `src.sim`.

The constant registry is [constants_registry.json](constants_registry.json). `scripts/check_decision_literals.py` fails when a heuristic assignment is missing from that file, or when a decision module grows a numeric literal that is not 0, 1, −1, 2, 3, 4 or an entry under `inline`.

## What still cannot be derived from data

These stay labelled assumptions until the user records the missing observations. They are not silent factory facts.

- Walk-away intercept, quote-gap coefficient, reserve-gap coefficient, and supplier spread. Quote text is not an outcome. The posterior moves only after a person records continue, walk-away, or agreed.
- The split between markup and reserve. A quote is an ask. It becomes a reserve only when the user sets a markup, including an explicit zero, or when the same product has an agreed price. An unset markup withholds the counter. It does not silently place the reserve on the ask.
- Opponent archetypes and concession schedules. They exist only on the legacy CLI grid (`src/engine/solver.py`). `/round` does not call that grid.
- Total-cost crisis and markdown ratios. They apply only when the caller supplies a lead-time or MOQ baseline. A missing baseline skips the term.
- Risk tolerance, search cost, the ceiling, and the plausibility multiple. Those are preferences. The desk exposes them. `UNIT_SANITY_MULTIPLE` is 3: a unit price is auto-accepted only from target/3 up to max(target, ceiling)×3. A human can still accept a reviewed figure.

## One-step counter

Code: `src/engine/one_step.py`, `src/engine/coupled.py`.

The counter maximises expected surplus of the one-step accept-or-reject model. The chance the reserve sits at or under the offer is the Student-t cdf with `STUDENT_T_DF` degrees of freedom, the same distribution as the reported interval. On the accept region the reserve gap is zero, so the walk-away chance depends on the quote gap and the supplier effect. The search uses `SEARCH_STEP` (0.001 of the target) and keeps lattice points at or below the cap. `DESK_N_GRID` and the old half-target floor do not choose the offer. Monte Carlo samples are drawn once and only fill the reported tail. They do not pick the price.

Surplus of an offer, in target units, is

`outside + P(accept) × max(0, max(outside, ceiling − landed adder − offer) − outside)`.

`P(accept) = Φ((offer − reserve mean) / reserve std) × (1 − walk-away probability)`.

On the desk the outside option is 0. A tighter rival quote can only lower the highest offer the search may consider. It cannot raise another supplier's counter. The auction path keeps the rival's surplus as the outside option. If the best surplus does not beat that outside option, the counter is withheld.

A quote with no reserve prior, no same-product agreed price, and no user markup is an ask. The counter is withheld and labelled `ask_only`. If the user sets a markup, including zero, the reserve is the quote minus that markup. A counter that then sits on the ask is labelled `at_ask`. It is not a silent echo. A counter below the ask is labelled `concession`.

The walk-away upper bound integrates the labelled logistic over the user's supplier spread. The effect grid covers at least eight spreads, so a spread of 0.5, 1.5, or 2.5 is inside the support. The coefficients stay the labelled policy. Recorded continue and walk-away events update the supplier effect only.

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

`HAZARD_CREDIBLE_ALPHA` is 0.05, a labelled 95% level. `/negotiate` and `/multi-auction/run` accept `hazard_intercept`, `hazard_tau`, `hazard_quote_gap`, `hazard_reserve_gap`, `bayes_obs_noise`, and `hazard_credible_alpha`. On the seed-19 bodies with markup 0.05, moving the level from 0.05 to 0.10 changed the block bit on 28 of 539 quotes (5.2%). `/negotiate` also reads `rounds` as the hazard round index and `lambda_break` as a multiplier on the walk-away probability. The round coefficient is 0 and `lambda_break` defaults to 1, so those defaults do not move an offer.

## Shared reserve factor

Code: `sample_joint_scenarios` in `src/engine/coupled.py`.

Idiosyncratic loading `sqrt(1 − ρ)`, common loading `sqrt(ρ)`. Each shock then has variance 1 and the correlation of two suppliers' shocks is `ρ`. The previous loadings `sqrt(1 − ρ²)` and `sqrt(ρ)` produced correlation `ρ / (1 − ρ² + ρ)`, which is about 0.248 when `ρ` is 0.3. The reserve draw is `mean + std × shock`, and `std` is the absolute posterior scale.

## Clopper-Pearson upper bound

Code: `scripts/benchmark_stats.py`.

For `s` successes in `n` trials the upper bound is the `p` such that `P(X ≤ s | p) = α`. That is the `1 − α` quantile of `Beta(s + 1, n − s)`. Zero events stay `1 − α^(1/n)`. The search moves upward while the lower-tail CDF is above `α`. The old search started at `s/n`, where the upper tail is already about one half, and then only searched upward, so any `s ≥ 1` returned about 1.

## Landed cost

Code: `src/engine/landed.py`. A missing freight, duty, or fee is "no data". It is not filled with zero. Ranking uses the unit price when any comparable supplier is missing a component.

## Legacy grid

`src/engine/solver.py` still scores one opening and one concession schedule for the CLI. It requires an explicit ceiling and opponent rows installed by `bind_archetypes`. HTTP `/round` returns 404 when the session does not exist and otherwise calls `one_step_counter`. That call maximises expected surplus. It is not a multi-ply expectimax.

## Categories

| Category | Meaning |
| --- | --- |
| c | Cited identity or statistical constant |
| d | Labelled policy or labelled assumption the user can replace |
| n | Numerical or display guard |

Counts are whatever `docs/constants_registry.json` holds when the checker runs. The checker is the list.
