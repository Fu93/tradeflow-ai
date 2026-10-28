# Devpost submission pack: Nebius x NVIDIA Global AI Hackathon

Deadline: **Friday 30 October 2026, 17:00 London** (10:00 Pacific). Aim to submit by Friday 12:00 London. Rules: <https://nebiusglobalaihackathon.devpost.com/rules>.

## Checklist

| Requirement | State |
|---|---|
| Runs on Nebius Token Factory and uses an NVIDIA open model | The desk quote reader (`src/desk/extract.py`) and the dashboard reader (`src/engine/quote_read.py`) call Nemotron on Token Factory. One live check was made on 2026-10-28 (see "Single live check" below); it was a single call, not a benchmark. The held-out evaluation is in [QUOTE_READER_EVAL.md](QUOTE_READER_EVAL.md). |
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
- Every confirmed quote recomputes all suppliers. Each supplier has its own Bayesian reserve-price belief. The desk ranks them and gives two figures per supplier: the default (the best price already on the table) and a push-lower counter that maximises expected surplus inside the buyer's walk-away risk cap. The buyer chooses which one goes into the draft.
- Drafts are written in the supplier's language. Every step lands in a keyed audit chain, and a send needs a signed human approval that the server re-derives from the chain before anything leaves.

**Where each tool earns its place.**

- *Nebius Token Factory with NVIDIA Nemotron* reads messy supplier text. The default model is Nemotron Super with one retry on a timeout, a 429, a 5xx or a malformed body; Ultra can be selected with `TRADEFLOW_LLM_MODEL`. Every attempt is timed and recorded, and paid calls are capped per day (`TRADEFLOW_LLM_DAILY_CALLS`, default 40) so a small balance lasts the judging window; past the cap the desk keeps working on the rule reader and says so.
- *Tavily* adds a Web evidence card to each supplier: cited public pages about a product's wholesale prices and about a supplier's reviews. It sits beside the quote and never touches the price.

**The design choice that matters.** Neither the model nor the search can move a price. A price field in the model's answer is thrown away, and no decision module imports the search code; a test fails if one does. This keeps the model's job narrow: it reads wording, the Bayesian reserve-price and expected-surplus core decides the number, and a person approves every send. That boundary is tested; whether it is enough for real money is not something we have shown, because no factory has closed a deal through this prototype.

**Honest limits.** No factory has closed a deal through this prototype. The walk-away hazard is a transfer from an eBay Best Offer fit, not a measured factory rate. The extension's automatic read was tested on a mock chat page, not on the real 1688, Alibaba, WhatsApp or WeChat pages. **Not yet proven:** the Nemotron reader's accuracy (the held-out evaluation in [QUOTE_READER_EVAL.md](QUOTE_READER_EVAL.md) has a rules-only baseline; the Nemotron run is pending) and latency over more than a single call. The Tavily card shows what public pages say; it does not verify a supplier.

**Single live check (2026-10-28, about 12:17 London time, UTC).** One check each, on the live demo (<https://tradeflow-ai-demo.onrender.com>) running master commit `5c0321830e5cadb7a4571fbe9ca46fe510017895`. This is one call each, not a benchmark. The measured run used Ultra, the default at that time; the default has since been changed to Super, and Ultra can be selected with `TRADEFLOW_LLM_MODEL`.

- *Nebius Token Factory:* one sample quote (fictional `example.test` suppliers) was read. The demo's recorded extractor attempt shows provider `nebius`, model `nvidia/Nemotron-3-Ultra-550b-a55b`, status `ok`, latency 1.83 s.
- *Tavily:* `GET /api/v1/market/evidence?kind=market&product=...` returned HTTP 200 with status ok and 5 results in about 2.3 s.
- *Not measured:* token usage; the Super default was not exercised live yet; no accuracy numbers from this run; the Nemotron part of the quote-reader eval (`scripts/eval_quote_reader.py`) is still pending.


**What was updated during the submission period.** The first commit in this repository is dated 13 October 2026, after the submission period opened on 26 August, so all of the work falls inside the period. The repository began as an entry for the AMD Developer Hackathon ACT III, which has concluded; no lablab submission record for this project has been located and no prize or placement is claimed. Through 26 October there were 124 commits (a keyed audit chain with signed human approval, deployment hardening, parser fuzzing, the split of the app layer; summarised in [STATUS.md](../STATUS.md)). For this hackathon, on 28 October: the Tavily web-evidence card; the dashboard reader moved to Nemotron on Token Factory with Super as the default; a held-out quote-reader evaluation; the desk showing which model read each quote and the spans it labelled; the buyer's choice of the push-lower counter for a draft; a sample deal and message; drafts in the supplier's language; a daily cap on paid model calls; and the extension's automatic read with one key for every call. The README leads with these and keeps the earlier AMD material in a labelled section. Details: README, "What changed during the submission period".

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
| 0:45 | The waiting item: "Read by nvidia/nemotron-3-super… on Nebius Token Factory", the labelled spans | "Nemotron on Nebius Token Factory labels each number: this one is a rival's price, this one is the supplier's own offer. It never gives me a number." |
| 1:10 | Pick 27.80, **Accept** | "Code reads the amount from that line, and I confirm it." |
| 1:25 | Supplier cards after a few quotes: ranking, default counter, push lower, hazard | "Every confirmed quote recomputes all four. The default is the best price on the table; push lower is the price with the highest expected surplus inside my risk cap." |
| 1:50 | **Use push lower in the draft** on the cheapest supplier; the draft changes | "I choose the push-lower figure. The draft changes, and the choice is in the audit chain." |
| 2:10 | **Check supplier on the web** | "Tavily finds public pages about this supplier, labelled reference only. The counter does not change." |
| 2:25 | The Chrome extension on a chat page with automatic read on; a new message appears on the desk by itself | "On a supplier chat, new messages reach the desk by themselves. The extension never types or sends." |
| 2:45 | Audit verify, then README limits | "Every step is in a keyed chain and a send needs a signature. No factory has closed a deal through this yet; the limits are in the README." |

## Testing instructions for judges

1. Open **<DEMO_URL>/desk**.
2. Press **Fill a sample deal**, enter the API key **<JUDGE_KEY>**, and press **Open desk**. (Or type your own deal: four or five suppliers, one per line as `Name, email@example.test`.)
3. Press **Use a sample message**, choose a supplier, and press **Add quote**. The waiting item shows which Nemotron model read it and the spans it labelled. Pick the figure and press **Accept**.
4. Add a quote or two for other suppliers; the cards rank them and show the default and push-lower counters. Press **Use push lower in the draft** on any card.
5. On a supplier card, press **Check supplier on the web**.
6. Sending mail is off on the demo service. Approval stays on the desk. Paid model calls are capped per day; past the cap the desk says so and keeps working on the rule reader.

The demo service stores data on an ephemeral disk, so a restart clears deals. Replace the two placeholders above before submitting, and rotate the key after judging.
