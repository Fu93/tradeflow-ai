# Decision math

A price on the live desk, on `POST /negotiate`, on `POST /api/v1/sessions/{id}/round`, and on `POST /api/v1/multi-auction/run` is a formula applied to the caller's target, ceiling, and quotes, plus labelled policy the caller can see. It is not a stored scenario price. The labelled demo path is `src/app/demo_data.py`. Nothing else in `src/engine`, `src/desk`, or `src/app` imports `src.sim`.

The constant registry is [constants_registry.json](constants_registry.json). `scripts/check_decision_literals.py` fails when a heuristic assignment is missing from that file, or when a decision module grows a numeric literal that is not 0, 1, −1, or 2. Every other literal, including 3, 4, and tiny floats, needs an `inline` row whose `context` is that source line. A new use of a number that is already registered on a different line fails. The scan includes `landed.py`, `quote_read.py`, `model.py`, and `store.py`.

## What still cannot be derived from data

These stay labelled until the user records the missing observations.

- The level of `D` for a supplier with fewer than 30 of their own responses. The Beta mean and strength are policy. The eBay auto-decline ratio is not that prior.
- The walk-away intercept and gap are a fit on eBay Best Offer 2012–13, Business & Industrial. Using them for a Chinese factory is a transfer, not a measurement on that factory. The supplier spread `τ` is still labelled. Recorded continue and walk-away outcomes update only that supplier's effect.
- The correlation `ρ` of reserve shocks. It stays 0. It is not identified at five suppliers or fewer.
- Risk tolerance, search cost, the ceiling, the credible level, and the plausibility multiple. Those are preferences.
- Total-cost crisis and markdown ratios. They apply only when the caller supplies a lead-time or MOQ baseline. A missing baseline skips the term.
- The one-dimensional offer is a per-round heuristic. Other suppliers' confirmed quotes are treated as fixed and recallable. The buyer is not committed to take-it-or-leave-it. It is not an exact multi-round equilibrium.

## Reserve

Code: `src/engine/reserve.py`.

For supplier `i`, `reserve = reference_ask × D` with `D` in `[0, 1]`. The reference ask is that supplier's first confirmed ask, in the same units the solver uses. The latest ask caps the offer. It does not rescale `D`. Each supplier has their own Beta prior. Another supplier's ask does not enter it. A typed markup is not applied. There is no lowest-ask rule.

The labelled prior is `D ~ Beta(α, β)` with mean `D_PRIOR_MEAN` and strength `α + β = D_PRIOR_STRENGTH`, so `α = mean × strength` and `β = (1 − mean) × strength`. The desk inputs for mean and spread replace that prior and are labelled user policy. Spread `s` sets `strength = mean(1 − mean) / s² − 1`.

A later ask, an accepted offer, and a closed-deal price are upper bounds: `D ≤ price / reference ask`. A ratio above 1 does not loosen a tighter bound. They are not draws from `D`.

A rejection is a lower bound: `D ≥ rejected offer / reference ask`. Let `L` be the largest rejection ratio and `U` the tightest upper bound. If `L ≥ U` the bounds contradict and the desk withholds a price. Otherwise the posterior is the Beta prior truncated to `[L, U]`:

`P(D ≤ x | L ≤ D ≤ U) = 0` for `x < L`, `1` for `x ≥ U`, and `(I_x(α, β) − I_L(α, β)) / (I_U(α, β) − I_L(α, β))` otherwise.

`E[D | L < D < U] = (α / (α + β)) × (I_U(α + 1, β) − I_L(α + 1, β)) / (I_U(α, β) − I_L(α, β))`.

`E[D²]` uses the same ratio of incomplete betas at `α + 2`. When `L = 0` this is the upper-truncation formula. A rejection never raises `P(accept)`.

Until `HAZARD_MIN_EVENTS` responses are on file for that supplier, the card says the prior is labelled policy.

## Offer

Code: `src/engine/decision.py`, called from `src/engine/coupled.py` and `src/engine/one_step.py`.

Let `q` be this supplier's latest ask, `q0` the reference (first) ask, and `W` the outside option: the best other confirmed landed quote, already capped by this latest ask and by the ceiling. When this supplier is the cheapest, `W = q`. The split `(q + target) / 2` is not used.

The default counter is `W`. It is labelled `at_ask` when it sits on this ask, and `match_rival` when a cheaper confirmed quote caps it.

The push-lower counter maximises

`Π(b) = (A(b) − Δh(b)) × max(W − b, 0)`

on `[hazard floor, W]`. `A(b)` is the truncated Beta CDF of `b / q0`. It is not `b / q`, and it is not multiplied by one minus the hazard. `Δh(b) = h(b) − h(q)` is the incremental walk hazard. The penalty is the surplus at stake, the option value of this supplier, not the full outside price `W`. If the maximised value is negative, the push is withheld and the card says so. An offer at the ask has `Δh = 0`, so the user's risk cap does not block it. The cap is the lowest `b` with `Δh(b)` still inside the user's setting.

The maximiser is a bounded golden-section search. A mesh step is not an input. Changing a step does not change the offer. The push is shown beside the default and is at most the default. The same search at half the prior strength and at double the prior strength, mean fixed, is the sensitivity on the card. Halving strength is `× 1/2`. Doubling strength is `× 2`. The mean is not scaled.

Two uncensored cases have a closed form, checked against the scalar maximiser of `A(b) × (W − b)` with no hazard penalty.

Uniform on `[d, 1]`. Acceptance of `b` is `(b/q − d) / (1 − d)` for `b` in `[d·q, q]`. The surplus derivative is zero at

`b* = (W + d·q) / 2`,

then clipped to `[d·q, min(q, W)]`.

`Beta(α, 1)`, whose CDF is `x^α`. With outside option `W` and no tighter cap, the first-order condition `W = b + q · G / g` gives

`b* = α W / (1 + α)`.

The general Beta and the hazard penalty use the golden-section search. The prior strength used in production has `β < 1`, so the log-concave uniqueness that needs `α ≥ 1` and `β ≥ 1` does not apply. The search evaluates the ends.

A tighter rival can only lower `W`. It cannot raise a counter.

Student-t utilities remain in `src/engine/bayesian_v2.py` for intervals and for older callers. They do not set this offer. The Weitzman index orders suppliers with a normal approximation to `ask × D`. It does not set the offer. Monte Carlo fills the reported tail. It does not pick the price. CVaR is a report.

`/negotiate` and `/webhook/evolus` delegate to this engine. The reserve is the labelled Beta prior, not a markup subtracted from the ask. `/round` uses only the caller's confirmed unit price. Raw text alone is HTTP 422 and is not a decision input. A quote above the ceiling still circuit-trips and does not issue a counter. A missing target or ceiling is HTTP 422.

## Walk-away hazard

Code: `src/engine/hazard_fit.py`, coefficients in `data/hazard_ebay_bi.json`, fit script `scripts/fit_ebay_hazard.py`. The script runs offline. It is not part of the Render service.

```
g = max(q − b, 0) / q
h(b) = σ(β0 + βg · g + u)
Δh(b) = h(b) − h(q)
```

`u` is this supplier's effect. Its prior is normal, mean 0, scale `τ`. A continue or a walk-away recorded for one supplier updates that supplier only. Quote text is not an outcome.

The JSON stores two outcomes on the same Business & Industrial threads. Thread ended with no deal includes threads the buyer ended. Seller-ended is the seller declining, auto-declining, or letting the buyer's offer expire. The decision uses seller-ended. Both outcomes carry seller-clustered intervals and a seller-grouped hold-out and a by-time hold-out. A reserve-gap coefficient is not stored as a decision term. The sample keeps a first buyer offer at or below the Buy-It-Now price. Offers above that price are dropped: a few ratios reach the thousands, and a logit on the raw gap then has a slope near zero even though the seller-ended rate rises with the discount. The coefficients, the sample size, and the hold-out errors are the ones in the JSON from this offline fit. They are not copied from a prefix of the files. The decision logit is `intercept + gap × quote_gap + small_gap × max(knot − quote_gap, 0)`. The straight line understated seller-ended walk-away on gaps below the knot; the hinge is the correction, and the JSON reports both the line and the hinge against the observed bins. The fit is the first buyer offer of a thread. The desk applies it per round to the latest ask. That transfer, and the remaining bin error, are disclosed on the card.

The user's risk cap applies to `Δh`, not to the level of `h`. The cap can limit how far the push-lower counter goes. It does not withhold the default counter at the ask.

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
