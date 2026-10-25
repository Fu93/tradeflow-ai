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

## Verified state (as of 2026-10-24, local run, Windows, after round 4)

| Check | Result |
|---|---|
| `python -m pytest tests -q` (with the CI coverage gate) | 1025 passed, 2 skipped, 112 subtests, 3 failed — **coverage 92.04%** (gate: 90%) |
| `python verify_all.py` | ALL 6 PILLARS PASS |
| `python verify_live.py` | V2 DESK SURFACE VERIFIED END TO END (9 steps) |
| `python scripts/mutation_sanity.py` | 73 mutants killed, none surviving (runs on Windows) |
| `python scripts/check_decision_literals.py` | decision registry ok (184 constants, relocated to the split modules) |
| `python scripts/check_no_fake_literals.py` | no undeclared scenario floats |
| `ruff check` (phase-1 modules + the 8 new app-layer modules) | clean |
| `mypy` (phase-1 modules) | clean |

The 3 failures are all pre-existing and platform- or machine-dependent. They
were re-run in a clean worktree at the previous commit and fail there too:

- `test_round_rss.py` and `test_coupled_properties.py` read `/proc/self/status`.
- `test_desk_phase2.py::test_five_supplier_streaming_budget` asserts a Linux RSS
  ceiling.

A fourth, `test_ood_500_benchmark.py::test_held_out_ood_benchmark_metrics`,
asserts a mean ONNX inference latency under 1.0 ms. This host measures 2.2–2.8 ms
on the *unmodified* previous commit, so the guard is machine-dependent rather
than a regression — it passes when the machine is idle and fails under load. It
is not counted in the 3 above because it is intermittent, not deterministic.

A fifth, `test_mutation_runner_leaves_the_worktree_untouched`, failed on Windows
because staging used `os.symlink`, which needs developer mode. It now falls back
to a copy (round 3 below) and passes here and in CI.

## Round 2 — structural pass (2026-10-23)

The first pass fixed behaviour. This one fixes the shape that made the behaviour
hard to reason about. 48 new regression tests across four files.

### The send gate is a module of its own

`src/desk/service.py` went from 2,237 to 1,710 lines and no longer defines
`_guard_send`, `_envelope`, `publish` or `DeskBus`. The gate is
`src/desk/verify.py` (657 lines) and it **cannot reach back into the service
layer** — it imports `errors`, `snapshot`, `store`, `record`, `recompute`,
`decide` and the engine's landed-cost and reserve types, and nothing else.
`tests/test_structure_round2.py` proves the isolation in a subprocess: the gate
loads in a fresh interpreter with the service layer absent.

Every existing import path still resolves. `service.py` re-exports the moved
names, including the private predicates the step tests import directly, so 116
call sites did not have to move. That is deliberate: breaking them to make a
point about underscores would be a worse trade.

### The schema has a version

`src/desk/migrations.py` reads and writes `PRAGMA user_version`. Before, the
schema was `CREATE TABLE IF NOT EXISTS` plus guarded `ALTER TABLE` — additive
only, recording nothing. A rename, a type change or a backfill could not be
expressed, and a rolled-back deploy left a schema the old code only
half-understood with no way to detect it.

Migration 1 is byte-for-byte the old `_create()` output, so opening an existing
database is a stamp rather than a rebuild. A database written by a **newer**
build is refused (`SchemaTooNew`) instead of being written into by code that
cannot read it — that is the rollback case, and it used to be silent.

Atomicity, stated honestly: `executescript` issues an implicit COMMIT, so a
migration is not one transaction. The guarantee that is actually made is that
`user_version` only advances after the body returns without raising, and every
migration is written to be safe to re-run.

### Writes are rate-limited and every request is traceable

The API had no limit at all: a client could loop
`POST /api/v1/desk/deals/{id}/quotes` and each call runs the extractor, the
belief update and the solver. A sliding window (not a fixed bucket, which lets a
caller send `limit` at each window boundary) keyed on a SHA-256 prefix of the
API key or the client address. 600/min and 6000/hr by default, read from the
environment per check so a test can tighten it without a restart. A missing
config means *limited*, not unlimited. `Retry-After` is set, because a 429
without it invites a retry storm.

It is in-process, so per-instance: a restart clears the window and a second
replica has its own. That is an abuse cap for this process, not a distributed
rate limit, and the module says so rather than implying otherwise.

Every request now carries an id — honoured from an inbound `X-Request-ID` when a
proxy supplies one, otherwise generated, always returned on the response. It is
available through a context variable and a logging filter, and one access line
per request records method, path, status and duration. The query string is
deliberately dropped: it is the part that can carry a token someone pasted into
a URL. A caller-supplied id is validated (short, alphanumeric) so it cannot
inject a header.

### The numerical kernel is written once

`_betacf` existed twice — in `reserve.py` with `BETA_CF_ITERS`/`OPT_TOL`, and in
`legacy/bayesian_v2.py` with 200/3e-14 hardcoded. The duplication was the
defect; the tolerance difference is not. `src/engine/numerics.py` is now the only
implementation, with the precision passed explicitly, and
`tests/test_numerics.py` pins that the two precisions agree to 1e-9 relative.
**No number changes.**

### Configuration is a leaf

`public_payload` moved from `src/config/heuristics.py` to
`src/app/heuristics_view.py`. It used to import `src.engine.hazard_fit` inside
the function body while `hazard_fit` imports `heuristics` at module scope — a
real cycle, hidden by a function-level import and a bare `except`. The
dependency direction is now `app → engine → config` with no edge pointing back
down, and `src/config/heuristics.py` imports nothing from `src` at all.

### The round safety gate is testable

Stage 4 of the round handler — the six-branch decision that blocks a round and
escalates to a person — is now `src/app/round/circuit_breaker.py`, a pure
function with a frozen input dataclass and a `str | None` output. The notify and
the state transition stay in the handler: those are the effects, not the check.
14 tests assert the ordering, which is the contract: no-counter wins over a
security escalation, a security escalation wins over a breakdown trip, and the
engine-above-ceiling branch is last because it should never fire.


## Round 3 — test-debt and platform pass (2026-10-23)

91 new tests across three files, one refactor, one platform fix. No new modules.

### The coverage gate is green again, and the new code is the reason it was red

The signing round landed `approval_signing.py` at 57% covered and dropped the
total to 89.19% — under the 90% CI gate — without breaking a single test. What
was untested was exactly the human-facing half of the accountability story:
the keygen/sign CLI, the operator's policy words, and the malformed-block
branches. Round 3 pins all three (`tests/test_approval_signing_cli.py`): the
CLI round-trips a keygen blob through sign to the server verifier in every
documented key form (hex, keygen JSON, bare-hex file); an unrecognised
`TRADEFLOW_REQUIRE_SIGNED_APPROVAL` word falls through to the environment
default instead of silently downgrading a production gate; and a bad key or
payload file exits with a clean one-line error rather than a traceback in the
human's terminal between review and send.

### The route layer's HTTP contract is pinned

`tests/test_desk_routes_gaps.py` drives every desk mutation route over HTTP:
a well-formed but unknown id is 404 (not 500) on all fourteen read and
confirmation routes, a `DeskRejected` is 422 with the reason, a stale send
digest is 409, poll keeps its channel validation and translates mailbox and
TLS failures into 502, audit verify returns a summary by default with
opt-in paginated entries, and deal creation without a chain key is 503.
`routes.py` went from 79% to 95% covered.

### The audit chain's fail-closed paths are behaviours, not line noise

`tests/test_audit_chain_failclosed.py` pins the paths that only run when
something is already wrong: a key holder who backdates a row (valid MAC,
falsified clock) breaks the chain with `timestamp_regression`; a key id that
is neither current nor published in the retired ring leaves the chain
unverifiable rather than silently accepted; a published retired key still
verifies; legacy key-less rows verify under the round-5 MAC and a tampered
one is broken; and an unreadable anchor counts as present — fail closed.

### One verifier, not two

`verify_signed_approval` and `verify_address_change` were line-for-line
duplicates of ~25 security-critical lines whose docstring claimed "one place
to reason about what a valid signature means". They now delegate to a shared
`_verify_block`, as do both signing functions through a shared `_sign_block`.
Public API unchanged; the rules can no longer drift between send approvals
and address changes.

### The mutation harness runs on a plain Windows box again

Staging used `os.symlink`, which Windows without developer mode refuses
(WinError 1314), so `scripts/mutation_sanity.py` and
`test_mutation_runner_leaves_the_worktree_untouched` failed on Windows with a
raw traceback. `_link_or_copy` falls back to a copy for a throwaway staging
tree — semantically identical, and the worktree-untouched property is
unchanged. The test passes here and in CI.

### One surviving mutant closed a real guardrail bypass

CI had never reached the mutation step after the bilingual needles were added
(the coverage gate failed first), so this round's run was their first, and one
mutant survived: disabling the literal blacklist
(`if word in draft_text:` → `if False:`) changed no test outcome. Reasoning
through it found two facts. First, "換次級料" is the one literal word the
synonym patterns do not cover (`換次料` has no 級), so the literal backstop
has exactly one distinguishing input and no test fed it. Second, and worse:
the backstop scanned the raw text while the synonym scan scans compacted
text, so "換 次 級 料" escaped **both** layers by spacing alone. The fix
compacts once and both layers scan the same text; the distinguishing inputs
are pinned in `TestBypass3ForbiddenPhraseRewrites`, and the harness needle
tracks the new line. `mutation_sanity.py` reports 73 mutants killed, none
surviving.

Net effect: total coverage 89.19% → 92.04%; `approval_signing.py` 57% → 100%;
`routes.py` 79% → 95%; `audit_chain.py` 84% → 89%; the numeric guardrail's
literal backstop is mutation-pinned and spacing-proof.


## Round 4 — the app layer split (2026-10-24, Phase 1 of the upgrade plan)

`src/app/main.py` went from **3,004 lines** to **~470 lines** of assembly.
Every handler, model, and helper moved verbatim into modules beside it, on
the same patterns the codebase already trusted (`register_desk_routes`,
`security.py` cycle-cutting, re-export compatibility):

| Module | Holds |
|---|---|
| `src/app/state.py` | `session_mgr`, the legacy-engine conditional load, `SafeJSONResponse`, the retired solve cache, `_legacy_route`, the session-id path validator, the shared turn/mold-fee helpers |
| `src/app/routes_meta.py` | `/health`, `/ready` |
| `src/app/routes_negotiate.py` | `Spec`, `/negotiate`, `/webhook/evolus`, heuristic views |
| `src/app/routes_command.py` | `_dispatch_supervisor_command`, `/webhook/mobile-command`, `/sessions/{id}/command` |
| `src/app/routes_sessions.py` | sessions CRUD, `ApproveActionRequest`, `/approve` |
| `src/app/routes_round.py` | `ModeEnum`, `RoundActionRequest`, `/round` |
| `src/app/routes_analysis.py` | TCO/Bayes/quote-read/classify endpoints |
| `src/app/routes_auction.py` | `/multi-auction/run` and its bounded request models |

- `main.py` re-exports the whole historical surface (`app`, `session_mgr`,
  `require_api_key`, the request models, `serve_http`, the legacy engine
  names …); every test imports `src.app.main` unchanged.
- `tests/test_app_structure.py` proves in a subprocess that no route module
  can import `src.app.main` (the split is real, not cosmetic), that the
  session manager is one object across `state` and `main`, and that every
  module's routes are actually registered.
- CI: ruff now gates the eight new modules (clean). mypy stays scoped to the
  phase-1 modules for a recorded reason — the `sys.modules` shim
  architecture makes `src.session.*` / `src.engine.*` names statically
  unresolvable, which is why `main.py` was never type-checked; the unblock
  (static stubs for the shim layer) is Phase 4 work.
- Two pre-existing dead-code defects surfaced by the first lint pass over
  this code were removed: the retired solve-cache function referenced an
  undefined `result` below its `raise` (F821), and `/round`'s unreachable
  engine path computed a `demand_mold_waiver` nobody read (F841). The
  unreachable engine path itself is kept and labelled — deleting it is a
  recorded Phase-4 decision, not a refactor accident.
- 13 pre-existing `raise`-inside-`except` sites gained explicit `from`
  chaining (house style since the desk routes), and five over-long lines
  were rewrapped with byte-identical message output.

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
- `src/app/limits.py`, `src/desk/money.py`, `src/desk/errors.py`,
  `src/desk/bus.py`, `src/desk/snapshot.py`, `src/desk/verify.py`,
  `src/desk/migrations.py`, `src/engine/numerics.py`,
  `src/app/heuristics_view.py`, `src/app/observability.py` and
  `src/app/round/circuit_breaker.py` are new, single-purpose modules.
- `GET /ready` is a real readiness probe (chain key, writable store, approval
  policy) and returns 503 with the blocking list. `GET /health` stays a cheap
  liveness probe with its existing body, because callers depend on it.

### Not done (deliberate, with reasons)

What the round-2 pass removed from this list is not repeated here. What remains:

- **`src/app/main.py` is still a god module** (2,997 lines). Its
  `process_negotiation_round` is 877 lines across twelve numbered stages, and
  only stage 4 (the safety gate) has been extracted so far, to
  `src/app/round/circuit_breaker.py`. The other eleven stages share local state
  heavily, so extracting them means threading a state object through the
  handler — a real refactor, not a move, and one that should not share a commit
  with behavioural fixes.
- **`src/desk/service.py` is still large** (1,710 lines, down from 2,237). The
  send gate, the bus, `publish`, the errors and the schema are out; what is left
  is deal CRUD, ingest, the extractor pool and the mailbox poller. Ingest and
  polling are the next candidates.
- **No per-user identity.** The desk is single-tenant behind one shared API key.
  The *send* path and the *address-change* path now require an Ed25519
  signature from a key that lives on a human's machine, so a shared API key is
  no longer sufficient on its own for either. What is still missing is a user
  model: the desk cannot say *which* human signed, only that the pinned key
  did, and there is no per-user revocation. That needs a user model, not a code
  tweak.
- **`src/engine/bilingual.py` (2,035 lines)** is still outside the coverage
  gate and is not split. Its `NumericGuardrailValidator` — the component that
  decides whether a draft contains an unauthorized price — now has 7 mutants and
  36 invariant tests (`tests/test_guardrail_invariants.py`) pinning the four
  bypasses its own docstring lists. What is *not* done is the file's remaining
  ~1,900 lines, which include the intent classifier and the tactical bridge.
- **Rate limiting is in-process**, so per-instance and cleared by a restart. A
  shared counter needs a shared store; adding Redis to a single-instance
  deployment to bound abuse would be a larger operational commitment than the
  problem warrants today.
- **`_betacf` still runs at two precisions** — one kernel, two tolerance
  budgets, in `src/engine/numerics.py`. They are pinned to agree to 1e-9
  relative, so this is a documented choice rather than a defect, but it is still
  two answers where a reader might expect one.


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
- **Supplier address changes are signed too** (same key, same policy
  resolution, `TRADEFLOW_REQUIRE_ADDRESS_SIGNATURE`). This closed the
  business-email-compromise path: an address change redirects where the next
  counter is sent, and the desk has one shared API key, so
  `human_confirmed: true` was reachable by anyone holding it. The signature
  covers `{deal_id, supplier_id, address}` — the address included, or a signer
  would be approving "a change" while an attacker chose "where to" — and it is
  recorded inside the `supplier_address` chain entry, so it is tamper-evident
  rather than merely checked once at the door. CLI:
  `python -m src.desk.approval_signing sign-address --key <key> --deal <id>
  --supplier <id> --address <email>`. Sandbox without a pinned key keeps the
  old boolean path, so existing callers are unaffected.
- **Git anchoring** (`scripts/anchor_chain_head.py`): records every deal's
  chain head into a committable JSON or an annotated git tag. A git commit
  carries its own hash, author, and timestamp, so a database holder who
  rewrites the whole chain consistently cannot rewrite the committed
  anchor without rewriting public history. One line a day, or cron.

## Phase 2 — named operator keys and the chain actor (2026-10-24)

The desk had exactly one credential — `TRADEFLOW_API_KEY`, shared by every
buyer — so revoking one person meant rotating the key for everyone, and the
audit chain could prove *that* a human approved but not *which* human.

- **Named keys** (`src/desk/users.py`, schema version 2, table `desk_users`):
  issued via `scripts/manage_users.py issue --label …`; the raw secret is
  printed exactly once and stored only as a SHA-256 hash plus a 12-character
  display prefix. `revoke --id N` takes effect on the next request without
  touching anyone else's access; `list` shows no secret material.
- **Authentication** (`require_desk_mutation`, and `require_api_key` on the
  same surface): the shared env key works exactly as before and records the
  actor `env-key` — it is shared, so it names nobody. A named key is a peer
  credential and records `user:<id>:<label>`. A revoked key is refused
  exactly like an unknown one, so revocation cannot be probed by comparing
  error messages. Production's fail-closed 500 without a configured env key
  is unchanged; named keys add a way in, they do not open one.
- **The chain records the actor**: `DeskStore.append_chain` stamps the
  authenticated actor inside the signed payload when a request is behind the
  write, so the keyed MAC makes the attribution as tamper-evident as the
  rest of the entry. Writes with no request behind them (the mailbox
  poller, CLI tools) omit the field, keeping every pre-existing entry shape.
- Tests: `tests/test_desk_users.py` (matrix, actor attribution, storage
  hygiene, CLI round-trip); `tests/test_migrations.py` now expects the
  version-2 transition. `.env.example` documents the surface.

## Phase 3 — deployment hardening (2026-10-24)

The compose volume (`desk-data:/app/data`) keeps the desk database and the
audit chain across container rebuilds; `docker compose down -v` still
destroys it, which is why the volume is not called a backup.
`scripts/backup_desk_db.py` takes an online backup with SQLite's backup API,
then verifies every deal's chain in the copy — a broken backup is deleted,
not left behind looking reliable — and `restore --yes` re-verifies after
copying. `GET /version` reports the build (app version, schema version,
`TRADEFLOW_GIT_SHA`) with no secret material. `docs/deploy.md` gained the
backup schedule, the restore drill, the daily anchor cron, and the honest
Render note: the starter plan's disk is ephemeral, persistence there needs a
paid instance type.

## Deep-debug pass (2026-10-24, after the five phases)

Two adversarial audits (web/middleware surface; concurrency/mailbox surface)
found fifteen issues. Eleven were real and are fixed, each pinned by
`tests/test_deep_debug_2026_10_24.py`:

- **HIGH: .env newline injection** — a telemetry value carrying a newline
  could mint a brand-new key into `.env`, and dotenv's last-occurrence-wins
  made that a credential takeover at next start. Values are now sanitized to
  one printable line (routes_web.py).
- **HIGH: unbounded limiter memory** — every request with a fresh random
  `X-API-Key` inserted a permanent entry before auth ran; the pruning claim
  in the docstring was false. Both limiters now sweep aged keys and evict
  least-recently-active ones past a hard cap (rate_limit.py); a legitimate
  key's budget survives the sweep (pinned).
- **HIGH: unauthenticated SSE** — `/api/v1/stream/session` streamed target
  prices, walk-away ceilings and posteriors to anyone who could reach the
  port. It now carries `require_api_key` (sandbox demo unaffected:
  fail-open there; production fail-closed), the audit exemption set is
  empty, and error frames carry the exception type, not the message.
- **Mailbox poison isolation** — one unparseable email (unknown charset
  raised LookupError) stalled every future poll behind it, forever. Parse
  failures are now per-message: skipped, counted, marked seen for every
  deal; unknown charsets fall back to utf-8; bodies are capped at the same
  20,000 chars as the paste API (mailbox.py, service.py).
- **Provenance forgery closed** — a supplier typing `X-TradeFlow-Fixture`
  into a real email had it stored as fixture origin in the audit chain. The
  marker is now honoured only from channels this process controls: paste,
  and the loopback non-TLS fixture mailbox (IMAP_SSL=0 is refused for any
  other host). Remote mailboxes and Gmail classify live (service.py,
  classify_origin trusted_channel).
- **Operator hygiene** — named-key labels are stripped of control
  characters and capped at 64 (no ANSI injection into `manage_users list`,
  no forged lines inside chain actor strings); the access-log path is
  cleaned path-shaped (a `%0A` in the request target can no longer forge
  log lines); `telemetry/test-push` answers 400 to a malformed body and
  caps the message; the static XSS scan now covers desk.js and
  market_intel.js (38 checks, clean).
- Verified safe by the same audits (no change needed): the set_policy
  f-string SQL is a closed key set with parameterised values; the deal-lock
  discipline is sound; named-key lookup leaks nothing by timing; the
  address regexes have no catastrophic backtracking; BodySizeLimit counts
  chunked bodies correctly; desk.js/app.js escaping holds.

Local battery after the pass: 1081 passed / 3 documented Windows platform
failures, coverage 91% (gate 90%), verify_all six pillars, mutation 73
killed / 0 survived, ruff + mypy (37 files) clean, decision registry ok.

## Product-grade construction pass (2026-10-24, after the debug pass)

Three constructions, one per axis, each behind the full battery:

- **Logic**: the unreachable engine paths behind the "A posted price is not
  a confirmation" gates in /round and /negotiate are gone — 843 lines of
  code that could never execute (the raise IS the behaviour; deleting it
  was the recorded Phase-4 decision). routes_round.py 1,001 → 218 lines,
  routes_negotiate.py 209 → 138. Three registry rows followed the deleted
  lines; one live row was kept.
- **Performance**: the SSE session stream ran synchronous SQLite
  (`list_sessions`) on the event loop every second, so N open dashboards
  blocked every request N times a second; the read now runs in a worker
  thread (`asyncio.to_thread`).
- **Architecture**: service.py is 1,102 lines (was 1,782). The intake
  pipeline — classify_origin, ingest_message, the extractor pool, the
  mailbox pollers, the per-deal lock table — moved verbatim to
  `src/desk/ingest.py` (830 lines) on the verify.py pattern: service.py
  re-exports every moved name, the module graph stays acyclic (one
  documented lazy import for Gmail reply drafting), and the mutation
  harness needle followed the code.

Battery: 1083 passed / 3 documented Windows platform failures, coverage
91% (gate 90%), verify_all six pillars, verify_live end-to-end, mutation
73 killed / 0 survived, ruff + mypy (38 files) clean, decision registry ok.

## Gap pass: capabilities that were specified but never built (2026-10-25)

Audited the repo for "declared but not actually built": PR#15 (the CNY
unit-basis reading feature, drafted 2026-10-20, 38 files, never merged —
master had moved too far for a textual merge) was probed case by case
against its own contract. Four real gaps, two of them correctness defects:

- **Fee lines poisoned the single-price check** (correctness noise): "运费
  3 元，单价 20 元" read as "more than one price" because the fee amount
  survived in `competing`. A fee amount now leaves candidacy entirely; the
  one goods price reads. The corpus rows that pinned the old behaviour were
  updated with the contract cited.
- **A non-piece basis could confirm as per-piece** (correctness defect):
  "单价 20 元/打" accepted 20 as the per-piece price — a 12x overpay on
  confirmation. Measure words (打/吨/克/码/米/张/片…) now mark the amount a
  unit-basis mismatch: review, no prefill, person decides.
- **Surcharge words read as the goods price** (correctness defect): "加价
  2 元每件" accepted 2. 加价/加收/差价 joined the delta vocabulary.
- **References were confirmable**: "我们上周买的单价是 12 元" (past
  purchase) accepted as an offer. Past/rival/market cues now label the
  amount a reference; the supplier's own price in the same message still
  reads.
- **Minor currency units** (分/毛/角/厘): deliberately NOT auto-converted —
  the divergence from PR#15 is documented in the tests: silent scaling
  inside the confirm span risks a 10x/100x error at approval. They gate to
  review with an explicit reason.

Pinned by `tests/test_quote_reading_gaps.py` (25 tests).

Also closed: the `/version` build stamp the docs promised but no pipeline
performed — the Dockerfile takes `--build-arg GIT_SHA` and compose forwards
`GIT_SHA` (unstamped builds say "unknown", honestly); `/ready` now refuses
a database whose schema is newer than the build (the rollback case
migrations refuse to touch); the retired solve-cache table and two dead
imports left the tree.

Battery: 1108 passed / 3 documented Windows platform failures, coverage
gate passed (90% floor), verify_all six pillars, mutation 73 killed /
0 survived, ruff + mypy clean, decision registry ok.

## Next steps

- **The remaining gate on "actually usable" is a live pilot**:
  [docs/PILOT_RUNBOOK.md](docs/PILOT_RUNBOOK.md) — 3 real quotes, one
  supplier, 2 weeks max, pre-committed success criteria. Until it runs,
  every capability claim is configuration- or simulator-level.
- **[Decision 2026-10-24, owner-approved] The maintenance-mode constraint
  ("no new modules") is lifted.** This repository is now in
  **productization-upgrade mode**, governed by
  [docs/internal/UPGRADE_PLAN_2026-10-24.md](docs/internal/UPGRADE_PLAN_2026-10-24.md):
  five phases (architecture split → multi-user identity → deployment
  hardening → test-scope expansion → pilot readiness), each landing behind
  the full verification battery. Phase 1 (the `main.py` split) is done —
  see the Round 4 section below. The AMD AI League Match 5 entry stays a
  separate fresh repository; that decision line is unchanged.
- The next competition target is the **AMD AI League**
  (lablab, live until 2026-12-01): Match 5 "AI-Powered Code Repository
  Repair" (Oct 27 – Nov 10) is the planned main entry, as a **separate,
  fresh repository** that reuses this project's audit-chain, constant
  registry, and verification methodology — not this codebase directly.
- Decision lines (pre-committed): Match 5 repo started by 10-27;
  submitted by 11-10 (judged by the lablab confirmation email, not by
  git push). Either missed → return to the audit-engine main line, no
  new tracks.
