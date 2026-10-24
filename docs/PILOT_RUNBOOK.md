# Pilot runbook: the first real desk session

> Goal: turn "No factory has closed a deal through this prototype" into
> "one supplier thread ran end to end on the real desk" — or an honest
> record of exactly where it stopped.
> Pre-committed scope: **3 real quotes, one supplier, 2 weeks max.**
> Success is a working desk, not a cheaper price.

## Why this exists

Everything verified so far is configuration-level (`scripts/check_live_readiness.py`)
or simulator-level (`verify_all.py`, `verify_live.py`, the 787-test suite).
The only claim still unbacked is "a person can actually use this against
a real supplier". This runbook is the smallest experiment that backs it.

## Day 0 — setup (about 30 minutes)

```bash
python scripts/check_live_readiness.py          # must print LIVE-READY
python -m src.desk.approval_signing keygen      # private key stays on YOUR machine
python scripts/anchor_chain_head.py --tag pilot-day-0
```

Mail in: a Gmail account with an app password (IMAP), or any IMAP
mailbox. Mail out: SMTP for the same account, `DESK_SMTP_SEND=1`.
Set `TRADEFLOW_APPROVAL_PUBKEY` — unsigned sends are then refused, which
is the point: your approvals become non-repudiable.

## The loop — one round per real quote

1. The supplier's email lands; the desk ingests it (IMAP poll or paste).
2. **You** read the message and confirm the unit-price span on the desk.
   One click is one real labelled training example (see the flywheel below).
3. Read the counter card: the push, the walk-away risk, the belief.
4. Disagree if you disagree — the human is the boss, the engine is a
   suggestion. Note WHY you disagreed; that note is pilot data.
5. Approve the draft with your Ed25519 key. The send is recorded in the
   chain with your signature; the anchor script pins the head into git.
6. Record the outcome when the supplier replies (accept / counter /
   walk). `POST /desk/deals/{id}/suppliers/{id}/outcome`.

## Every day

```bash
python scripts/anchor_chain_head.py --tag pilot-$(date +%F)
```

## After the loop — the flywheel and the ledger

```bash
python scripts/export_confirmed_spans.py        # real labelled data, git-ignored
```

- Feed the JSONL to the next extractor training round; the held-out
  number stops being a synthetic-set figure the moment there is real data.
- The recorded outcomes are the first rows of a **hazard calibration
  set**: LIMITATIONS section 13.8 says the eBay transfer costs at most
  ~0.04/unit if factories walk twice as readily — the pilot's outcome
  rows are what turns that bound into a measurement.
- Write the honest ledger: every round, what the engine suggested, what
  you sent, what came back. Divergences are the finding, not a failure.

## Stop conditions

- 3 quotes processed, or 14 days elapsed, or the supplier walks —
  whichever comes first. Then write the outcome into STATUS.md and stop.

## Pre-committed success criteria

| Criterion | Met when |
|---|---|
| Real thread | ≥1 real supplier, real mailbox, no paste fallback on the last round |
| Real confirmations | ≥3 human-confirmed spans exported |
| Real send | ≥1 counter approved with the Ed25519 key and transmitted |
| Real outcome | supplier reply recorded as an outcome row |
| Integrity | chain verifies; anchor tags exist for every pilot day |

Not required for success: closing a cheaper deal. The pilot proves the
desk works on real mail; the price quality is the next experiment and
needs more than three rounds to say anything.

## Getting the readiness check to LIVE-READY (2026-10-24)

`scripts/check_live_readiness.py` fails on exactly three rows, and all three
are operator credentials no code change can supply:

1. `TRADEFLOW_API_KEY` — 16+ characters, `.env` line: `TRADEFLOW_API_KEY=<generate one>`.
2. `TRADEFLOW_CHAIN_KEY` — the HMAC key of the audit chain; desk sends refuse
   to start without it. Also consider `TRADEFLOW_APPROVAL_PUBKEY` (the
   signed-approval gate) and named operator keys
   (`scripts/manage_users.py issue --label …`, Phase 2).
3. A mail intake channel — either `IMAP_HOST/PORT/USERNAME/PASSWORD/SSL=1`
   (a dedicated app password, TLS on) or the Gmail API trio with
   `GMAIL_DRAFTS=1`. The desk reads; a human always sends.

`DESK_SMTP_SEND=1` plus the SMTP variables is what turns approved sends into
transmitted mail; leaving it unset keeps sends recorded-but-not-transmitted,
which is the correct posture for the first week of the pilot.

Everything else the check prints — persistence, provenance notes — is
informational. The verdict line counts only the `[FAIL]` rows.
