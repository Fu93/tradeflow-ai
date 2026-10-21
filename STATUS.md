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

## Verified state (as of 2026-10-21, local run)

| Check | Result |
|---|---|
| `python -m pytest tests -q` | 764 passed, 6 skipped, 112 subtests |
| `python verify_all.py` | ALL 6 PILLARS PASS |
| Frozen-reserve wiring regression | covered by `tests/test_explicit_suppliers_frozen_reserves.py` |

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
