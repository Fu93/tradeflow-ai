# Market analysis

Sourced notes for the TradeFlow desk. HS trade figures in the generated block come from a live UN Comtrade preview fetch (`scripts/market_data.py` → [results/market_snapshot_6601.json](../results/market_snapshot_6601.json)). Prose figures below are cited to the page that published them. Labels: **ESTIMATE** (formula on cited inputs), **ASSUMPTION** (no source), **third-party** (not the vendor's own price), **unverified** (seen in a snippet or not opened).

The primary trade source is the UN Comtrade public preview. A research note that used WITS pages is a cross-check only. WITS is not the primary source, and this snapshot did not call WITS. Where the note and the snapshot disagree, the snapshot wins.

Umbrellas (HS 6601) are the demo wedge, not a large market. The same route accepts any 2–6 digit HS code. Customs cannot split automatic, carbon, and custom umbrellas. There is no sourced FX rate, so Comtrade USD is not converted to GBP. Cluster shares are not stated.

## Persona (hypothesis, not data)

A small UK cross-border seller sources from factories and negotiates in chat, without a procurement team. This is a design hypothesis. It has not been checked with interviews.

## Buyer counts and the goods total

HMRC "Customs importer and exporter population 2025" (published 16 Apr 2026): 334,294 UK businesses imported goods in 2025 (321,493 in 2024). https://www.gov.uk/government/statistics/customs-importer-and-exporter-population-2025/customs-importer-and-exporter-population-2025

Same release, Tables 2 and 4, 2025: importing-only from non-EU only 126,488; importing-and-exporting with non-EU only 5,996; importing-only from EU and non-EU 29,463; importing-and-exporting with EU and non-EU 77,669.

SAM (count, proxy for non-EU sourcing, not a China-only count):

- Lower bound: 126,488 + 5,996 = 132,484.
- Upper bound: 126,488 + 5,996 + 29,463 + 77,669 = 239,616.

The upper cell can include firms that import from the EU and only export outside it. The China share of firms is unknown. Do not multiply by 11.1 percent: that figure is a share of goods value, not of firms. US Census, 2024 preliminary profile: 232,804 of 239,231 identified importers were SMEs (97.3 percent). https://www.census.gov/foreign-trade/Press-Release/edb/2024prelimprofile.pdf

UK goods imports from China, 2025: GBP 69.7 billion (+3.5 percent year on year). China was the second-largest UK goods import source (11.1 percent). Twelve months to July 2026: GBP 72.9 billion. ONS/DBT trade and investment core statistics book, Table 15a/15b: https://www.gov.uk/government/statistics/trade-and-investment-core-statistics-book/trade-and-investment-core-statistics-book and the DBT China factsheet of 24 Sep 2026: https://assets.publishing.service.gov.uk/media/6ab147d44c0f475de0a66afa/china-trade-and-investment-factsheet-2026-09-24.pdf

That GBP total is current prices, balance-of-payments basis. It includes non-monetary gold and precious metals, and large corporates, so it overstates small-buyer sourcing spend.

China goods exports 2025: 3,771.87 billion US dollars. China General Administration of Customs: http://english.customs.gov.cn/Statics/1c8250cb-3436-4c46-b596-e2ff88e88457.html

A vendor page quotes global cross-border B2B e-commerce near 135 billion US dollars. Another vendor disagrees by a wide margin. UNCTAD says the definitions are scarce. That figure is unverified as a headline and is not used as one. https://unctad.org/system/files/official-document/tn_unctad_ict4d06_en.pdf

## Savings-pool ceiling (ESTIMATE, upper bound)

Formula: spend times s, with s = 1.5 percent and s = 3 percent. Those rates are the Walmart/Pactum large-buyer reports (1.5 percent, later 3 percent), not a TradeFlow result and not a small-buyer China-sourcing measurement. https://www.pon.harvard.edu/daily/negotiation-skills-daily/chatbot-negotiations-what-can-ai-do-for-you/ and https://www.businesswire.com/news/home/20230111005148/en/Pactums-Autonomous-Negotiation-Technology-on-Display-at-the-National-Retail-Federations-2023-Retail-Big-Show

GBP 69.7 billion times 1.5 percent = GBP 1.05 billion. Times 3 percent = GBP 2.09 billion. This is a ceiling if every pound were negotiated at that large-buyer rate. It is not TradeFlow savings.

APQC's open benchmark median cost-avoidance is 2.0 percent of spend (n = 2,431). Cost avoidance is not hard savings. https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/typical-savings-achieved-through-cost-0

A Chilean public-procurement study found about 8 percent lower prices when competition increased. That supports a direction only. https://web.stanford.edu/~dsaban/chilecompra.pdf

Hackett and one academic paper were seen only as search snippets. They stay unverified and are not cited as numbers here.

The umbrella pool uses the same formula on the Comtrade row in the snapshot. It is an ESTIMATE and an upper bound. It is not a TradeFlow result.

## HS 6601 snapshot (primary)

<!-- BEGIN GENERATED MARKET SNAPSHOT results/market_snapshot_6601.json -->
Generated from `results/market_snapshot_6601.json`. Do not edit the figures by hand.
Source: UN Comtrade public preview (`https://comtradeapi.un.org/public/v1/preview/C/A/HS`).
Fetched at: 2026-10-18T09:44:14+01:00.
Valuation is whatever the preview row supports (CIF when cifvalue equals primaryValue). Currency is USD.
Quantity 0 on the preview row is stored here as `not available`. Unit value is computed only when value and quantity both exist.

| Reporter | Partner | Flow | HS | Year | Value USD | Quantity | Qty unit | Unit value USD | Net weight kg | Aggregate | Provisional | Cache |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| United Kingdom | China | import | 6601 | 2024 | 83330323.481 | not available | not available | not available | 21467978 | True | False | fresh |
| United Kingdom | China | import | 6601 | 2025 | 73071378.761 | 12049357 | not available | 6.06 | 11718789 | True | True | fresh |
| United Kingdom | China | import | 660110 | 2024 | 30625465.075 | 1514935 | u | 20.22 | 6952801 | True | False | fresh |
| United Kingdom | China | import | 660191 | 2024 | 23713294.758 | not available | not available | not available | 10498772 | True | False | fresh |
| United Kingdom | China | import | 660199 | 2024 | 28991563.648 | 8436132 | u | 3.44 | 4016405 | True | False | fresh |
| United Kingdom | World | import | 6601 | 2024 | 93076531.709 | not available | not available | not available | 22026746.724 | True | False | fresh |
| USA | China | import | 6601 | 2024 | 528731204 | 124186704 | not available | 4.26 | 88922005.65 | True | False | fresh |
| European Union | China | import | 6601 | 2024 | 557319351.045 | 131804117 | not available | 4.23 | 113252257 | True | False | fresh |
| China | World | export | 6601 | 2024 | 2890313937 | 961450981 | not available | 3.01 | 542942135.726 | True | False | fresh |
| United Kingdom | China | import | 660199 | 1990 | not available | not available | not available | not available | not available | not available | False | fresh |

Children 660110+660191+660199 (UK imports from China, 2024) sum to 83330323.481 USD.
Parent HS 6601 for the same flow is 83330323.481 USD.
Children equal parent: True.
UK-China share of UK-World HS 6601 imports in 2024 (both Comtrade rows): 0.8953.

Savings pool (ESTIMATE, upper bound). Formula: `value x s` with s = 1.5% and s = 3% from large-buyer reports. This is not a TradeFlow result.
- Year 2024: base 83330323.481 USD x 1.5% = 1249954.85 USD; x 3% = 2499909.7 USD. Label: estimate. Upper bound. Not a TradeFlow result.
- Year 2025: base 73071378.761 USD x 1.5% = 1096070.68 USD; x 3% = 2192141.36 USD. Label: estimate. Upper bound. Not a TradeFlow result.
<!-- END GENERATED MARKET SNAPSHOT results/market_snapshot_6601.json -->

### HMRC is a different source

HMRC Overseas Trade Statistics, non-EU imports, origin China, CN8 66019920 (GBP 4.67 million) plus 66019990 (GBP 13.99 million) = GBP 18.66 million in 2024. The 2023 total on the same two codes was GBP 15.37 million. Comtrade's USD row for 660199 is about 29 million US dollars and is CIF, reporter-declared, not a mirror. The gap is a data-quality caveat. The two currencies are not merged. No sourced FX rate is in this repo, so this note does not convert one into the other.

### Cross-check against the research note

The note's WITS pages (accessed 2026-10-18) rounded UK-from-China 2024 heading 6601 to about 83.33 million US dollars, with children near 23.71, 28.99, and 30.63 million. Those pages are a cross-check. The snapshot's exact primaryValue rows are the figures to quote. The note's 660199 quantity 8,436,130 and 660110 quantity 1,514,940 are not copied into the table. 2025 was not in the note; the snapshot's 2025 row is flagged provisional when the year is inside the revision window. IndexBox "90 percent / 84 million US dollars" is a vendor page and is not reproduced as fact.

Unit value in the table is primaryValue divided by qty, half-up to 0.01 USD, and only when both exist. Quantity 0 on the preview is `not available`, not zero. The note's US$3.44 per item used a rounded value and a different quantity, so it is not the snapshot unit value.

China's 2024 umbrella export total in an older `knowledge/market_data.json` anchor is a separate $/kg series. It is not this module's unit value.

## How the desk uses this

`GET /api/v1/market` calls the same module. A keyword returns candidates and does not pick an HS code and does not call Comtrade. An explicit `hs` of 2–6 digits fetches UK-from-China imports unless the query says otherwise.

Creating a session (dashboard commodity picker, `POST /api/v1/sessions`, or the extension reading that session) resolves a keyword over the local HS list. `SessionSpec.hs_code` overrides that guess. The session stores `market_context`: HS code, UK-from-China value, quantity, implied unit value when both exist, year, source, fetched_at, caveats, or `status: unavailable`. The extension Evidence tab and the engine-decision card show it as REFERENCE ONLY beside the supplier quote. The label is "implied customs unit value". CIF customs valuation is not a price quote.

`POST /api/v1/multi-auction/run` runs `simulate_reverse_auction` first, then attaches `market_context`. The block is not an argument of the simulator.

Invariant: `market_context` does not influence the solver, the recommended price, or the circuit breaker. `src/engine/solver.py` and `src/engine/multi_solver.py` do not read it. A test creates two sessions with the same offer, one of them carrying a different unit value, and requires the same `recommended_offer_price`. Another test shows a model JSON that includes `recommended_price` still leaves `buyer_price` null.

Offline (`TRADEFLOW_MARKET_OFFLINE=1`, or a failed preview call) stores an explicit unavailable state. It does not invent a value. Cache lives in gitignored `.cache/market`. Optional `COMTRADE_API_KEY` is sent only as a header and is not written into URLs or this document.

## Revenue (proposal)

No revenue is claimed. No factory deal has closed. A proposed model is a desk subscription or a share of documented savings versus the first-round best quote.

SOM scenarios are ASSUMPTION. Formula: SAM lower bound times a times P. a is an assumed win rate (0.1 percent or 0.5 percent). P = 19.90 US dollars a month times 12 = 238.80 US dollars a year, copied from Accio Pro's public price. That is an anchor, not willingness to pay. 0.1 percent of 132,484 is about 132 customers; 132 times 238.80 is about 31.5 thousand US dollars a year. 0.5 percent is about 662 customers and about 158 thousand US dollars a year. Do not present these as data.

Take-rate illustrations (10 percent and 20 percent of the GBP savings-pool ceiling) are also ASSUMPTION. They are not a forecast. They stay in this note and are not judge-facing data.

## Competitors

| Product | What it does | Public price | Label |
|---|---|---|---|
| Accio (Alibaba) | Sourcing agent, inquiry and negotiation on Alibaba | Free tier; Pro 19.90 US dollars a month; Elite 99; Ultra 199. Core app Starter 9.90, Pro 19.90, Elite 99 | Public page. https://www.accio.com/wow/doc-help-billing.html |
| Alibaba RFQ and Trade Assurance | Suppliers quote on a request | Free for buyers | Public page. https://tradeassurance.alibaba.com/lp/buyer/ta/MoneyBackPolicy.htm |
| Pactum | Autonomous negotiation for large buyers | Quote-based. A pricing page was seen only as a search excerpt | unverified as a list price. https://pactum.com/pricing |
| Keelvar | Sourcing optimisation | Quote-based. A third-party range is not an official price | third-party |
| Fairmarkit | Tail-spend RFQs | Quote-based. A third-party range is not an official price | third-party |
| Jaggaer | Source-to-pay | Quote-based. A third-party range is not an official price | third-party |
| Zycus | Source-to-pay | No public list price found | unverified |
| Globality | Enterprise sourcing | No public list price found | unverified |

## Promo merchandise (snippet only)

A Sourcing City / ASI snippet says UK and Ireland promotional merchandise distributor sales were GBP 1.232 billion in 2024. The pages were not opened as full reports. Treat that as a snippet, not a measured input. Umbrellas are not broken out. https://members.asicentral.com/news/industry-news/february-2025/sourcing-city-report-says-uk-promo-market-achieved-record-sales-in-2024/

## What we can claim, and what we must not

Can claim, with the citation above: 334,294 UK importers in 2025; SAM 132,484 to 239,616 by the formula; GBP 69.7 billion of UK goods imports from China in 2025, with the precious-metals caveat; the Comtrade rows in the generated block; Accio's public prices; Alibaba RFQ free for buyers.

Can claim only as ESTIMATE: the GBP 1.05–2.09 billion savings-pool ceiling, and the umbrella pool printed in the snapshot.

Must not claim: TradeFlow savings equal to those percentages; a China-only importer count; a cluster share; a GBP conversion of the Comtrade row; customers, revenue, or willingness to pay; that a third-party enterprise price range is the vendor's price; a single global B2B market total.

## Gaps

Customs data cannot separate automatic, carbon, and custom umbrellas. No sourced FX rate. No China-specific importer count. No small-buyer savings benchmark. Promo figures are snippets. EU importer counts were not pulled.

## 繁體中文摘要

雨傘（HS 6601）是示範品類，不是大市場。同一支 UN Comtrade 公開預覽腳本可查其他 HS 編碼。海關數字是對照，不進入求解器、建議價或熔斷。數量缺報記為 not available，不補零。英國 2025 年有 334,294 家進口企業（HMRC）；非歐盟進口者的代理區間是 132,484 至 239,616。英國自中國商品進口 GBP 697 億（ONS/DBT），含貴金屬與大型企業，會高估小買家採購。1.5% 至 3% 是大型買家公開區間，節省池上限是估算，不是 TradeFlow 的成績。HMRC 的英鎊與 Comtrade 的美元不合併。SOM 的取得率與價格是假設。

## Closing the gaps (runbook, added 2026-10-24)

Each gap above, with the source and the method to close it. Nothing in this
section is a number; until someone runs the pull and cites the release here,
the gap stays open and the "Must not claim" list still applies.

1. **EU importer counts.** Eurostat, "Participation of enterprises in
   international trade" (the same annual release the UK figures above come
   from, ish — the UK rows cite the UK release; for the EU-27 bloc pull the
   Eurostat structural business statistics on importers, table
   `ext_tecpart`), filtered to goods importers from outside the EU. Record
   the reference year and whether the count includes intra-EU importers,
   exactly as the UK rows do.
2. **China-specific importer count.** No public register separates
   China-only importers in the UK or EU releases. Two honest options: (a)
   state the proxy explicitly and never drop the "not a China-only count"
   caveat (current practice); (b) commission or find a private panel and
   cite it. Do not derive one by multiplying a goods-value share.
3. **FX for the desk path.** The auction surface already fetches a sourced,
   reference-only ECB rate (`src/market/official_refs.py`, frankfurter as
   the alternate source, `used_in_solver` stamped). The desk's quote path
   deliberately has no FX conversion: deals are CNY, and a USD quote is
   flagged, not converted (`TRADEFLOW_USD_CNY_RATE` documents this). Closing
   the gap means a sourced rate reaching the desk's provenance label —
   never the solver — with the same `used_in_solver: false` stamping.
4. **Small-buyer savings benchmark.** Only a real pilot produces this
   (docs/PILOT_RUNBOOK.md); do not substitute the Walmart/Pactum
   large-buyer rates.

FX "cache": `official_refs.py` deliberately does not cache — every call
fetches fresh with a timeout and stamps the retrieval. For a reference
figure that must never enter the solver, freshness beats rate limiting; if
call volume ever justifies a cache, it must keep the `as_of` date in the
payload honest.
