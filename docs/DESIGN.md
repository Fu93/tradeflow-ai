# Interface design: the Deal Dossier

This document records the visual and layout decisions behind the dashboard and the Chrome extension, so a later change does not quietly turn it back into "dark fintech terminal plus a wall of cards".

## Why it is not a dark dashboard

The screen represents **the deal file on a buyer's desk**: a document with an authorised limit, the counterparty's quote, a draft reply and, at the end, a human signature. Dark-terminal aesthetics (near-black background, neon green, rounded cards, gradient charts) are the default of every SaaS dashboard and have nothing to do with "a document, a seal, a limit".

So it became paper-like: cool grey-green stock, hairline rules, tabulated figures and one red stamp. Red is reserved for **authorised ceilings and risk** and is never decorative — which is why `walk-away ceiling`, `TRIPPED` and "the counterparty's asking price" share the same red.

## Tokens

| Purpose | Value | Note |
|---|---|---|
| Paper | `#dfe2db` | Page field, with very faint ruling |
| Dossier | `#f2f2ec` | Main panel (one step brighter than the paper) |
| Recess | `#e8e9e1` | Table rows, inputs |
| Ink | `#1b2622` | Body text (contrast ≈ 13:1) |
| Soft ink | `#55645c` | Secondary text, quotes |
| Faint ink | `#8b968d` | Labels, notes |
| Rule | `#c2c8bd` / `#9ba79c` | Hairline / structural line |
| Stamp red | `#a33a2a` | Authorised ceiling, risk, stamp, CTA |
| Steel blue | `#2e4f63` | Engine output, secondary figures |
| Moss | `#46664c` | Safe / measured low risk |
| Ochre | `#8d641b` | Awaiting a human decision, attention |

Type: headings and figures use `Bahnschrift` (the narrow sans shipped with Windows; fallbacks DIN Alternate / Roboto Condensed / Arial Narrow), body and Chinese text use `Segoe UI` plus `Noto Sans TC` / `Microsoft JhengHei`. The split is deliberate: display type carries amounts, dates and titles; body type carries sentences. Amounts always use `font-variant-numeric: tabular-nums` so decimal points line up.

## Layout

```
masthead ──────────────────────────────────────────────────────────────
Deal file   自動反開摺疊傘                       [ status stamp ]
Reference UI_CHECK_01                              Approval stays with the buyer
════════════════════════════════════════════════════════════════════════
┌ price ladder (the lead) ─────────┐ ┌ margin notes (no frame) ────┐
│ 16.20 ─ authorised ceiling       │ │ Breakdown risk  1.7%        │
│ ¥15  ────────                    │ │ Mandate (table)             │
│ ¥14  ──────── ● counterparty ask │ │ Deals (list)                │
│ ¥13  ────────                    │ │ Four-supplier auction       │
│ 12.00 ─ target                   │ └─────────────────────────────┘
│ ¥1.40 below ceiling │ +¥2.80 over target │
└──────────────────────────────────┘
┌ engine decision: intent / recommendation / risk + draft (copyable) ┐
┌ counterparty belief: line chart (demoted to a supporting position) ┐
─────────────────────────────────────────────────────────────────────
Remote command 1/2/3            Mobile telemetry
```

The main column is "documents pinned into the file" (framed, brighter); the sidebar is "margin notes" (unframed, rules only). The asymmetry is intentional: making every block the same rounded card is the one default this product does not need.

**The single bold move is the price ladder.** It puts target, ceiling, the counterparty's ask and the engine's counter on one axis and answers the buyer's first question — how much room is left. Ticks use round steps (¥13/14/15) rather than quartiles so it reads like a quotation. Everything else is restrained: hairlines, small type, no shadows, no gradients.

## Behaviour and honesty

- With no data, values render as `—`, markers hide, and a sentence covers the chart (so Chart.js cannot draw a fake 0–1 axis).
- With no quote, the ladder collapses instead of leaving a 296px hole.
- "Copy draft" sits beside the draft: that text was always meant to be pasted back into the chat by a human, and could previously only be selected by hand.
- The extension reuses the same tokens; the HUD is "a paper card clipped onto the page" with a status colour along its left edge, and the decision cards are a rule-separated list rather than a stack of identical dark rounded cards.

## Considered and rejected

- Dark plus a single acid-green accent: the most "AI-generated dashboard" look available, and unrelated to the document metaphor.
- Cream background plus serif headings plus terracotta: also a default, and it would dilute the red stamp.
- Making every panel the same-sized card: loses the "main document / margin note" hierarchy.
