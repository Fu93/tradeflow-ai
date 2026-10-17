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

An optional second stage can call an OpenAI-compatible model to classify a supplier message or draft wording. The sample environment points that call at AMD's tokenfactory (`https://developer.amd.com.cn/radeon/api/v1`). A committed log, `results/real_llm_benchmark_100.json`, records 348 calls to `Qwen3.8-Flash-Next` on 15 October 2026 (average latency 16.81 seconds, cost $0.108444). Re-check with `python scripts/verify_amd_endpoint.py`. A run without a key writes `skipped_no_credentials`. That is not a successful check.

The solver backend is selected with `TRADEFLOW_SOLVER_BACKEND` (`numpy` by default, `cupy` / `rocm` when ROCm CuPy is installed, `auto` if the import works). This repository has **not** recorded an AMD GPU run. `results/bench.json` is a CPU measurement (`is_gpu_active: false`). The bench file does not name a CPU model. MI300X figures in that file are the roofline constant in results/bench.json (5300 GB/s, 5.3 TB/s), not a measured runtime. `scripts/benchmark_amd_gpu.py` is the command to time the solver on a GPU box. It sets `ran_on_amd_gpu` only when the visible device name looks like AMD.

On the synthetic self-play table (`results/self_play_benchmark.md`, 20 matches × 5 archetypes, 100 matches, seed 42) the engine's savings versus its walk-away ceiling range from 8.8% to 20.0%. Against a Hard-Bargainer it is slightly behind a fixed 5%-per-round concession (8.8% vs 9.2%). Seed 42 reproduces that table; it is not a variance estimate. The real-model log above is a different experiment: the AMD-hosted model, used as the buyer, closed at a deal rate of 61.11% of completed matches against a Bluffing Hardliner and recorded 0 budget violations in 94 completed matches. Those are sandbox results, not factory results.

`cp .env.example .env && docker compose up --build` starts the dashboard with no API keys. Placeholder keys are ignored. `/health` reports `offline_demo` and the solver backend.

## Demo video script (about 4 minutes)

Record the screen. Speak only what the screen just showed. If a command prints `skipped_no_credentials` or `ran_on_amd_gpu: false`, say that. Do not narrate a GPU speedup.

**0:00–0:25 — Title.** "TradeFlow AI. The engine picks the price. The model only phrases it. I send it."

**0:25–0:55 — Start.** Terminal: `cp .env.example .env && docker compose up --build`. When the healthcheck is healthy, open http://localhost:8000 and `curl -s localhost:8000/health`. Read `offline_demo` and `solver.actual` off the JSON. Say they are NumPy on CPU unless the JSON says otherwise.

**0:55–1:40 — One decision.** On the dashboard, open a demo session or run the single-supplier walkthrough you prepared. Point at the recommended price and the breakdown probability. Say both come from the solver, not from a chat model.

**1:40–2:30 — Auction.** Click Run Auction. Read the winner and the price the page shows. If you quote a savings percent, say which baseline the page is using (walk-away ceiling, first quote, or target). Do not swap those baselines.

**2:30–3:10 — Human send.** Open `extension/mock_chat.html` or the side panel. Show a supplier line, the decision card, and Copy. Say the extension has no send button. Paste nothing into a live factory chat unless you mean to.

**3:10–3:50 — AMD.** Run `python scripts/verify_amd_endpoint.py`. If you configured a real key and the script prints `status: ok`, show `models_http_status` and stop. If it prints `skipped_no_credentials`, say the language endpoint was not called in this take. Then show `results/bench.json` and say the MI300X column is an estimate, not a measurement. If you are on an AMD GPU box, run `TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py` and read `ran_on_amd_gpu` exactly as printed.

**3:50–4:20 — Limits.** One sentence: no real factory has bought anything through this, the self-play table is a simulator, and the held-out intent accuracy on the synthetic set is 63.08% (195 of 500 held out), not the old training-set figure.

## Slide outline

1. **Title.** TradeFlow AI. Reinvent Commerce. One operator, human send.
2. **Problem.** Overnight self-negotiation, or a commission agent. Prototype, not a deployed company.
3. **Split.** Solver decides. Model phrases. Human sends. Diagram from the README.
4. **Decision core.** Belief update plus vectorised Expectimax. Show one dashboard number you just captured. Do not invent a latency.
5. **AMD language layer.** Tokenfactory URL, the 2026-10-15 log (348 calls, 16.81 s average, cost $0.108444), and a screenshot of `verify_amd_endpoint.py` from the day you submit.
6. **AMD compute layer.** `TRADEFLOW_SOLVER_BACKEND`. CPU bench.json. State that no AMD GPU measurement is in the repo unless slide 5's sibling benchmark JSON says `ran_on_amd_gpu: true`.
7. **Evidence, with the baseline named.** Self-play 8.8% to 20.0% on the simulator, and the Hard-Bargainer comparison (8.8% vs 9.2%). Real-model deal rate 61.11% against the Bluffing Hardliner. Held-out intent accuracy 63.08% (195 of 500 held out).
8. **Limits and how to run.** Docker quick start. No factory deal. No shipped MI300X timing. MIT, public repo.

## Still for a person

- [ ] Confirm the lablab deadline and submit the form. Paste the short and long text above. Upload a 3–5 minute narrated video; `results/demo_video.mp4` in the repo is a short silent clip and is not that video.
- [ ] Run `docker compose up --build` once on a machine with Docker and confirm `/health` goes green. The audit environment had no Docker daemon.
- [ ] Put a real `VLLM_API_KEY` in an untracked `.env`, run `python scripts/verify_amd_endpoint.py` (and `--chat` if you want a completion), and keep the evidence file. Do not commit the key.
- [ ] On an AMD GPU box with ROCm and CuPy, run `scripts/benchmark_amd_gpu.py`. Commit that JSON only if `ran_on_amd_gpu` is true and you have read it.
- [ ] Rotate the Evolus and AMD keys if they were pasted into chat, tickets, or screenshots during development. A scan of this git history did not find a committed live key; `.env` is gitignored. Rotation is still the right move if the keys were shared outside the repo.
- [ ] `results/pitch.pptx` was generated from an older pitch. Rebuild it from this outline or do not upload it.
