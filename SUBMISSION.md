# TradeFlow AI — submission pack

Judge-facing copy for the AMD Developer Hackathon ACT III on lablab.ai. Track: **Reinvent Commerce**. Only claims that the repository can support are included. Working deadline used for this pack: **11:00 London (BST), 18 October 2026**. An earlier internal checklist said the live page showed 17 October, and an earlier draft of this file said 15:00. Confirm the timestamp on the form before uploading.

Public repo: https://github.com/Fu93/tradeflow-ai
License: MIT

## Short description

One-person sourcing desk: a Bayesian Expectimax engine sets the price, an AMD-hosted model only phrases it, and a human sends the offer.

(136 characters. If the form wants a shorter line, use: "Expectimax prices the offer. An AMD-hosted model only phrases it. A human sends it." — 83 characters.)

## Long description

Small cross-border sellers lose procurement margin when they negotiate factory quotes alone, often across a language gap, or pay a sourcing agent a commission. TradeFlow is a prototype of a third arrangement: one operator, a Bayesian Expectimax engine that sets the price, an optional AMD-hosted model that only reads a quote and phrases the reply, and a human who sends the offer. No factory deal has closed. A proposed revenue model is a desk subscription or a share of documented savings. No revenue is claimed.

The decision core does not ask a language model what to pay (src/engine/solver.py, src/engine/multi_solver.py). A language model is not the forecasting method. The default model for a new call is Gemma-4-31B-IT. Set VLLM_MODEL=Qwen3.8-Flash-Next to select the model in the committed log. That log, results/real_llm_benchmark_100.json, records 348 calls (average latency 16.81 s, cost $0.108444). No HTTP status is stored. python scripts/verify_amd_endpoint.py lists models and, with a real key, sends one chat. status: ok means that process received HTTP 200. skipped_no_credentials is not a successful check. No evidence file is committed. No committed Gemma latency.

The auction headline is savings versus the first-round best quote. Versus target stays visible, including when that figure is negative. Savings versus the walk-away ceiling are secondary. Self-play (results/self_play_benchmark.md, 20 matches x 5 archetypes, 100 matches, seed 42) runs from 8.8% to 20.0% versus that table's walk-away ceiling. Against a Hard-Bargainer, 8.8% versus a fixed baseline's 9.2%. The real-model log closed at 61.11% of completed matches against a Bluffing Hardliner, with 0 budget violations in 94 completed matches. Held-out intent accuracy is 63.08% (195 of 500 held out). results/bench.json is a CPU measurement (is_gpu_active: false).

The desk runs offline until AMD live mode is on and VLLM_API_KEY is a real secret. The model never sets our price.

## Demo video script (about 4 minutes)

Record the screen. Speak only what the screen just showed. If AMD live mode shows the offline fallback, say that. Do not narrate a call that did not happen. The public URL is whatever Render assigns after a deploy. This pack does not name a hostname. Locally, http://localhost:8000 is the stand-in. Clicks are in [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md).

**0:00 — Pain.** Small sellers lose margin negotiating factory quotes alone, or pay an agent.

**0:20–1:20 — Live demo.** Open the public URL, or localhost if you have not deployed. Turn on AMD live mode only if `/health` says a real key is configured. Paste a labeled sample. Point at the line `AMD GPU Cloud · model · latency · tokens`, or at the offline fallback if that is what the page shows. Point at the solver's recommended price and say the model did not set it.

**1:20 — How the price is computed.** Belief update, then Expectimax. The number on the card is the solver.

**1:50 — Auction.** Click Run auction. Read the headline versus the first-round best quote. Read versus target next, including when that figure is negative. The walk-away ceiling percent is secondary.

**2:30 — LLM versus TradeFlow.** Open `results/llm_vs_tradeflow.json`. Say it is a simulation, `ran_on_amd` is false, and read the budget-violation rate, deal rate, and average savings the file prints.

**3:10 — Business.** Target user, the proposed subscription or savings share, and the three competitor types. No market-size number.

**3:40 — Limits and roadmap.** No factory deal. No committed Gemma latency. No shipped MI300X timing. Human send stays. Held-out intent accuracy is 63.08% (195 of 500 held out).

## Market and business

Target user: a small cross-border seller who sources from factories and negotiates in chat.

Revenue model, proposed and not earned: a desk subscription, or a share of savings versus the first-round best quote. No revenue is claimed, because no factory deal has closed.

Competitors: LLM negotiation agents that let the model set the price; sourcing agents who take a commission; marketplace tools built into Alibaba-style sites. TradeFlow keeps the price on the solver.

Roadmap: a public offline demo from `render.yaml`, AMD live reads only when `VLLM_API_KEY` is set, a logged Gemma chat before any extraction-accuracy number, and the human keeps the send button.

No market-size number is stated. None was sourced for this pack.

## Human + AI workflow

Hybrid desk. The workflow is written for the Hybrid, Becreatives, and Vibe Generation criteria: the model reads and phrases, the solver prices, and the human sends. The extension cannot press Send.

## Offline comparison

`python scripts/compare_llm_vs_tradeflow.py` writes [results/llm_vs_tradeflow.json](results/llm_vs_tradeflow.json). It is a simulation with fixed seed 42 and 24 episodes. `ran_on_amd` is false. The stand-in buyer is `offline_accept_opening` (not an AMD call). TradeFlow budget-violation rate 0.00%, deal rate 50.00%, average savings versus the supplier opening 22.24% (deals only). The stand-in budget-violation rate is 100.00%, deal rate 100.00%, average savings 0.00%. That 22.24% is not the auction headline and not the secondary walk-away ceiling figure.

## Evidence notes

MI300X figures in `results/bench.json` are a theoretical roofline lower-bound estimate, not measured. Formula: `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)`. The denominator is the AMD Instinct MI300X datasheet figure "5.3 TB/s max. peak theoretical" (https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf). The same PDF says "Up to 192 GB HBM3". The product page repeats Peak Memory Bandwidth 5.3 TB/s and Dedicated Memory Size 192 GB (https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html). No AMD GPU run is recorded.

## Slide outline

1. **Title.** Procurement margin. Reinvent Commerce. Human send.
2. **Problem.** Negotiate alone, or pay an agent. Prototype, not a company.
3. **Market, revenue, competition.** No market-size number.
4. **Split.** Hybrid, Becreatives, Vibe Generation. Solver decides. Model phrases. Human sends.
5. **Decision core.** Belief update plus Expectimax. Do not invent a latency.
6. **AMD language layer.** Gemma-4-31B-IT is the default for a new call. The committed log is Qwen3.8-Flash-Next (348 calls, cost $0.108444). No committed Gemma latency.
7. **AMD compute layer.** `is_gpu_active: false`. The MI300X column is a theoretical roofline lower-bound estimate, not measured.
8. **Evidence.** Self-play 8.8% to 20.0%. Auction headline is the first-round best quote. Offline comparison labels from `results/llm_vs_tradeflow.json`.
9. **Limits and roadmap.** No factory deal. Public demo only after Render assigns a URL.

## Still for a person

- [ ] Confirm the lablab deadline and submit the form. Paste the short and long text above. Upload a 3–5 minute narrated video; `results/demo_video.mp4` in the repo is a short silent clip and is not that video.
- [ ] Run `docker compose up --build` once on a machine with Docker and confirm `/health` goes green. The audit environment had no Docker daemon.
- [ ] Apply `render.yaml` on Render (free Docker web service) and paste the hostname Render assigns into the form. This pack does not invent that URL.
- [ ] Put a real `VLLM_API_KEY` in an untracked `.env` or in the Render dashboard (`sync: false`). `python scripts/verify_amd_endpoint.py` lists models and sends one chat. `--no-chat` skips the chat. Keep the evidence file. Do not commit the key.
- [ ] On an AMD GPU box with ROCm and CuPy, run `scripts/benchmark_amd_gpu.py`. Commit that JSON only if `ran_on_amd_gpu` is true and you have read it.
- [ ] Rotate the Evolus and AMD keys if they were pasted into chat, tickets, or screenshots during development. A scan of this git history did not find a committed live key; `.env` is gitignored. Rotation is still the right move if the keys were shared outside the repo.
- [x] `results/pitch.pptx` and `results/pitch.pdf` are the 9-slide deck (`python scripts/make_pitch.py`). Glance at both before upload.
- [ ] Record the video using [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md). Paste the form fields from [docs/LABLAB_FORM.md](docs/LABLAB_FORM.md). `python scripts/preflight.py` is the checklist.
- [ ] GitHub topics are empty. This pack cannot edit repository settings. Add `procurement`, `negotiation`, `expectimax`, `fastapi`, `amd`, `hackathon`, `commerce`. The description already matches the pitch and can stay. The CI badge in the README points at `.github/workflows/ci.yml`. License on the repo is already MIT.
