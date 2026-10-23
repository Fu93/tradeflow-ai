# Architecture: two generations, one map

Two generations of the negotiation code coexist. This page says where each
one lives and which way the dependencies point.

## v1 and v2

- **v2 (current):** the persistent desk in `src/desk/` and the verified
  decision core in `src/engine/` (`decision.py`, `reserve.py`,
  `hazard_fit.py`, `numerics.py`). This is where new negotiation logic goes.
- **v1 (legacy):** the original negotiation engines in `src/legacy/`
  (belief update, hazard, coupled solver, multi-supplier auction,
  logrolling, robustness) plus the v1 session store
  (`src/legacy/session/`) and orchestrator. They still run the CLI and the
  `/round` and `/negotiate` routes, which are mounted only when
  `TRADEFLOW_LEGACY=1`.

Dependencies point one way: `src/legacy/` may call the v2 core
(`src.engine.decision`, `reserve`, `hazard_fit`, `backend`, `landed`), and
the v2 decision modules and the desk must not import `src.legacy`
(enforced by `tests/test_discipline_invariants.py`).

### History of the layout

The v1 engines were first written under `src/engine/` and moved to
`src/legacy/` when the v2 architecture landed (2026-10-20, commit 9ab8edc).
The move left ten one-line `sys.modules` shims in `src/engine/`, the
`src/session/` package and one at `src/app/orchestrator.py`, so a path like
`src/engine/solver.py` named a six-line alias while the code lived elsewhere.
On 2026-10-24 the shims were removed and every caller now imports
`src.legacy.*` by name. A test fails if a module aliases itself again.

Removing them also exposed two source-text tests
(`tests/test_coupled_properties.py`, `tests/test_market_data.py`) that read
`src/engine/coupled.py`, `solver.py` and `multi_solver.py` to assert that the
solvers never read Evolus or market data. Those files were the shims, so the
assertions were checking an empty alias. They now read the real
`src/legacy/` sources, and the invariants still hold.

## What lives where

| Path | Role |
|---|---|
| `src/legacy/` | v1 negotiation engines (bayesian belief, hazard, coupled solver, multi-supplier auction, logrolling, robustness), the v1 session store (`session/`) and orchestrator. Mounted on HTTP only with `TRADEFLOW_LEGACY=1`. |
| `src/engine/decision.py`, `reserve.py`, `hazard_fit.py` | v2 phase-1 verified decision core |
| `src/desk/` | v2 phase-1 persistent live desk: quote intake (IMAP/Gmail/chat import) → SQLite → belief update → coupled solver; drafts approved by a human; HMAC-SHA256 keyed audit chain (`audit_chain.py`) |
| `src/desk/verify.py` | The send gate: every predicate a draft must pass before mail leaves. Imports `errors`, `snapshot`, `store`, `record`, `recompute`, `decide`; never `service`. |
| `src/desk/migrations.py` | Numbered schema migrations + `PRAGMA user_version`. The only place DDL lives. |
| `src/desk/errors.py` | `SendRefused`, `DraftStale`. Below everything, so a caller can catch them without the service layer. |
| `src/desk/bus.py` | In-process snapshot fan-out for the event stream. |
| `src/desk/snapshot.py` | `publish()` — recompute + store + fan out. |
| `src/desk/money.py` | The single money-quantisation choke point (`quantise`, `quantise_offer`, `to_minor`). Nothing else rounds a price. |
| `src/engine/numerics.py` | The shared incomplete-beta continued fraction. One implementation, precision passed explicitly. |
| `src/app/security.py` | Authentication gate primitives (API key, webhook token). |
| `src/app/limits.py` | Request-body size limit, enforced before the body is buffered. |
| `src/app/observability.py` | Request id, context variable, access log. |
| `src/app/rate_limit.py` | AMD cost limiter + the write-path sliding window. |
| `src/app/heuristics_view.py` | The documented-constants view. Above both config and engine. |
| `src/app/round/` | Extracted pure stages of the round handler. Currently `circuit_breaker`. |
| `src/web/`, `extension/` | Dashboard and Chrome extension (v1 demo surface) |
| `src/sim/` | Labelled simulator (never imported by production decision paths) |

## Rules for future changes

1. New negotiation logic → v2 core files (`src/engine/decision.py` & co),
   never into `src/legacy/`.
2. Bug fixes in v1 behavior go in `src/legacy/`. Import v1 code as
   `src.legacy.<module>`; do not add aliases or re-exports elsewhere.
3. `src/legacy/` is live code for the CLI and the legacy routes. Delete it
   only together with those entry points.
4. CI treats `src/desk` + the three v2 core files as phase-1 modules
   (ruff, mypy, 90% coverage gate). `src/legacy/` is covered by the full
   pytest suite but not by the phase-1 quality gates.
