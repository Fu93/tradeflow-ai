# TradeFlow AI — Project Status

> Single source of truth for the current state of this repository.
> Supersedes every planning document in `docs/archive/` (kept for
> traceability only, never for current guidance).
> Last updated: 2026-10-21.

## Competition outcome (honest record)

- **AMD Developer Hackathon ACT III online phase: 12–18 October 2026. The
  event has concluded** (winners announced on the official recap page).
- No lablab submission record for TradeFlow has been located: the event
  page does not list it, search engines do not index a team page, and this
  repository carries no submission tag or marker. If a submission was in
  fact completed, the lablab confirmation email is the authoritative
  record; treat everything here as "no verified submission".
- The 2026-10-19/20 commits (CVaR consistency, v2 architecture phase 1)
  were built after the online phase ended, without a competition forcing
  function. One of them (9ab8edc, v2 phase 1) broke `verify_all.py`
  pillar 2 while the pytest suite stayed green; CI did not run
  `verify_all` at the time. This is fixed and `verify_all` is now a named
  CI step.

## What this repository is now

- A **research prototype** of a constrained-decision negotiation desk:
  Bayesian belief + expected-surplus solver choose the price; an optional
  language model only reads and phrases; a human approves every send.
- No factory has closed a deal through it. All benchmark numbers come
  from this repository's CPU runs or a labelled simulator. The honest
  boundary list lives in `docs/LIMITATIONS_AND_REAL_WORLD_FINDINGS.md`.
- Two architecture generations coexist (see `ARCHITECTURE.md`):
  the v1 negotiation engines (active implementation in `src/legacy/`,
  re-exported through `src/engine/`) and the v2 phase-1 desk
  (`src/desk/` + `src/engine/decision.py`, `reserve.py`,
  `hazard_fit.py`).

## Verified state (as of 2026-10-23, local run, Windows)

| Check | Result |
|---|---|
| `python -m pytest tests -q` | 813 passed, 6 skipped, 112 subtests, 3 failed |
| `python verify_all.py` | ALL 6 PILLARS PASS |
| `python verify_live.py` | V2 DESK SURFACE VERIFIED END TO END (9 steps) |
| `python scripts/mutation_sanity.py` | 66 mutants killed |
| `python scripts/check_decision_literals.py` | decision registry ok (180 constants) |
| `python scripts/check_no_fake_literals.py` | no undeclared scenario floats |
| `ruff check` + `mypy` (phase-1 modules) | clean |
| Frozen-reserve wiring regression | covered by `tests/test_explicit_suppliers_frozen_reserves.py` |

The 3 failures are platform-only and pre-date this pass: `test_round_rss.py`
and `test_coupled_properties.py` read `/proc/self/status`, and
`test_desk_phase2.py::test_five_supplier_streaming_budget` asserts a Linux RSS
ceiling. They fail on Windows and pass on the Linux CI image.

## Hardening pass (2026-10-23)

Four independent audits (architecture, decision logic, security, performance)
were run against this repository, then the findings were fixed in place. Every
fix has a regression test in `tests/test_hardening_2026_10_23.py` (37 tests).

### Security — the two claimed properties were off by default

- **The published sandbox token was a universal API-key bypass.**
  `security.py` accepted the constant `SANDBOX-DEMO-TOKEN` even when
  `TRADEFLOW_API_KEY` was configured, so a key from a public repository
  authenticated a configured server. It is now rejected whenever a real key is
  set, and the comparison is constant-time (`_same_secret`).
- **Signed approval was opt-in by accident.** The gate was
  `required_approval_pubkey() is not None`, so with the variable unset the
  server accepted `human_approved: true` as proof of a human. There is now an
  explicit `approval_policy()`: `TRADEFLOW_ENV=production` requires a signature,
  and a production desk with **no pinned key refuses every send** rather than
  accepting the boolean. A signature from an unpinned key is refused explicitly
  (`approval_is_pinned`), not silently accepted.
- **No request body limit.** Pydantic validated field sizes only after the ASGI
  server had buffered the whole body, so one 500 MB POST could OOM the instance.
  `src/app/limits.py` rejects on `Content-Length` before reading and counts
  bytes while streaming; oversized bodies get 413.
- **Key material could be committed.** `.gitignore` now covers `*.pem`, `*.key`,
  `*.p8`, `*.p12`, and approval keypair JSON. `.dockerignore` already did.
- **Backdated chain entries.** A keyed MAC over a falsified clock is still a
  valid MAC. `append` now refuses a timestamp earlier than the head, and
  `verify` reports `timestamp_regression` for rows written before the guard.
- **`render.yaml`** pins `TRADEFLOW_APPROVAL_PUBKEY` (`sync: false`) so a
  production deploy states its intent instead of failing silently at send time.

### Correctness — seven defects in how a price is produced, recorded, or labelled

- **An extreme prior std collapsed the belief.** `std=1e-5` produced α+β≈9e8,
  where the `lgamma` subtraction loses enough precision that `cdf(mean)` returns
  `0.0` — "this supplier never accepts anything" — and the desk issued the full
  ask as a counter labelled "this supplier's posterior". `from_moments` now
  clamps the implied strength, and a belief that cannot be evaluated
  (`numerically_valid()`) **withholds the price** instead of voting for the worst
  offer.
- **The record and the message disagreed about the price.** The record stored
  `8.196919283`; the supplier read `8.20`. A ceiling of `8.1952` passed the
  check while the supplier was offered above it. `src/desk/money.py` is now the
  single choke point: `quantise_offer` rounds half-up to whole minor units and
  holds the result at or below both caps at cent precision. `build_record` adds
  a `counter_quantised` check, so a sub-cent counter is withheld rather than
  recorded.
- **A duplicate quote forged the provenance label.** Thirty re-ingests of one
  quote counted thirty responses, flipping `source` to `supplier_responses`
  while α/β were still the policy prior. `tighten` no longer counts a bound that
  did not move.
- **Blocked suppliers reported a price of zero.** `offers[i]` was `0.0` for a
  blocked supplier. `solve_coupled` now also returns `offers_masked` with `None`.
- **`/negotiate` silently ignored `prior_reserve_mean`.** It accepted the
  parameter and always used the policy prior, then echoed a posterior as if the
  caller's prior had been applied. It is honoured now (as D = reserve / observed
  offer), the response carries `prior_applied`, and an out-of-range value is a
  422 instead of a no-op. Omitting it is byte-identical to before.
- **TCO crashed on its own documented default.** `TradeTermsSpec()` leaves both
  baselines `None`, and the linear path subtracted and divided by them
  (`TypeError`, `ZeroDivisionError`). The `None` guards that the nonlinear path
  already had are now on both.
- **`p_accept` was evaluated at the ask**, which is 1.0 by construction, so
  `/negotiate` reported 100 % acceptance for every supplier. It is now evaluated
  at the issued counter, with `p_accept_push` beside it.

### Performance — measured, not estimated

| Operation | Before | After | Change |
|---|---|---|---|
| `choose_offer` (1 supplier) | 1.82 ms | 0.60 ms | −67 % |
| `solve_coupled` (5 suppliers) | 10.9 ms | 5.93 ms | −46 % |
| `solve_coupled` (20 suppliers) | 46.8 ms | 22.2 ms | −53 % |
| `verify_chain` (5,000 entries) | 84.5 ms | 71.1 ms | −16 % |
| `import src.app.main` | 2.5–2.9 s | 1.78 s | −35 % |

- The truncated Beta's `(mass, F(lower))` is memoised on `ReserveState`, and
  `regularized_beta` itself is memoised: one `choose_offer` issues ~844 CDF
  evaluations and the optimiser re-evaluates the same triples.
- `incremental_hazard`'s zero-gap base reduced to `sigmoid(b0 + effect)` and is
  memoised; it was recomputed through a full `hazard_at_gap` call (which re-read
  the 7 KB fit file) 912 times per decision.
- `load_hazard_fit` is memoised — it was 157 µs of file I/O per supplier per
  decision.
- Four missing indexes were added. `EXPLAIN QUERY PLAN` went from
  `SCAN` + temp B-tree to `SEARCH ... USING INDEX` on `quote_events`, `drafts`,
  and `decisions`.
- `onnxruntime` is imported on first use, not at module import. It is still
  required, and `_warmup_stage1` still pays for it at startup.
- The audit verify endpoint returns a verdict plus a paginated window instead of
  the whole chain: at 100k entries the old response was a 36.6 MB body.
- The chain MAC pass now runs outside the store lock: rows and the pending mark
  are fetched under the lock, and the O(n) HMAC work happens after it is
  released. **Honest limit:** eight concurrent 5,000-entry verifications still
  take 563 ms of wall time because the work is pure-Python CPU under the GIL.
  The lock no longer adds latency; the GIL is now the ceiling for this workload.
- The extractor thread pool is shut down from the lifespan handler; it was a
  module global that was never closed.

### Architecture

- `src/legacy/coupled.py`, `multi_solver.py`, `one_step.py`, `orchestrator.py`,
  and `proposal.py` no longer import their own siblings through the
  `src.engine` shims. The compatibility layer is now external-only, which is
  what makes the 2026-10-21 "freeze" decision actually stable.
- `src/app/limits.py` and `src/desk/money.py` are new, single-purpose modules.
- `GET /ready` is a real readiness probe (chain key, writable store, approval
  policy) and returns 503 with the blocking list. `GET /health` stays a cheap
  liveness probe with its existing body, because callers depend on it.

### Not done (deliberate, with reasons)

- **`src/desk/service.py` (2,237 lines) and `src/app/main.py` (2,888 lines) are
  still god modules.** The decomposition plan (extract `desk/verify.py` from the
  280-line send guard, then `ingest`/`delivery`/`polling`/`bus`; split the
  877-line round handler into `app/round/*`) is written down but not executed.
  It is a large mechanical diff across 116 test imports, and it should not share
  a commit with behavioural fixes.
- **No SQLite migration manager.** `_ensure_column` is additive only and
  `PRAGMA user_version` is never written. A rename, type change, or backfill
  cannot be expressed.
- **No per-user identity.** The desk is single-tenant behind one shared API key.
  Supplier address changes are still gated by a client-supplied
  `human_confirmed` boolean rather than a signature, which is the real
  business-email-compromise path. This needs a user model, not a code tweak.
- **`src/engine/bilingual.py` (2,035 lines)** is outside both the coverage gate
  and the mutation set. Its `NumericGuardrailValidator` has no mutants.
- **`_betacf` is still duplicated** in `reserve.py` and `legacy/bayesian_v2.py`
  with different tolerances. Measured money impact is 1.86e-9 absolute, so it is
  a maintenance liability, not a live defect.
- **The rate limiter is in-memory and per-process**, and only covers the AMD
  routes. Restarting resets it.

## Not verified (do not claim otherwise)

- No real supplier, no real quote, no real deal, no real user.
- Walk-away hazard is an eBay Best Offer 2012–13 fit transferred to
  factory negotiation (labelled as such in code and docs).
- AMD MI300X numbers are roofline lower-bound estimates, not measured
  runs on AMD Developer Cloud.
- No legal-grade accountability claim: "legally binding" is never written,
  because key-to-identity binding is out of scope.

## Accountability upgrade (delivered 2026-10-21)

- **Ed25519 human approval signatures** (`src/desk/approval_signing.py`):
  the approving human signs `{deal_id, draft_id, body_sha256}` — exactly
  what they reviewed — with a private key that never reaches the server.
  The server verifies and records the signature inside the `send_approved`
  chain entry. With `TRADEFLOW_APPROVAL_PUBKEY` set, unsigned sends are
  refused (fail-closed). Gold tests include: a server holding the HMAC
  chain key still cannot forge one human approval.
- **Git anchoring** (`scripts/anchor_chain_head.py`): records every deal's
  chain head into a committable JSON or an annotated git tag. A git commit
  carries its own hash, author, and timestamp, so a database holder who
  rewrites the whole chain consistently cannot rewrite the committed
  anchor without rewriting public history. One line a day, or cron.

## Next steps

- **The remaining gate on "actually usable" is a live pilot**:
  [docs/PILOT_RUNBOOK.md](docs/PILOT_RUNBOOK.md) — 3 real quotes, one
  supplier, 2 weeks max, pre-committed success criteria. Until it runs,
  every capability claim is configuration- or simulator-level.
- This repository is in **maintenance / consolidation mode**: fixes and
  documentation only, no new modules, until a new competition target
  exists.
- The next competition target is the **AMD AI League**
  (lablab, live until 2026-12-01): Match 5 "AI-Powered Code Repository
  Repair" (Oct 27 – Nov 10) is the planned main entry, as a **separate,
  fresh repository** that reuses this project's audit-chain, constant
  registry, and verification methodology — not this codebase directly.
- Decision lines (pre-committed): Match 5 repo started by 10-27;
  submitted by 11-10 (judged by the lablab confirmation email, not by
  git push). Either missed → return to the audit-engine main line, no
  new tracks.
