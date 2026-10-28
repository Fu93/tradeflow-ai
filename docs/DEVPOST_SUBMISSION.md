# Devpost submission pack: Nebius x NVIDIA Global AI Hackathon

Deadline: **Friday 30 October 2026, 17:00 London** (10:00 Pacific). Aim to submit by Friday 12:00 London. Rules: <https://nebiusglobalaihackathon.devpost.com/rules>.

## Checklist

| Requirement | State |
|---|---|
| Runs on Nebius Token Factory and uses an NVIDIA open model | In code: the quote reader (`src/desk/extract.py`). **Still to do:** one live run with a real key, so the runtime call is proven, not only tested with fakes. |
| Track | Best Apps and Agents |
| Public repository with an open-source license visible in the About panel | MIT (`LICENSE`). Confirm the About panel on GitHub shows it. |
| README with setup and how NVIDIA models and Token Factory were used | The first sections of [README.md](../README.md) |
| Working demo URL | **To do.** Set the keys on the Render service, then paste the URL. |
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

**What it does.** TradeFlow is a one-person desk. A supplier message is pasted or arrives by mailbox. Nemotron, served by Nebius Token Factory, names the evidence spans and what each one is. Deterministic code reads the amount from a span that occurs verbatim in the message, a second rule reader has to agree, and a person confirms the number. Only then does a Bayesian reserve-price belief and an expected-surplus optimiser produce the counter. Every step lands in a keyed audit chain, and a send needs a signed human approval.

**Where each tool earns its place.**

- *Nebius Token Factory with NVIDIA Nemotron* reads messy supplier text. It has a failover chain (Ultra, then Super on Token Factory, then NVIDIA's hosted Super) and every hop is timed and recorded.
- *Tavily* adds a Web evidence card to each supplier: cited public pages about a product's wholesale prices and about a supplier's reviews. It sits beside the quote and never touches the price.

**The design choice that matters.** Neither the model nor the search can move a price. A price field in the model's answer is thrown away, and no decision module imports the search code; a test fails if one does. We think that is what makes an AI negotiation tool safe to put in front of real money.

**Honest limits.** No factory has closed a deal through this prototype. The walk-away hazard is a transfer from an eBay Best Offer fit, not a measured factory rate. No accuracy or latency measurement of the Nemotron chain is committed yet. The Tavily card shows what public pages say; it does not verify a supplier.

**Built with.** Python, FastAPI, SQLite, NumPy, ONNX Runtime, a Chrome MV3 extension, Docker and Render, Nebius Token Factory, NVIDIA Nemotron, Tavily.

## Demo video script (3:00 or less)

Record on the live demo URL. Say the number that is on the screen. Do not say a model "decided" anything.

| Time | On screen | Say |
|---|---|---|
| 0:00 | The desk page | "Small sellers lose margin negotiating factory quotes alone. This desk reads the quote, prices the counter, and I send it." |
| 0:15 | Open a deal with a product, a target, a ceiling and three suppliers | "One deal, several suppliers, each with their own reserve belief." |
| 0:40 | Paste a Chinese supplier message and add the quote | "Nemotron on Nebius Token Factory reads the message and names what each number is: an offer, a fee, a deposit, a rival's price." |
| 1:15 | The review queue and the shadow line | "The price is read by code from a span in the message. A rule reader has to agree. Auto-accept is off. I confirm the number." |
| 1:45 | The counter on the supplier card | "Now the counter is computed from confirmed quotes. The model never set it." |
| 2:00 | Click **Check supplier on the web** | "Tavily finds public pages about this supplier. They are labelled reference only. Watch the counter: it does not change." |
| 2:30 | Verify the audit chain; show that a send needs a signed approval | "Every step is in a keyed audit chain. A send needs a human signature." |
| 2:45 | README limits | "No factory has closed a deal through this yet. The limits are in the README." |

## Testing instructions for judges

1. Open **<DEMO_URL>/desk**.
2. Enter the API key **<JUDGE_KEY>** in the key field and open a desk with any product and three suppliers (one per line: `Name, email@example.test`).
3. Paste a supplier quote into **Paste a quote** and press **Add quote**.
4. On a supplier card, press **Check supplier on the web**.
5. Sending mail is off on the demo service. Approval stays on the desk.

The demo service stores data on an ephemeral disk, so a restart clears deals. Replace the two placeholders above before submitting, and rotate the key after judging.
