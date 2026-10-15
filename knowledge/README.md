# TradeFlow Knowledge Base

Three documents intended for upload into the **Evolus Personal library**, so the
`Supplier Negotiation Analyst` agent answers from controlled, sourced material rather
than from model memory.

## Files

| File | Type | Refresh | Purpose |
|---|---|---|---|
| [`market_anchor.md`](market_anchor.md) | **auto-generated** | `python scripts/refresh_market_data.py` | Real customs trade data + live FX, for sanity-checking supplier cost claims |
| [`spec_baseline.md`](spec_baseline.md) | human-maintained | manual review | Should-cost BOM and the specification-downgrade catalogue |
| [`compliance_policy.md`](compliance_policy.md) | human-maintained | manual review | Binding negotiation red lines and escalation triggers |

`market_data.json` is the machine-readable cache behind `market_anchor.md`; the engine
can read it programmatically.

## Why the split

The three files have **different truth conditions**, and mixing them would make all
three less trustworthy:

- `market_anchor.md` contains figures that change and can be **fetched from an
  authoritative source**. It is regenerated, never hand-edited.
- `spec_baseline.md` contains practitioner estimates that **no public dataset can
  supply** (a named factory's BOM for 210T pongee). These are labelled `[EXPERT]` and
  must not be laundered into false precision.
- `compliance_policy.md` is a **decision rule set**, not data. It changes only when
  policy changes.

## Verifying the market data

```bash
python scripts/refresh_market_data.py          # fetch and regenerate
python scripts/refresh_market_data.py --offline  # rebuild markdown from cache
```

Sources (free, no API key, verified reachable):

- **UN Comtrade** public preview API — China customs declarations.
  `https://comtradeapi.un.org/public/v1/preview/C/A/HS`
- **open.er-api.com** — daily USD/CNY reference rate.

### Known data limitations (also stated inside `market_anchor.md`)

1. **Only USD/kg is published.** USD-per-piece is deliberately excluded. Although some
   HS sub-codes (660191/660199) return a quantity field, the implied average weight per
   piece swings from 348 g to 794 g across years — evidence of inconsistent reporting
   scope, not real price movement. Reporting a per-piece price from this data would
   produce a confidently wrong anchor.
2. **Weights are frequently estimated** (`isNetWgtEstimated: true`), not individually
   declared. Treat the series as an order-of-magnitude anchor.
3. **These are macro aggregates**, not quotations. They cannot produce a product-level
   should-cost; that is what `spec_baseline.md` is for.
4. **Rate limited** (HTTP 429). The script backs off and retries; a full refresh takes
   a few minutes by design.

## Uploading to Evolus

1. Evolus → `Your AI team` → open the agent → **Personal library**
2. Upload all three `.md` files (`.md` up to 1 MB each — these are far smaller)
3. Leave **"Can search the web" OFF** (keeps supplier data internal and removes an
   uncontrolled variable)
4. Enable **"Reads scanned documents"** only if suppliers send scanned quotations

Then ask the agent a question such as:

> *"A factory says raw material costs rose 30% and quotes ¥14.80. Based on your
> knowledge base, how should I respond?"*

A correctly configured agent will cite the data date, note that a 30% input rise does
not justify a 30% finished-price rise, and ask for the material cost share — rather
than inventing a counter-offer.
