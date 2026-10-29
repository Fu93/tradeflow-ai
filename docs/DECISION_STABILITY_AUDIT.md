# Decision stability audit — 29 October 2026

This audit follows the submission-readiness fixes in PR #46. It tests numerical
identities and decision replay, not model extraction accuracy or factory savings.

## Reproduced problems and corrections

| Problem | Reproduction | Correction |
|---|---|---|
| Intake order differed from confirmation order | Receive 30 then 25; confirm 25 then 30. The card still showed latest 25/reference 30, while the chain's latest review was 30, and sending was refused. | Decision history and the confirmed-ask send check now use accepted-review sequence numbers. First confirmed ask is the reserve reference; the most recently confirmed quote is the current ask. |
| Valid custom policy could not be sent | Set prior mean/spread or hazard intercept/slope, choose push. The record used the custom policy, but chain replay used defaults and rejected it. | Send replay reads the same supported assumption events, including explicit null resets. Unsupported fields are not silently promoted to decision inputs. |
| Equal timestamps reordered supplier outcomes | A walk-away and reopen in the same millisecond could sort by random UUID in reverse. | SQLite insertion order breaks ties independently of clocks and random identifiers; audit sequence still orders confirmations. Backdated chain entries retain their existing refusal policy. |
| Tail CDF subtraction lost the posterior mass | Beta(1,20) on [0.9,0.95] subtracted two CDFs rounded to 1 and became unavailable. Another tail case displaced the push by about 0.033 currency units. | Reflect interval probabilities into the survival tail. Use log1p/expm1 in elementary tail identities. |
| Narrow intervals lost their spread | A uniform posterior on [0.999999,0.9999999] reported standard deviation 0. | Reflect upper-tail moments; use centred, log-scaled Gauss–Legendre moments for narrow interior intervals. The uniform identity is width/sqrt(12). |
| Positive extreme spread divided by zero | std=1e-300 underflowed when squared before deriving prior strength. | Divide by spread twice, then apply the documented strength bounds. |
| A continued fraction could return an unconverged approximation | Large concentrated priors and insufficient iteration budgets could report a value without convergence. | Raise the registered decision ceiling to 1024, retain early convergence, and withhold on unavailable numerical evaluation. Invalid shapes and non-finite shape totals are not usable beliefs. |
| Finite inputs produced non-finite derived arithmetic | Extremely small plausibility multipliers or enormous freight/duty produced infinity or zero derived bounds. | Reject unstable price bounds and landed totals before writing an event or policy; reject non-finite outcome amounts. Do not count a realised saving when derived landed arithmetic is unavailable. |

The scalar solver also withholds a counter if any required objective evaluation
is unavailable. It does not substitute a zero acceptance probability or a fake
successful optimisation for a numerical failure.

## Independent numerical evidence

[`scripts/audit_decision_numerics.py`](../scripts/audit_decision_numerics.py) uses
SciPy's independent beta implementation and a dense bracket followed by bounded
scalar search. SciPy is an optional audit dependency, not a runtime dependency.

| Run | Cases | Compared feasible cases | Price/risk blocked | Numerically unavailable or reference unavailable | Maximum objective gap |
|---|---:|---:|---:|---:|---:|
| Seed 76123, shape parameters 10^-1 through 10^2 | 1000 | 846 | 154 | 0 | 1.1546319456101628e-14 |
| Seed 88416, wider shapes 10^-4 through 10^5 | 1000 | 590 | 96 | 314 | 1.2934542326092924e-11 |

Recorded results: [regular cross-check](../results/decision_numeric_audit.json)
and [wider numerical stress](../results/decision_numeric_stress.json). Neither
found a failed tested price/risk constraint or objective-gap threshold. The 314
unavailable cases are explicitly excluded from the oracle comparison; this is
not evidence that every possible truncated distribution can be evaluated in
float64. Production withholds decisions when its posterior is unavailable.

```bash
python -m pip install scipy
python scripts/audit_decision_numerics.py --cases 1000
python scripts/audit_decision_numerics.py --cases 1000 --seed 88416 --shape-low -4 --shape-high 5
python -m pytest tests/test_deep_stability.py tests/test_numerics.py -q
```

Permanent regression tests use exact probability identities, all six confirmation
orders of three quotes, frozen timestamps with descending IDs, valid policy
changes/resets, unavailable objectives, and pre-persistence overflow rejection.
They do not require SciPy. Existing full-suite, type, lint, coverage and mutation
gates remain enabled.

## Practical boundary

These checks improve the implemented one-step model and its state consistency.
They do not prove a globally optimal multi-round negotiation, calibrate a factory
walk-away rate, verify real supplier messages, or certify every possible input
and process failure. Database and audit storage remain subject to their existing
operational backup and recovery requirements. Live deployment must be verified
separately by its commit identity; a merged PR alone is not a deployment check.
