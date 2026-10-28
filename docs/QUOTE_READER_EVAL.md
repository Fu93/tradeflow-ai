# Quote-reader evaluation on a held-out set

This measures how well the desk reads a supplier's unit price. It compares the deterministic rules, each Nemotron model on Nebius Token Factory alone, and the desk's gate, which needs both to agree.

## The set

[`data/eval/quote_reader_holdout.jsonl`](../data/eval/quote_reader_holdout.jsonl) has 40 short supplier messages in Simplified Chinese, Traditional Chinese, English and mixed language. They are original sentences written for this test, not real supplier mail.

- 22 messages carry one current CNY unit offer from the supplier (`own_offer`).
- 18 must be left for a person (`own_offer: null`): tier tables, ranges, questions, refusals, other currencies, fees and deposits alone, forwarded old prices, conditional prices, two products, and two prompt-injection attempts.

The set was written on 2026-10-28, before any model was run on it. Its SHA-256 is recorded in every output file. The labels are our reading of each message, not a supplier's confirmation.

This is a different set from `tests/fixtures/quote_corpus.py`. The rules were tuned on that corpus and score perfectly on it, so it cannot measure them fairly.

## Scoring

Every answer is compared with `own_offer`:

| Outcome | Meaning |
|---|---|
| Correct | the right amount |
| Wrong | an amount that is not the label (the costly error) |
| Missed | no amount where the label has one; the message goes to review |
| Held | no amount where the label is null; correctly left for a person |

Every reader is limited to the desk's unit-sanity band around the deal's target and ceiling. The script defaults to a target of 30 and a ceiling of 40.

- **The model never supplies a number.** For "model alone", code reads the amount from the one span the model labels as the supplier's own unit offer. A number field in the model's answer is discarded, as it is on the desk.
- **The gate** is the desk's shadow gate. It accepts only when the rules and that span agree.

## Results so far: rules only (2026-10-28)

[`results/quote_reader_eval_rules_only.json`](../results/quote_reader_eval_rules_only.json), from `python scripts/eval_quote_reader.py --offline`:

| Reader | Correct | Wrong | Missed | Held for a person |
|---|---|---|---|---|
| rules | 14 | 2 | 7 | 17 |

**The two wrong reads are parser defects that this held-out set found:**

- `zh_fullwidth_digits`: full-width digits (`２８．５０元`) were read as 50.
- `zh_forward_old`: a price under a Chinese forward marker (`—— 转发 ——`) was taken as the current offer.

**The seven misses all went to review, which is the safe outcome:**

- colloquial 块
- a rival's price before the supplier's own (Chinese and English)
- 每把 before the number
- the buyer's figure echoed before the supplier's own
- a price derived from a discount
- a WeChat-style message split over several lines

We have not fixed either defect yet. Fixing the parser after seeing this set would stop it being held-out for the rules, so the fix waits until the Nemotron run below is recorded, and both results will be kept.

## A change to the reader before the Nemotron run (2026-10-28)

The first live call of the demo read the sample message (a rival's 24 and the supplier's own 27.80). Nemotron Super labelled the 24 correctly as a rival's price, but labelled the supplier's own 27.80 as `our_price_echo`. The rules then sent the quote to review because it holds two prices. No draft was produced.

The reader's prompt had named the classes without saying what each one means. It now defines every class in general terms, and the prompt hash changes with it. (Result files record that hash from 2026-10-28 20:00 UTC on. The Nemotron run below predates that field; how it is pinned is stated there.)

That sample message is close to two held-out items, `zh_rival_then_own` and `en_rival_then_own`. Those two are therefore no longer clean for the model, and a report should list them apart. The other 38 items were not looked at for this change. The rules were not changed.

## Results: Nemotron Super on the held-out set (2026-10-28)

[`results/quote_reader_eval.json`](../results/quote_reader_eval.json) is one run of `nvidia/nemotron-3-super-120b-a12b` on Nebius Token Factory, started by the author at 19:32 UTC.

- It ran after the prompt change above and before either parser fix.
- The dataset SHA-256 in the file (`cffb50ff…93cdc`) is the set's.
- All 40 messages were answered. No call failed. No answer carried a number field.
- The file does not record the prompt hash; that field was added after this run. The reader prompt has not changed since PR #31 (`git log` on `src/desk/extract.py`); its hash today is `e385213e…d7ddd`.
- The rules column was re-run locally and matches the file message for message. Every outcome in the file was re-scored against the labels, and all match.

**This is a small held-out test, not a benchmark.** It covers 40 messages, 22 of them with a labelled offer. It is one run of one model, and the labels are the author's reading.

| Reader | Correct | Wrong | Missed | Held for a person |
|---|---|---|---|---|
| rules | 14 | 2 | 7 | 17 |
| Nemotron Super alone | 20 | 2 | 2 | 16 |
| gate (rules and Super agree) | 14 | 1 | 8 | 17 |

Without the two exposed items (38 messages):

| Reader | Correct | Wrong | Missed | Held for a person |
|---|---|---|---|---|
| rules | 14 | 2 | 5 | 17 |
| Nemotron Super alone | 18 | 2 | 2 | 16 |
| gate | 14 | 1 | 6 | 17 |

One call per message took a median of 3.9 s and at most 8.3 s. The run used 14,155 prompt tokens and 18,947 completion tokens in total.

### Where the readers differ

| Item | Label | Rules | Super alone | Gate |
|---|---|---|---|---|
| `zh_kuai_colloquial` | 27 | missed | **27, correct** | missed |
| `zh_rival_then_own` (exposed) | 27.80 | missed | **27.80, correct** | missed |
| `en_rival_then_own` (exposed) | 26.00 | missed | **26.00, correct** | missed |
| `zh_per_umbrella_logo` | 32 | missed | **32, correct** | missed |
| `zh_buyer_echo_then_own` | 25.50 | missed | **25.50, correct** | missed |
| `zh_wechat_lines` | 26 | missed | **26, correct** | missed |
| `zh_fullwidth_digits` | 28.50 | 50, wrong | missed | missed |
| `en_no_currency` | for a person | held | 27.5, wrong | held |
| `zh_forward_old` | for a person | 31.00, wrong | 31.00, wrong | **31.00, wrong** |

### What this does and does not show

- **Super alone read six offers the rules left for a person**, four of them outside the exposed pair: colloquial 块, 每把 before the number, the buyer's figure echoed first, and a WeChat message over several lines. It made as many wrong reads as the rules (two), but mostly different ones.
- **The gate's one wrong read is a shared blind spot.** In `zh_forward_old`, both the rules and the model took the forwarded 31.00 as the current offer, so agreement did not help. The desk still asks a person to confirm. The parser fix in PR #33 makes the rules hold this message, and the gate can only accept what the rules accept, so it holds it too.
- **The gate is conservative by design.** It can only accept what the rules accept, so it cannot be right more often than the rules. Here it is right 14 times with one wrong read, where the rules alone had two.
- **"Super alone" here is looser than the desk.** This script reads the amount from any span the model calls the supplier's own offer. On the desk, the model's pick must also pass the gate's currency and per-piece tests (PR #35). So `Best price 27.5 per piece.` (`en_no_currency`), which has no currency, would not be pre-selected there. How often the desk's stricter test keeps the six correct reads was not measured.
- **On the desk the model is a second opinion.** In four of the six messages Super read correctly, the rules pre-selected no figure; the model's figure fills that gap when it passes those tests. In the two exposed messages, the rules already pre-selected the right figure and the model agrees. A person confirms every figure, and the gate never accepts on its own (`auto_accept` stays off).

## How to run it

This needs a Nebius Token Factory key. The development workspace cannot reach Token Factory.

```bash
export TRADEFLOW_LLM_API_KEY=...   # never commit it
python scripts/eval_quote_reader.py
# or pick models:
python scripts/eval_quote_reader.py --models nvidia/nemotron-3-super-120b-a12b
```

The run writes `results/quote_reader_eval.json`. For each model it records:

- the four outcomes for the model alone and for the gate
- per-message latency, with the median and maximum
- prompt and completion tokens
- how many answers carried a number field that was discarded

To estimate cost, multiply the token totals by the published Token Factory prices on the day of the run. No price is hard-coded here.

**Before the full run, try three messages.** `python scripts/eval_quote_reader.py --models nvidia/nemotron-3-super-120b-a12b --limit 3` scores the first three messages and writes `results/quote_reader_eval_smoke.json`, never the full result file. Its note says it is a smoke test, not a result.

**While it runs**, each message prints a line: its id, the call status, the latency, and the outcome for the model alone and for the gate.

**If the key is refused or the credit runs out** (HTTP 401, 402 or 403), the run stops at that message instead of failing the rest. What finished is written with `"complete": false` and the reason, and the script exits with status 3. A partial file is not an evaluation result and should not be quoted as one. Interrupting with Ctrl-C does the same.
