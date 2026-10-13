# Submission Checklist — AMD Developer Hackathon: ACT III

> Double-submission allowed: **Reinvent Commerce (main)** + **Evolus Business Agents on AMD**.
> Verify each box before pushing the final tag.

## Code / repo
- [x] Public GitHub repo: https://github.com/Fu93/tradeflow-ai
- [x] MIT LICENSE
- [x] README runs with one command (`python -m src.cli --figures`)
- [x] Dockerfile + docker-compose.yml (containerized)
- [x] No secrets committed (`.env` in `.gitignore`; only `.env.example`)
- [x] `tests/` green (engine / evolus-mock / benchmark)

## Engine (decision core)
- [x] Bayesian belief update + vectorized MCTS/expectimax (`src/engine/`)
- [x] Robustness: gain vs fixed baseline + prior ±50% sensitivity (`robustness.py`, `results/robustness.png`)
- [x] Benchmark: CPU measured + MI300X projected (`benchmark.py`, `results/benchmark.png`, `bench.json`)
- [ ] **MI300X real measurement** — replaces projection once AMD credits droplet is up

## Evolus integration (≥2 building blocks + BYO vLLM)
- [x] EvolusClient: workflow execute / AI Tools (email) / agent chat (`src/evolus/client.py`)
- [x] Orchestrator: webhook → engine → draft email (`src/app/orchestrator.py`)
- [ ] Create **event workspace** from the official event link (kickoff link; self-made workspace does NOT count)
- [ ] Build workflow: email-in → Document Extractor → callback to `/webhook/evolus`
- [ ] Mount a vLLM endpoint (AMD MI300X or Fireworks fallback) as an Evolus model
- [ ] Confirm workspace actually exercises the blocks (Evolus judges by usage stats)

## AMD technology
- [x] Engine rollouts run batched (GPU story: 9.6M rollouts, ~0.4s projected on MI300X)
- [ ] Deploy `rocm/vllm:rocm10.1.0...` + Qwen2.5-14B + `--tool-call-parser hermes` on MI300X
- [ ] Capture `rocm-smi` / vLLM startup log / bench screenshot for the demo

## Demo / pitch
- [x] 4 figures (posterior, belief_update, benchmark, robustness)
- [ ] 3–5 min demo video (script: §7 in TradeFlow_PlanB_Final.md)
- [ ] Pitch deck (10 slides: problem / one-liner / why-not-chatbot / arch / 3 wows / business value / AMD / Evolus / robustness / next steps)
- [ ] Human-in-the-loop approval gate shown

## Before submit
- [ ] Confirm exact deadline time on the lablab project page (schedule "To be announced")
- [ ] Submit **morning of 2026-10-17** (not last minute)
- [ ] Selected challenge = Reinvent Commerce + Evolus track
- [ ] Tags: AMD / ROCm / vLLM / MI300X / Evolus / Bayesian / MCTS
- [ ] Cover image 1280×720 + demo video + pitch
