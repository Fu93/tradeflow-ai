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

## What lives where

| Path | Role |
|---|---|
| `src/legacy/` | v1 negotiation engines (bayesian belief, hazard, coupled solver, multi-supplier auction, logrolling, robustness). **Active v1 implementation.** |
| `src/engine/<name>.py` (10 files) | Compatibility shims → `src/legacy/` |
| `src/engine/decision.py`, `reserve.py`, `hazard_fit.py` | v2 phase-1 verified decision core (real files, no shims) |
| `src/desk/` | v2 phase-1 persistent live desk: quote intake (IMAP/Gmail/chat import) → SQLite → belief update → coupled solver; drafts approved by a human; HMAC-SHA256 keyed audit chain (`audit_chain.py`) |
| `src/app/` | App entry, demo data, security primitives |
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
