# Pilot runbook: the first real desk session

> Goal: turn "No factory has closed a deal through this prototype" into
> "one supplier thread ran end to end on the real desk" — or an honest
> record of exactly where it stopped.
> Pre-committed scope: **3 real quotes, one supplier, 2 weeks max.**
> Success is a working desk, not a cheaper price.

## Why this exists

Automated validation now includes actual Docker resource limits, backup/restore and container recreation, signed approvals in a real browser, and independent financial/numerical checks. Real mailbox and supplier delivery remain unverified. Start with [the owner handoff](OWNER_HANDOFF.md); this pilot measures the external-account workflow, not a promised saving.

## Day 0 — setup

First configure and start the service following OWNER_HANDOFF. The configuration checker inspects its process environment; a host-side invocation does not automatically load `.env`. Preserve an existing private key instead of generating a replacement.

```bash
docker compose exec -T tradeflow python scripts/check_live_readiness.py --intake imap --probe-db --connect
python -m src.desk.approval_signing keygen --out approval_keypair.json # create once on YOUR computer
docker compose exec -T tradeflow python scripts/anchor_chain_head.py --out /app/data/anchors/pilot-day-0.json
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
   chain with your signature; export the anchor and keep a trusted copy outside the service host.
6. Record the outcome when the supplier replies (accept / counter /
   walk). `POST /desk/deals/{id}/suppliers/{id}/outcome`.

## Every day

```bash
docker compose exec -T tradeflow python scripts/anchor_chain_head.py --out /app/data/anchors/pilot-YYYY-MM-DD.json
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
| Integrity | chain verifies; trusted off-host anchor exports exist for every pilot day |

Not required for success: closing a cheaper deal. The pilot proves the
desk works on real mail; the price quality is the next experiment and
needs more than three rounds to say anything.

## Configuration gate and live pilot are separate

The checker reports CONFIGURATION OK or CONFIGURATION INCOMPLETE, not a live-delivery certification. It rejects incomplete selected IMAP/Gmail credentials, incomplete enabled SMTP credentials, unusable keys, malformed approval keys and invalid ports. Manual intake can be selected without a mailbox.

`--probe-db` verifies an existing database and a rolled-back write; it never creates a replacement database to make a wrong path look healthy. `--connect` enables TLS login-only probes, without selecting the inbox or transmitting a message. SMTP authentication is not recipient delivery.

Follow OWNER_HANDOFF for the signed-payload download, local signing and signature-file import. Keep private keys on the operator computer. Copy anchor exports off the service host, together with the maintenance backup; a file beside the database is not independent tamper evidence.
