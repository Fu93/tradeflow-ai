# Architecture: two generations, one map

Read this before touching `src/`. It explains a naming situation that
otherwise looks wrong.

## The naming inversion (read twice)

**`src/legacy/` is the active v1 implementation, not dead code.**
**`src/engine/` re-exports it.** Ten modules in `src/engine/` are
one-line compatibility shims:

```python
"""Compatibility import. The implementation lives in src.legacy.solver."""
import src.legacy.solver as _impl
sys.modules[__name__] = _impl
```

History: the negotiation engines were first built under `src/engine/`,
later moved to `src/legacy/` when the v2 architecture landed
(2026-10-20, commit 9ab8edc), leaving shims behind so every existing
import path kept working. The move was never finished: v1 code was never
renamed back and v1 kept receiving fixes inside `src/legacy/`.

**Decision (2026-10-21): freeze, do not rename.** Physically moving
~4,900 lines back to `src/engine/` would be a large diff with zero
behavioral value, and this repository is in maintenance mode — the next
competition entry is a fresh repo. Instead, this file is the authority
that resolves the confusion.

**Correction (2026-10-23): the shims are no longer part of v1's own graph.**
The freeze decision had a defect the first version of this file did not
name: `src/legacy/coupled.py`, `multi_solver.py`, `one_step.py`,
`orchestrator.py`, and `proposal.py` imported their own sibling modules
*through* the `src.engine` shims, which point back at `src.legacy`. That made
the compatibility layer a load-bearing dependency inside the code it was meant
to protect, and it meant the shim's resolution semantics were part of v1's
contract. Those imports now point at `src.legacy.*` directly. The remaining
`src.engine` imports inside `src/legacy/` are all real modules (`decision`,
`hazard_fit`, `reserve`, `backend`, `landed`), which is the intended direction:
v1 may call the v2 decision core, and must not call itself through a shim.

The shims still exist and still serve external callers. They are now a
compatibility surface only, not an internal dependency, which is what makes
"freeze" a stable end state rather than a latent liability.


## What lives where

| Path | Role |
|---|---|
| `src/legacy/` | v1 negotiation engines (bayesian belief, hazard, coupled solver, multi-supplier auction, logrolling, robustness). **Active v1 implementation.** |
| `src/app/state.py` | [2026-10-24] Shared app-layer process state: `session_mgr`, the legacy-engine conditional load, `SafeJSONResponse`, the retired solve cache, `_legacy_route`, the session-id path validator, shared turn helpers. Route modules import this, never `main`. |
| `src/app/routes_meta.py` … `routes_auction.py` | [2026-10-24] The former `main.py` handlers, verbatim, grouped per surface (meta probes / v1 negotiate+webhook / supervisor commands / sessions+approve / round / analysis / auction). Each exposes `register_x_routes(...)`; `main.py` calls them in the original order and re-exports the historical names, so `from src.app.main import …` keeps working. `tests/test_app_structure.py` proves the route modules cannot import `main` back. |
| `src/engine/<name>.py` (10 files) | Compatibility shims → `src/legacy/` |
| `src/engine/decision.py`, `reserve.py`, `hazard_fit.py` | v2 phase-1 verified decision core (real files, no shims) |
| `src/desk/` | v2 phase-1 persistent live desk: quote intake (IMAP/Gmail/chat import) → SQLite → belief update → coupled solver; drafts approved by a human; HMAC-SHA256 keyed audit chain (`audit_chain.py`) |
| `src/desk/ingest.py` | [2026-10-24] Quote intake: parse → provenance → chain event → extractor scheduling, the IMAP/Gmail pollers, and the per-deal lock table. Extracted verbatim from `service.py` (1,782 → 1,102 lines); `service.py` re-exports every moved name. Imports `store`/`mailbox`/`snapshot`; never `service` (one lazy call-site import for Gmail reply drafting, documented in place). |
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
2. Bug fixes in v1 behavior stay in `src/legacy/` (the shim makes them
   visible through `src.engine` automatically).
3. Do not "clean up" the shims by deleting `src/legacy/` — that deletes
   the implementation.
4. CI treats `src/desk` + the three v2 core files as phase-1 modules
   (ruff, mypy, 90% coverage gate). `src/legacy/` is covered by the full
   pytest suite but not by the phase-1 quality gates.
