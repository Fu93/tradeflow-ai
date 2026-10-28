# Devpost submission pack: Nebius x NVIDIA Global AI Hackathon

Deadline: **Friday 30 October 2026, 17:00 London** (10:00 Pacific). Aim to submit by Friday 12:00 London. Rules: <https://nebiusglobalaihackathon.devpost.com/rules>.

## Checklist

| Requirement | State |
|---|---|
| Runs on Nebius Token Factory and uses an NVIDIA open model | The desk quote reader (`src/desk/extract.py`) and the dashboard reader (`src/engine/quote_read.py`) call Nemotron on Token Factory. One live check (desk reader only) was made on 2026-10-28 on Ultra and twice on Super (see "Single live check" below); each was a single quote read, not a benchmark. The held-out evaluation is in [QUOTE_READER_EVAL.md](QUOTE_READER_EVAL.md). |
| Track | Best Apps and Agents |
| Public repository with an open-source license visible in the About panel | MIT (`LICENSE`). Confirm the About panel on GitHub shows it. |
| README with setup and how NVIDIA models and Token Factory were used | The first sections of [README.md](../README.md) |
| Working demo URL | Done: <https://tradeflow-ai-demo.onrender.com>. `/health` returned 200 and one single live check was made on 2026-10-28 (see "Single live check" below). |
| Public YouTube video, 3 minutes or less, no unlicensed trademarks or copyrighted music | **To do.** Script below. Leave AMD and lablab logos out of every frame. |
| Text description | Below |
| Feedback on the Nebius and NVIDIA tools | [NEBIUS_NVIDIA_FEEDBACK.md](NEBIUS_NVIDIA_FEEDBACK.md). Edit it against your own runs first. |
| Explanation of updates to a pre-existing project | README, "What changed during the submission period". The first commit is dated 13 October 2026. |
| Best Use of Tavily (optional bonus) | The Web evidence card: a real runtime Tavily call, needs `TAVILY_API_KEY` on the service |

## Title and tagline

**TradeFlow AI: the supplier negotiation desk.**
The model reads the quote. The math prices the counter. You press send.

## Description

**The problem.** A small cross-border seller negotiates factory quotes alone, often across a language gap, or pays a sourcing agent a commission. Quotes arrive as free text in two languages, with prices buried next to fees, deposits, rival numbers and tier tables.

**What it does.** TradeFlow is a one-person desk for four or five suppliers at once.

- A supplier message is pasted, arrives by mailbox, or is read from an open 1688, Alibaba, WhatsApp or WeChat chat by the Chrome extension. The extension can read new supplier messages automatically; it never types into the chat or presses send.
- Nemotron, served by Nebius Token Factory, names the evidence spans and what each one is: the supplier's offer, a rival's price, a fee, a deposit, an injected instruction. The desk shows those spans next to the message, with the model and its latency.
- Deterministic code reads the amount from a span that occurs verbatim in the message. A person confirms the number.
- Every confirmed quote recomputes all suppliers. Each supplier has its own Bayesian reserve-price belief. The desk ranks them (by unit price, or by landed cost when freight, fees and duty are typed for every quoted supplier) and gives two figures per supplier: the default (the best price already on the table) and a push-lower counter that maximises expected surplus inside the buyer's walk-away risk cap. The buyer chooses which one goes into the draft.
- Drafts are written in the supplier's language. Every step lands in a keyed audit chain, and a send needs a signed human approval that the server re-derives from the chain before anything leaves. The demo service pins no approver key, so it refuses sends (see the limits below).
- The card shows the quote validity the supplier stated and flags an expired quote; it never moves the rank, counter or draft (PR #34). Not live-tested. A validity given in working days is shown as written and not turned into a date, and two contradictory validity statements are flagged as a conflict.

**Where each tool earns its place.**

- *Nebius Token Factory with NVIDIA Nemotron* reads messy supplier text. The default model is Nemotron Super with one more try of the same model after any failed attempt; Ultra can be selected with `TRADEFLOW_LLM_MODEL`. Every attempt is timed and recorded (first hop 30 s, retry 20 s, whole chain 80 s), and paid calls are capped at 40 per UTC day per server process (`TRADEFLOW_LLM_DAILY_CALLS`; the count is kept in memory and resets if the service restarts; shown on `/health` as `llm.model_calls_today`) so a small balance lasts the judging window. The cap counts one call per quote read, so a retry of the same model does not count. `render.yaml` sets `WEB_CONCURRENCY=1`, so the service runs one process. Past the cap the desk keeps working on the rule reader and says so.
- *Tavily* adds a Web evidence card to each supplier: cited public pages about a product's wholesale prices and about a supplier's reviews. It sits beside the quote and never touches the price.

**The design choice that matters.** Neither the model nor the search can move a price. A price field in the model's answer is thrown away, and no decision module imports the search code; a test fails if one does. This keeps the model's job narrow: it reads wording, the Bayesian reserve-price and expected-surplus core decides the number, and a person approves every send. That boundary is tested; whether it is enough for real money is not something we have shown, because no factory has closed a deal through this prototype.

**Honest limits.** No factory has closed a deal through this prototype. The walk-away hazard is a transfer from an eBay Best Offer fit, not a measured factory rate. The extension's automatic read was tested on a mock chat page, not on the real 1688, Alibaba, WhatsApp or WeChat pages. The landed-cost fields on the paste form are covered by unit tests and CI, and have not been tried live with a real quote. **Not yet proven:** the Nemotron reader's accuracy (the held-out evaluation in [QUOTE_READER_EVAL.md](QUOTE_READER_EVAL.md) has a rules-only baseline; the Nemotron run is pending; the one message read live before the prompt change got one label wrong, and the one read after it was a single sample, see below) and latency beyond these single calls. The Tavily card shows what public pages say; it does not verify a supplier.

**Limits for real use.** Each of these is checked against the code.

- The desk page has no currency choice. Deals are opened in CNY and most figures on it are labelled CNY.
- Each supplier is entered as an email address.
- Quotes are compared on the confirmed unit price. Landed cost is used only when freight, fees and duty are entered for every quoted supplier. Tax-included or tax-excluded status, MOQ and lead time are not compared.
- A person confirms a figure that is already in the supplier's message. There is no manual price entry.
- With one confirmed quote, the default counter equals that supplier's own ask, unless the person picks the push-lower counter.
- In this version the model is a shadow check. The candidate figures, the suggested figure and the draft are produced without it; in a local run they were identical with the model off and with a stub that labelled the wrong number.
- Paid model calls are capped at 40 per UTC day, counted in memory per server process.
- Deals live in the demo database on an ephemeral disk; a restart clears them.
- On the demo service a send is refused: signed approval is required, no approver public key is pinned, and the desk page attaches no signature.


**Single live check, Ultra (2026-10-28, about 12:17 London time, UTC).** One check each, on the live demo (<https://tradeflow-ai-demo.onrender.com>) running master commit `5c0321830e5cadb7a4571fbe9ca46fe510017895`. This is one call each, not a benchmark. That run used Ultra because it was the default of that commit.

- *Nebius Token Factory:* one sample quote (fictional `example.test` suppliers) was read. The demo's recorded extractor attempt shows provider `nebius`, model `nvidia/Nemotron-3-Ultra-550b-a55b`, status `ok`, latency 1.83 s.
- *Tavily:* `GET /api/v1/market/evidence?kind=market&product=...` returned HTTP 200 with status ok and 5 results in about 2.3 s.

**Single live check, Super, before the prompt change (2026-10-28, demo on master `10868aa08d154027b4bdb5ef6cb134fc9c116b4b`).** One sample deal and one pasted message (source `paste`; the sample message, to supplier A: 老板好，隔壁厂给你 24 元那是次料，我们正品单价 27.80 元/把，起订 1000 把，15 天出货。). One run, not a benchmark.

- *Model:* provider Nebius Token Factory, model `nvidia/nemotron-3-super-120b-a12b` (the default; one retry of the same model).
- *Latency:* the first attempt timed out at 12.0 s; the retry of the same model succeeded in 11.2 s, about 23 s of model time in total. We note this as an observation only. Token usage was not measured.
- *Labels:* "隔壁厂给你 24 元那是次料" was labelled `rival_price`, which is right. "我们正品单价 27.80 元/把", the supplier's own offer, was labelled `our_price_echo`; we expected `own_unit_offer`. So this one label was wrong, and we do not claim the model labels numbers correctly.
- *What the desk did:* the quote went to human review with the reason "more than one price"; no draft or counter was created. The decision number comes from the deterministic reader (`llm_price_ignored: true`), not from the model's label.
- *Audit and budget:* audit verify returned ok (8 entries, no broken sequence). `/health` `llm.model_calls_today` went from 0 to 1 of a cap of 40: the retry did not count.
- *Not measured:* token usage; accuracy beyond this one message; the Nemotron part of the quote-reader eval (`scripts/eval_quote_reader.py`) is still pending.

**What changed after that check (PR #31).** The reader's prompt had named the label categories without defining them; it now defines each one in general terms, including that a price said right after a rival's price is the supplier's own offer. The first-hop timeout went from 12 s to 30 s, the retry hop from 15 s to 20 s, and the chain budget from 57 s to 80 s, because the 12 s limit had been set for Ultra and a timed-out call is still billed. The extraction model still never supplies a number.

**Single live check, Super, after PR #31 (2026-10-28, demo on master `1b4dc056ef9ef81096263bda79cf419eb9ee02a3`).** One sample deal and one pasted message (source `paste`), the same sample message as above. One quote read, not a benchmark.

- *Model and latency:* provider Nebius Token Factory, model `nvidia/nemotron-3-super-120b-a12b`, exactly one attempt, status `ok`, latency 12.26 s. No timeout, no retry, no failover. Total time from adding the quote to the finished result was between 12.3 and 20.8 s (the snapshot was still pending at 10.4 s and finished at the 20.8 s poll).
- *Labels:* "隔壁厂给你 24 元" was labelled `rival_price`; "我们正品单价 27.80 元/把" was labelled `own_unit_offer`, which is correct this time (the earlier run gave `our_price_echo`); "起订 1000 把" and "15 天出货" were labelled `other`.
- *What the desk did:* the quote still went to human review with the reason "more than one price". That reason comes from the deterministic rule reader seeing two prices, not from the model's label. The suggested figure was 27.80, with candidates 27.80 and 24; no draft or counter exists until a person confirms. `llm_price_ignored` was true.
- *Audit and budget:* audit verify returned ok (8 entries, no broken sequence). `/health` `llm.model_calls_today` went from 0 to 1 of a cap of 40.
- *How to read this:* it is one sample, taken after the prompt was changed in response to the earlier mislabel. The sample resembles the two held-out evaluation items that the change exposed (`zh_rival_then_own`, `en_rival_then_own`), so it is not a held-out result. It is not a benchmark and not proof of accuracy, and we do not say the problem is fixed. The Super run of the evaluation is still pending.
- *Not measured:* token usage; accuracy beyond this one message.

**What was updated during the submission period.** The first commit in this repository is dated 13 October 2026, after the submission period opened on 26 August, so all of the work falls inside the period. The repository began as an entry for the AMD Developer Hackathon ACT III, which has concluded; no lablab submission record for this project has been located and no prize or placement is claimed. Through 26 October there were 124 commits (a keyed audit chain with signed human approval, deployment hardening, parser fuzzing, the split of the app layer; summarised in [STATUS.md](../STATUS.md)). For this hackathon, on 28 October: the Tavily web-evidence card; the dashboard reader moved to Nemotron on Token Factory with Super as the default; a held-out quote-reader evaluation; the desk showing which model read each quote and the spans it labelled; the buyer's choice of the push-lower counter for a draft; a sample deal and message; drafts in the supplier's language; a daily cap on paid model calls; the reader's 30 s first-hop timeout and a prompt that defines each label category; freight, fees and duty fields on the paste form so the ranking can use landed cost; and the extension's automatic read with one key for every call. The README leads with these and keeps the earlier AMD material in a labelled section. Details: README, "What changed during the submission period".

**AI assistance.** The project's design, direction and trade-offs are the author's; AI coding tools (Cursor, Codebuff, Claude) assisted with writing code.
<!-- TODO(owner): confirm that you reviewed the decision logic and tests before keeping the sentence "all decision logic and tests were reviewed by the author" -->

**Built with.** Python, FastAPI, SQLite, NumPy, ONNX Runtime, a Chrome MV3 extension, Docker and Render, Nebius Token Factory, NVIDIA Nemotron, Tavily.

## Demo video script (3:00 or less)

Record on the live demo URL. Say the number that is on the screen. Do not say a model "decided" anything.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Home page, then **Open the live supplier desk** | "Small sellers lose margin negotiating factory quotes alone. This desk reads the quote, prices the counter, and I send it." |
| 0:15 | **Fill a sample deal**, key, **Open desk** (four suppliers) | "One deal, four suppliers, each with their own reserve belief." |
| 0:30 | **Use a sample message** (a rival's 24 and the supplier's own 27.80), **Add quote** | "A Chinese message with two prices in it." |
| 0:45 | The waiting item: "Read by nvidia/nemotron-3-super… on Nebius Token Factory", the labelled spans (reading takes about 12 to 21 s on the latest single run; the video cuts the wait) | "Nemotron on Nebius Token Factory labels each number; I confirm which one is the supplier's offer. It never gives me a number." |
| 1:10 | Pick 27.80, **Accept** | "Code reads the amount from that line, and I confirm it." |
| 1:25 | Supplier cards after a few quotes: ranking, default counter, push lower, hazard | "Every confirmed quote recomputes all four. The default is the best price on the table; push lower is the price with the highest expected surplus inside my risk cap." |
| 1:50 | **Use push lower in the draft** on the cheapest supplier; the draft changes | "I choose the push-lower figure. The draft changes, and the choice is in the audit chain." |
| 2:10 | **Check supplier on the web** | "Tavily finds public pages about this supplier, labelled reference only. The counter does not change." |
| 2:25 | The Chrome extension on a chat page with automatic read on; a new message appears on the desk by itself | "On a supplier chat, new messages reach the desk by themselves. The extension never types or sends." |
| 2:45 | The audit line on the desk (`#audit-line`); optionally the response of `GET /api/v1/desk/deals/{id}/audit/verify` or `python scripts/desk_verify.py <base-url>`; then README limits | "Every step is in a keyed chain, and a real send needs a signed approval; this demo pins no approver key, so it refuses sends. No factory has closed a deal through this yet; the limits are in the README." |

The ranking on screen is by unit price unless freight, fees and duty were typed for every supplier; the desk prints "Ranking basis: unit_price" or "Ranking basis: landed_cost" above the cards.

## Testing instructions for judges

1. Open **<https://tradeflow-ai-demo.onrender.com/desk>**.
2. Press **Fill a sample deal**, enter the API key **<JUDGE_KEY>**, and press **Open desk**. (Or type your own deal: four or five suppliers, one per line as `Name, email@example.test`.)
3. Press **Use a sample message**, choose a supplier, and press **Add quote**. The waiting item shows which Nemotron model read it and the spans it labelled. Pick the figure and press **Accept**.
4. Add a quote or two for other suppliers; the cards rank them and show the default and push-lower counters. (Optional: type freight, fees and duty together on every quote and the ranking uses landed cost; otherwise it uses the unit price.) Press **Use push lower in the draft** on any card.
5. On a supplier card, press **Check supplier on the web**.
6. Sending is not possible on the demo service: signed approval is required, no approver key is pinned there (`/ready` reports `signed_approval` as not ready), and the desk page attaches no signature, so **Approve and send** is refused and no mail is sent. Signing is done outside the page with `python -m src.desk.approval_signing`. To check the audit chain, read the audit line on the desk, open `GET /api/v1/desk/deals/{id}/audit/verify` on `/docs`, or run `python scripts/desk_verify.py <base-url>`; there is no verify button. Paid model calls are capped at 40 per UTC day per server process; past the cap the desk says so and keeps working on the rule reader.

The demo service stores data on an ephemeral disk, so a restart clears deals. Replace the `<JUDGE_KEY>` placeholder above before submitting (put the key only in the Devpost form, never in the repository), and rotate the key after judging.
