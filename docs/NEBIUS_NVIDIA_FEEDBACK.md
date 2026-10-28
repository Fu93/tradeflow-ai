# Feedback on the Nebius and NVIDIA tools

Written for the feedback requirement of the Nebius x NVIDIA Global AI Hackathon. Everything below comes from what this repository does and records. Anything that needs a live measurement is listed at the end as open, not guessed.

## What we used

- **Nebius Token Factory**, the OpenAI-compatible `chat/completions` API at `https://api.tokenfactory.nebius.com/v1`. It is called with the Python standard library (`urllib`), with no vendor SDK. Code: `src/desk/extract.py`.
- **NVIDIA Nemotron** open models: `nvidia/Nemotron-3-Ultra-550b-a55b` (hop 1) and `nvidia/nemotron-3-super-120b-a12b` (hop 2) on Token Factory, plus the same Super model on NVIDIA's hosted endpoint (`https://integrate.api.nvidia.com/v1`) as an optional third hop.
- **Tavily Search API** for the Web evidence card (the bonus category). Code: `src/market/web_evidence.py`.

## How the integration is built

- One request body serves every hop: `temperature` 0, a `max_tokens` sized from the length of the supplier message (a floor and a ceiling, both registered constants), a `reasoning_budget`, `stream` off, and the supplier text wrapped in `DATA_START` / `DATA_END` markers so it is data, not instructions.
- The model answers with evidence spans and a class (own offer, rival price, fee, question, instruction, and so on). A price field in its answer is discarded. Deterministic code reads the amount from a span that occurs verbatim in the message. The model does not set a price.
- Failover is explicit. A timeout, a connection error, HTTP 429, HTTP 5xx, or a malformed body on Nebius opens the next hop. Any other 4xx does not, so a configuration error cannot silently fall through to another provider. The whole chain stops at a fixed time budget and the quote goes to human review.
- Every attempt is recorded with its provider, model, status, latency and a SHA-256 of the response, so per-hop latency is in the audit trail of each quote.

## What we had to design around

These are recorded as rationales next to the constants in `src/config/heuristics.py`.

1. **Truncated output on long messages.** Leaving the provider's default completion cap in place truncated the answer for a long supplier message. We size `max_tokens` from the message length, and a truncated answer fails closed even when the partial JSON looks valid (`tests/steps/test_extractor_failover.py`).
2. **Reasoning tokens share the completion budget.** The reasoning cap has to leave room for the JSON answer inside the output limit, so the two are set together.
3. **The Ultra model is not on the NVIDIA-hosted hop.** Failover to that host changes the model as well as the provider, so the third hop is the Super model.

## Suggestions

These are drafted from the points above. Please edit them against your own experience before submitting.

- A per-endpoint table of which Nemotron models are available where (Token Factory versus NVIDIA's hosted endpoint) would make a cross-provider failover design much easier to plan.
- Documentation that states how `max_tokens` and a reasoning budget interact for each Nemotron model would remove the trial and error that produced the sizing rule above.
- Error bodies that distinguish "rate limited" from "temporarily out of capacity" would let a client choose between waiting and failing over, if they do not already.

## Open: needs a live run before this is final

- Accuracy and latency of the Nemotron chain on real supplier messages. No measurement is committed under `results/`; the committed latency log is for a different endpoint.
- Whether each model honours `reasoning_budget` on Token Factory and on the NVIDIA-hosted endpoint. The code sends it on every hop, and this repository has no live test of how each model treats it.
- Tavily: no live call has been made from the development workspace yet. After the first real run, add the status codes seen for plan limits and how well results rank for Chinese-language supplier names.
