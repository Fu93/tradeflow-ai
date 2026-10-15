# TradeFlow — Live Market Price Anchor

> **Data fetched (UTC)**: 2026-10-14T20:54:33+00:00  
> **Source**: UN Comtrade public preview API (China customs declarations)  
> **FX**: open.er-api.com (daily reference rate) — USD/CNY = **6.7050** (Wed, 14 Oct 2026 00:02:32 +0000)

## How to use this document

This file is **machine-refreshed real customs data**, not a static estimate. When a supplier claims a raw-material price increase, compare their claim against the year-over-year movement below.

**Always state the data date when citing these figures.** If the fetched date looks stale, the figures should be refreshed by running `python scripts/refresh_market_data.py`.

## Critical limitations (do not overstate)

- Only USD/kg is reported. USD/piece is deliberately excluded: the implied average weight per piece swings 348g-794g across years, which indicates inconsistent reporting scope rather than genuine price movement.
- net_weight is frequently flagged isNetWgtEstimated=true, i.e. estimated rather than individually declared. Treat as an order-of-magnitude anchor.
- These are macro trade anchors for sanity-checking supplier claims, NOT quotations. Factory-level BOM costs must be maintained by domain experts.

## Reference FX rate

| Pair | Rate | As of |
|---|---|---|
| USD -> CNY | 6.7050 | Wed, 14 Oct 2026 00:02:32 +0000 |

Use this single rate for all conversions so figures stay internally consistent.

## China export unit values (USD per kilogram)

| HS | Category | Role | Latest | YoY change | Latest year |
|---|---|---|---|---|---|
| 6601 | Umbrellas and sun umbrellas | 成品：主要標的商品 | **$5.01/kg** | 2024 -> 2025: -5.8% | 2025 |
| 660191 | Umbrellas, telescopic shaft | 成品細類 | **$6.84/kg** | 2024 -> 2025: -4.5% | 2025 |
| 660199 | Umbrellas, other | 成品細類 | **$5.15/kg** | 2024 -> 2025: -6.1% | 2025 |
| 7208 | Flat-rolled steel | BOM：中棒鋼管 | **$0.5067/kg** | 2024 -> 2025: -9.2% | 2025 |
| 5407 | Woven synthetic fabric | BOM：傘布面料 | **$4.12/kg** | 2024 -> 2025: -8.8% | 2025 |
| 3903 | Styrene polymers (ABS/PS) | BOM：手柄頂帽塑料 | **$1.35/kg** | 2024 -> 2025: -12.4% | 2025 |
| 7601 | Unwrought aluminium | BOM：五金件原料(進口價) | **$2.68/kg** | 2024 -> 2025: +3.3% | 2025 |
| 7318 | Screws, bolts, fasteners | BOM：五金標準件 | **$2.01/kg** | 2024 -> 2025: -2.1% | 2025 |

## Full time series

### HS 6601 — Umbrellas and sun umbrellas

*Role in this project*: 成品：主要標的商品

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 2,518,573,964 | 493,653,923 | $5.10 | ¥34.21 | yes |
| 2022 | FOB | 3,062,033,657 | 518,202,009 | $5.91 | ¥39.62 | yes |
| 2023 | FOB | 2,802,866,129 | 473,767,601 | $5.92 | ¥39.67 | yes |
| 2024 | FOB | 2,890,313,937 | 542,942,136 | $5.32 | ¥35.69 | yes |
| 2025 | FOB | 2,672,624,377 | 533,049,573 | $5.01 | ¥33.62 | yes |

### HS 660191 — Umbrellas, telescopic shaft

*Role in this project*: 成品細類

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 558,097,929 | 70,712,050 | $7.89 | ¥52.92 | no |
| 2022 | FOB | 876,932,419 | 107,282,471 | $8.17 | ¥54.81 | yes |
| 2023 | FOB | 1,021,254,055 | 133,452,910 | $7.65 | ¥51.31 | yes |
| 2024 | FOB | 1,040,706,316 | 145,290,294 | $7.16 | ¥48.03 | yes |
| 2025 | FOB | 1,003,270,845 | 146,621,730 | $6.84 | ¥45.88 | yes |

### HS 660199 — Umbrellas, other

*Role in this project*: 成品細類

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 698,730,635 | 112,269,427 | $6.22 | ¥41.73 | yes |
| 2022 | FOB | 973,126,163 | 145,374,961 | $6.69 | ¥44.88 | yes |
| 2023 | FOB | 963,898,693 | 156,106,851 | $6.17 | ¥41.40 | yes |
| 2024 | FOB | 885,220,536 | 161,492,001 | $5.48 | ¥36.75 | yes |
| 2025 | FOB | 768,100,169 | 149,232,019 | $5.15 | ¥34.51 | yes |

### HS 7208 — Flat-rolled steel

*Role in this project*: BOM：中棒鋼管

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 3,246,764,470 | 3,315,807,660 | $0.9792 | ¥6.57 | yes |
| 2022 | FOB | 11,601,603,492 | 13,792,994,210 | $0.8411 | ¥5.64 | yes |
| 2023 | FOB | 16,042,442,000 | 25,498,256,675 | $0.6292 | ¥4.22 | yes |
| 2024 | FOB | 18,853,166,636 | 33,789,907,517 | $0.5580 | ¥3.74 | yes |
| 2025 | FOB | 14,273,056,024 | 28,170,337,586 | $0.5067 | ¥3.40 | yes |

### HS 5407 — Woven synthetic fabric

*Role in this project*: BOM：傘布面料

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 17,073,721,386 | 3,308,726,484 | $5.16 | ¥34.60 | yes |
| 2022 | FOB | 20,098,049,435 | 3,730,849,532 | $5.39 | ¥36.12 | yes |
| 2023 | FOB | 19,436,143,736 | 4,016,698,510 | $4.84 | ¥32.44 | yes |
| 2024 | FOB | 20,299,004,852 | 4,498,054,279 | $4.51 | ¥30.26 | yes |
| 2025 | FOB | 20,155,943,950 | 4,896,097,764 | $4.12 | ¥27.60 | yes |

### HS 3903 — Styrene polymers (ABS/PS)

*Role in this project*: BOM：手柄頂帽塑料

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 778,513,487 | 363,163,159 | $2.14 | ¥14.37 | yes |
| 2022 | FOB | 1,113,667,643 | 547,422,904 | $2.03 | ¥13.64 | yes |
| 2023 | FOB | 1,157,595,894 | 755,971,892 | $1.53 | ¥10.27 | yes |
| 2024 | FOB | 1,403,067,555 | 912,374,719 | $1.54 | ¥10.31 | yes |
| 2025 | FOB | 1,835,043,547 | 1,361,961,413 | $1.35 | ¥9.03 | yes |

### HS 7601 — Unwrought aluminium

*Role in this project*: BOM：五金件原料(進口價)

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 416,916,985 | 157,765,356 | $2.64 | ¥17.72 | yes |
| 2022 | FOB | 1,312,762,255 | 421,141,548 | $3.12 | ¥20.90 | yes |
| 2023 | CIF | 6,002,153,562 | 2,675,299,745 | $2.24 | ¥15.04 | yes |
| 2024 | FOB | 942,003,775 | 363,440,277 | $2.59 | ¥17.38 | yes |
| 2025 | FOB | 1,559,814,275 | 582,311,206 | $2.68 | ¥17.96 | yes |

> Note (2023): export weight unavailable; using China IMPORT CIF as material-cost proxy

### HS 7318 — Screws, bolts, fasteners

*Role in this project*: BOM：五金標準件

| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | CNY/kg | Weight estimated? |
|---|---|---|---|---|---|---|
| 2021 | FOB | 9,090,078,097 | 3,732,688,241 | $2.44 | ¥16.33 | yes |
| 2022 | FOB | 11,178,859,446 | 3,918,046,598 | $2.85 | ¥19.13 | yes |
| 2023 | FOB | 9,139,925,948 | 3,871,802,796 | $2.36 | ¥15.83 | yes |
| 2024 | FOB | 9,563,989,933 | 4,659,283,933 | $2.05 | ¥13.76 | yes |
| 2025 | FOB | 10,049,982,829 | 5,000,080,529 | $2.01 | ¥13.48 | yes |

## What this document cannot tell you

These are aggregate national export statistics. They **cannot** give:

- The should-cost of one specific finished product
- A particular factory's quotation or margin structure
- Live spot prices for a named material grade (e.g. 210T impact fabric)
- Domestic Chinese purchase prices paid by factories

For product-level should-cost, use the physical BOM decomposition in `spec_baseline.md`, and treat this file only as a **reasonableness check** on whether a claimed cost movement is plausible.

---

*Auto-generated by `scripts/refresh_market_data.py`. Do not hand-edit — rerun the script instead.*
