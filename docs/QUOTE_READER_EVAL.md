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

## The two parser defects, fixed after the baseline

Both are fixed in `src/desk/parse.py` and `src/desk/span_read.py`, with tests in `tests/test_parse_fullwidth_and_forward.py`:

- A full-width full stop is now a decimal mark, so `２８．５０元` reads as 28.50. The token stays as written, so a person's confirmation still quotes the message.
- A dash rule around 转发 or 轉發 now starts forwarded history, like `----- 转发邮件 -----` already did.

[`results/quote_reader_eval_rules_after_fix.json`](../results/quote_reader_eval_rules_after_fix.json) is the rules run after the fix:

| Reader | Correct | Wrong | Missed | Held for a person |
|---|---|---|---|---|
| rules, after the fix | 15 | 0 | 7 | 18 |

**This run is not held-out.** The two fixes were made after looking at two items in this set. The held-out figure for the rules is the baseline above (14 correct, 2 wrong). Both files are kept.

## Pending: the Nemotron run

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
