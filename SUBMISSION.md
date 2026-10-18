# TradeFlow AI — submission pack

Judge-facing copy for the AMD Developer Hackathon ACT III on lablab.ai. Track: **Reinvent Commerce**. Only claims that the repository can support are included. Working deadline used for this pack: **11:00 London (BST), 18 October 2026**. An earlier internal checklist said the live page showed 17 October, and an earlier draft of this file said 15:00. Confirm the timestamp on the form before uploading.

Public repo: https://github.com/Fu93/tradeflow-ai
License: MIT

## Short description

One-person sourcing desk: a Bayesian Expectimax engine sets the price, an AMD-hosted model only phrases it, and a human sends the offer.

(136 characters. If the form wants a shorter line, use: "Expectimax prices the offer. An AMD-hosted model only phrases it. A human sends it." — 83 characters.)

## Long description

Cross-border buyers sourcing from Chinese factories either negotiate alone, often with a language penalty, or pay an agent. TradeFlow is a prototype of the third arrangement: one operator, with the software doing the arithmetic and the person keeping the send button.

The decision core does not ask a language model what to pay. It keeps a belief about the supplier's reserve price, then runs a vectorised Expectimax search over opening prices and concession curves (`src/engine/solver.py`, `src/engine/multi_solver.py`). A Chrome extension (Manifest V3) shows the card. The buyer copies the text and sends it. The extension cannot press Send.

An optional second stage can call an OpenAI-compatible model to classify a supplier message or draft wording. The sample environment points that call at AMD's tokenfactory (`https://developer.amd.com.cn/radeon/api/v1`).

**Verified.** A committed log, `results/real_llm_benchmark_100.json`, records 348 calls to `Qwen3.8-Flash-Next` on 15 October 2026 (average latency 16.81 seconds, cost $0.108444). That file does not store an HTTP status code. Re-check with `python scripts/verify_amd_endpoint.py`. `status: ok` means that process received HTTP 200. A run without a key writes `skipped_no_credentials`. That is not a successful check, and no evidence file is committed.

**Estimated.** MI300X figures in `results/bench.json` are a theoretical roofline lower-bound estimate, not measured. Formula: `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)`. `working_set_mb` is the tracemalloc peak already stored on each bench row (`src/engine/benchmark.py:_measure_working_set_mb`). `scripts/regen_mi300x_roofline.py` rebuilds `results/mi300x_roofline.json` from those rows. The denominator is the AMD Instinct MI300X datasheet figure "5.3 TB/s max. peak theoretical" (https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf). The same PDF says "Up to 192 GB HBM3". The product page repeats Peak Memory Bandwidth 5.3 TB/s and Dedicated Memory Size 192 GB (https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html). Footnote MI300-18 (https://www.amd.com/en/products/accelerators/instinct/mi300.html) writes 5.325 TB/s as `8192 bits * 5.2 Gbps / 8`; the estimate uses the datasheet's 5.3 TB/s. No host DDR ratio is stated: the bench file does not name a CPU.

**Not run.** This repository has **not** recorded an AMD GPU run. `results/bench.json` is a CPU measurement (`is_gpu_active: false`). `scripts/benchmark_amd_gpu.py` is the command to time the solver on a GPU box. It sets `ran_on_amd_gpu` only when the visible device name looks like AMD.

On the synthetic self-play table (`results/self_play_benchmark.md`, 20 matches × 5 archetypes, 100 matches, seed 42) the engine's savings versus its walk-away ceiling range from 8.8% to 20.0%. Against a Hard-Bargainer it is slightly behind a fixed 5%-per-round concession (8.8% vs 9.2%). Seed 42 reproduces that table; it is not a variance estimate. The real-model log above is a different experiment: the AMD-hosted model, used as the buyer, closed at a deal rate of 61.11% of completed matches against a Bluffing Hardliner and recorded 0 budget violations in 94 completed matches. Those are sandbox results, not factory results.

The multi-supplier auction uses a different baseline again. Its headline is savings versus the first-round best quote (committed report: ¥13.26 to ¥12.60, 5.0%, ¥6,600). Versus target is shown on the same panel, including when that figure is negative (this run is −5.0% versus ¥12.00). Savings versus the walk-away ceiling, 22.2% and ¥36,000, are secondary and labeled as such. Do not quote 22.2% as the headline, and do not mix it with the self-play range above.

`cp .env.example .env && docker compose up --build` starts the dashboard with no API keys. Placeholder keys are ignored. `/health` reports `offline_demo` and the solver backend.

## Demo video script (about 4 minutes)

Record the screen. Speak only what the screen just showed. If a command prints `skipped_no_credentials` or `ran_on_amd_gpu: false`, say that. Do not narrate a GPU speedup.

**0:00–0:25 — Title.** "TradeFlow AI. The engine picks the price. The model only phrases it. I send it."

**0:25–0:55 — Start.** Terminal: `cp .env.example .env && docker compose up --build`. When the healthcheck is healthy, open http://localhost:8000 and `curl -s localhost:8000/health`. Read `offline_demo` and `solver.actual` off the JSON. Say they are NumPy on CPU unless the JSON says otherwise.

**0:55–1:40 — One decision.** On the dashboard, open a demo session or run the single-supplier walkthrough you prepared. Point at the recommended price and the breakdown probability. Say both come from the solver, not from a chat model.

**1:40–2:30 — Auction.** Click Run auction. Read the winner and the price. The headline is savings versus the first-round best quote. Read versus target next, including when that figure is negative. The walk-away ceiling percent is secondary and labeled on the page. Do not quote the ceiling figure as the headline.

**2:30–3:10 — Human send.** Open `extension/mock_chat.html` or the side panel. Show a supplier line, the decision card, and Copy. Say the extension has no send button. Paste nothing into a live factory chat unless you mean to.

**3:10–3:50 — AMD.** Run `python scripts/verify_amd_endpoint.py`. If you configured a real key and the script prints `status: ok`, show `models_http_status` and stop. If it prints `skipped_no_credentials`, say the language endpoint was not called in this take. Then show `results/mi300x_roofline.json` and say the column is a theoretical roofline lower-bound estimate, not measured. If you are on an AMD GPU box, run `TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py` and read `ran_on_amd_gpu` exactly as printed.

**3:50–4:20 — Limits.** One sentence: no real factory has bought anything through this, the self-play table is a simulator, and the held-out intent accuracy on the synthetic set is 63.08% (195 of 500 held out), not the old training-set figure.

## Slide outline

1. **Title.** TradeFlow AI. Reinvent Commerce. One operator, human send.
2. **Problem.** Overnight self-negotiation, or a commission agent. Prototype, not a deployed company.
3. **Split.** Solver decides. Model phrases. Human sends. Diagram from the README.
4. **Decision core.** Belief update plus vectorised Expectimax. Show one dashboard number you just captured. Do not invent a latency.
5. **AMD language layer.** Tokenfactory URL, the 2026-10-15 log (348 calls, 16.81 s average, cost $0.108444), and a screenshot of `verify_amd_endpoint.py` from the day you submit.
6. **AMD compute layer.** `TRADEFLOW_SOLVER_BACKEND`. CPU `results/bench.json` (`is_gpu_active: false`). The MI300X column is a theoretical roofline lower-bound estimate, not measured (`results/mi300x_roofline.json`). State that no AMD GPU measurement is in the repo unless a sibling benchmark JSON says `ran_on_amd_gpu: true`.
7. **Evidence, with the baseline named.** Self-play 8.8% to 20.0% on the simulator, and the Hard-Bargainer comparison (8.8% vs 9.2%). Real-model deal rate 61.11% against the Bluffing Hardliner. Held-out intent accuracy 63.08% (195 of 500 held out).
8. **Limits and how to run.** Docker quick start. No factory deal. No shipped MI300X timing. MIT, public repo.

## Still for a person

- [ ] Confirm the lablab deadline and submit the form. Paste the short and long text above. Upload a 3–5 minute narrated video; `results/demo_video.mp4` in the repo is a short silent clip and is not that video.
- [ ] Run `docker compose up --build` once on a machine with Docker and confirm `/health` goes green. The audit environment had no Docker daemon.
- [ ] Put a real `VLLM_API_KEY` in an untracked `.env`, run `python scripts/verify_amd_endpoint.py` (and `--chat` if you want a completion), and keep the evidence file. Do not commit the key.
- [ ] On an AMD GPU box with ROCm and CuPy, run `scripts/benchmark_amd_gpu.py`. Commit that JSON only if `ran_on_amd_gpu` is true and you have read it.
- [ ] Rotate the Evolus and AMD keys if they were pasted into chat, tickets, or screenshots during development. A scan of this git history did not find a committed live key; `.env` is gitignored. Rotation is still the right move if the keys were shared outside the repo.
- [ ] `results/pitch.pptx` was generated from an older pitch. Rebuild it from this outline or do not upload it.
- [ ] GitHub topics are empty. This pack cannot edit repository settings. Add `procurement`, `negotiation`, `expectimax`, `fastapi`, `amd`, `hackathon`, `commerce`. The description already matches the pitch and can stay. The CI badge in the README points at `.github/workflows/ci.yml`. License on the repo is already MIT.
