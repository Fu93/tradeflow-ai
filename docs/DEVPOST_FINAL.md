# TradeFlow AI — final submission copy

## Title

TradeFlow AI: the supplier negotiation desk

## Tagline

The model reads the quote. The math prices the counter. You approve the draft.

## Track

Best Apps and Agents. Optional bonus: Best Use of Tavily.

## Description

### The problem

A small cross-border seller compares factory quotes alone, often across a language gap. One message can mix a supplier's offer, a competitor's price, a deposit, minimum quantities and delivery terms. Misreading one number can turn a negotiation into an expensive mistake.

### What we built

TradeFlow is a working desk for four or five suppliers. Paste a message, review the price evidence, compare confirmed quotes, choose a counter and approve a draft in the supplier's language.

NVIDIA Nemotron on Nebius Token Factory labels the supplier's wording: their offer, a rival's price, a fee or a deposit. Deterministic code reads the amount; the model can point only at existing candidate figures. A person confirms every price before it affects the decision.

The desk compares unit prices, or landed costs when every active quote includes freight, fees and duty. It provides a default counter and an optional push-lower figure from a one-step expected-surplus model. If no amount fits the price and incremental walk-away risk limits, it withholds the counter. The final whole-cent amount is checked again before send.

A Chrome extension can import visible supplier messages. Tavily supplies cited web evidence about a supplier or product, displayed as reference only. Search results do not set prices or verify suppliers.

Every confirmation and counter choice is recorded in a keyed audit chain. The public demo uses an approval click and has no SMTP configured, so approval does not email anyone. Production supports signed human approvals. The extension never types or sends chat messages.

### Why these tools

Nebius Token Factory serves Nemotron at runtime through its OpenAI-compatible inference API. The model contributes language understanding and evidence labels; numerical decision code and a person retain control of commitments. Tavily adds traceable public references without feeding untrusted search text into the price solver.

### Evidence and limits

The repository includes automated decision, parser, API and browser tests. One earlier run of Nemotron Super on 40 original test messages read 20 of 22 labelled offers correctly, with two wrong reads elsewhere in the set; two items resemble an exposed development sample. This is a small synthetic evaluation, not a general accuracy benchmark. The rule reader and combined gate have different results, all published with the dataset and limitations.

No factory has closed a deal through this prototype. The walk-away model is transferred from eBay negotiations and is not a measured Chinese-factory rate. The extension was tested on a simulated chat page, not real supplier platforms. The demo database is ephemeral and model API attempts have a per-process daily cap; exhausted or unavailable inference falls back explicitly to rules.

### Work during the submission period

The repository began on 13 October 2026 within the submission period, originally targeting a concluded AMD hackathon. The current entry adds Nemotron on Nebius Token Factory, model evidence on the desk, Tavily search, sample workflows, human-selected push counters, multilingual drafts and a held-out reader evaluation. The 29 October review corrected risk feasibility, nonzero-duty conversion, exited-supplier ranking and retry accounting, and simplified the confirmation flow. Earlier AMD results are archived and are not presented as Nebius measurements.

AI coding tools assisted implementation. The project's direction and trade-offs are the author's. Tool feedback is in [NEBIUS_NVIDIA_FEEDBACK.md](NEBIUS_NVIDIA_FEEDBACK.md).

## Built with

Python, FastAPI, SQLite, NumPy, Chrome MV3, Docker, Render, Nebius Token Factory, NVIDIA Nemotron and Tavily.

## Testing instructions

1. Open https://tradeflow-ai-demo.onrender.com/desk and use the demo API key supplied privately in the Devpost testing field.
2. Choose **Fill a sample deal**, then **Open desk**. The contacts are fictional.
3. Choose **Use a sample message**, then **Add quote**. The message mentions a competitor's 24 and the supplier's own 27.80. Review the evidence and accept the supplier's own figure.
4. Compare the default and push-lower counters. Choose **Use push lower in the draft** and read the updated Chinese draft. Try a second supplier quote to see the comparison update.
5. Choose **Check supplier on the web** for reference-only results. API keys, outages or the model attempt cap may cause an explicit fallback.
6. **Approve and send** records approval on this demo; no email is transmitted. The audit verification endpoint is available through `/docs` with the same demo key.

Do not publish an API key in this file. Supply the actual key in the private testing instructions on Devpost, and a public YouTube URL in its video field.
