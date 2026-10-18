# Submission Checklist — AMD Developer Hackathon: ACT III

> Double-submission allowed: **Reinvent Commerce (main)** + **Evolus Business Agents on AMD**.
> Verify each box before pushing the final tag.
> **Online build ends 2026-10-17** (official live page; earlier drafts incorrectly said 10-18).

## Code / repo
- [x] Public GitHub repo: https://github.com/Fu93/tradeflow-ai
- [x] MIT LICENSE
- [x] README runs with one command (`python -m src.app.main`, then http://localhost:8000)
- [x] Dockerfile + docker-compose.yml (containerized)
- [x] No secrets committed (`.env` in `.gitignore`; only `.env.example`) — verified via `git grep`
- [x] `tests/` green — **376 tests + 98 subtests passing** (`python -m pytest tests -q`)

## Engine (decision core)
- [x] Bayesian belief update + vectorized Expectimax (`src/engine/`)
- [x] Robustness: gain vs fixed baseline, scenario sensitivity **and misbelief test** (`robustness.py`, `results/robustness.png`; see LIMITATIONS §13)
- [x] Benchmark: CPU measured in `results/bench.json`. The MI300X column is a theoretical roofline lower-bound estimate, not measured (`results/mi300x_roofline.json`, `results/benchmark.png`)
- [ ] **MI300X real measurement** — not run. Do not replace the estimate with a projected runtime
      (note: the $100 credit is NOT required to participate; tokenfactory access is sufficient for the model layer)

## Evolus integration — live-verified 2026-10-14
- [x] EvolusClient: REST (AI Tools) / agent chat (`src/evolus/client.py`)
- [x] Orchestrator: callback → engine → real Evolus AI Tool draft (`src/app/orchestrator.py`) — **no longer an offline stub**
- [x] **Event workspace** created from the official event code (ID stored in `.env`, not committed)
- [x] **AI Tools actually exercised, generating usage stats**: `email` (2.46 credits), `doc-structure` (2.12), `sentiment` (3.75)
- [ ] Build a **workflow / automation** (Evolus `Your AI team → Automations`): email-in → doc-structure → callback to `/webhook/evolus`
      — *client + callback receiver are ready; only the Evolus-side automation creation remains*
- [x] Create an **agent** (`Supplier Negotiation Analyst`, live-verified via `POST /v1/chat/completions`, HTTP 200)
- [x] AMD model bound to the agent (`modelKey: own/qwen3.8-flash-next`, `modelState: ok`)

## AMD technology
- [x] AMD tokenfactory calls recorded in `results/real_llm_benchmark_100.json` (no HTTP status stored). `scripts/verify_amd_endpoint.py` records HTTP 200 only when `status` is `ok`; that evidence file is not committed
- [x] Stage-2 semantic layer calling an AMD-hosted open model (`Qwen3.8-Flash-Next`)
- [x] **Compliance CONFIRMED**: organisers confirmed the tokenfactory endpoint **satisfies** the
      *"your own vLLM endpoint on AMD Developer Cloud"* requirement. No self-hosted vLLM needed.
- [ ] Capture `rocm-smi` / vLLM startup log / bench screenshot for the demo

## Demo / pitch
- [x] 4 figures (posterior, belief_update, benchmark, robustness)
- [ ] 3–5 min demo video (script: [DEMO_VIDEO_DIRECTOR_SCRIPT.md](DEMO_VIDEO_DIRECTOR_SCRIPT.md); current `results/demo_video.mp4` is ~15 s silent — re-record with the revised [DEMO_SCRIPT.md](DEMO_SCRIPT.md))
- [x] Pitch deck (see [PITCH.md](PITCH.md)) — `results/pitch.pptx` present; re-run `scripts/make_pitch.py` if PITCH.md changes again
- [ ] Human-in-the-loop approval gate shown

## Before submit
- [x] Refresh hardcoded counts in docs if tests changed: run `pytest tests -q`, update README / SUBMIT / PITCH figures
- [ ] Re-run `python scripts/refresh_market_data.py` so `knowledge/market_anchor.md` carries a fresh date
- [ ] Submit **before 2026-10-17** (not last minute)
- [ ] Selected challenge = Reinvent Commerce + Evolus track
- [ ] Tags: AMD / ROCm / vLLM / Evolus / Bayesian / Expectimax / AST-Guardrails / B2B-Commerce
- [ ] Cover image 1280×720 + demo video + pitch
- [ ] **Rotate both API keys** (they were shared in plaintext during development)
