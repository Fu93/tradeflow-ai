# TradeFlow AI

[![CI](https://github.com/Fu93/tradeflow-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Fu93/tradeflow-ai/actions/workflows/ci.yml)

## 30-second pitch

Small cross-border sellers lose procurement margin when they negotiate factory quotes alone, often across a language gap, or pay a sourcing agent a commission. TradeFlow is a one-person desk for that problem: a Bayesian Expectimax engine chooses the price, an optional language model only reads and phrases the offer, and a human sends it. No factory has closed a deal through this prototype.

Benchmark numbers below come from this repository's CPU runs or from a parameterised simulator. The full boundary note is [docs/LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](docs/LIMITATIONS_AND_REAL_WORLD_FINDINGS.md). The claim-by-claim check is [docs/DATA_INTEGRITY.md](docs/DATA_INTEGRITY.md).

Track: **Reinvent Commerce** (AMD Developer Hackathon ACT III, lablab.ai). License: MIT.

## Quick start (Docker)

```bash
git clone https://github.com/Fu93/tradeflow-ai.git
cd tradeflow-ai
cp .env.example .env
docker compose up --build
```

Open http://localhost:8000. `GET /health` is the compose and image healthcheck.

Leave the `__FILL_ME__` keys as they are. The dashboard, the solver, and the supplier-intent regex/ONNX stage run offline. The container listens on `0.0.0.0:$PORT` (compose publishes **8000**). The solver inside this image is **NumPy on CPU**. The compose file does not attach a GPU.

Without Docker (use `python3` if `python` is not on your PATH):

```bash
python -m pip install -r requirements.txt
python -m src.app.main
```

Open http://localhost:8000. If port 8000 is already taken, the process prints that and exits. Start on another port with `PORT=8001 python -m src.app.main` (the suggestion in the message names the interpreter you actually ran).

Chrome copilot (does not send messages): load `extension/` unpacked, or open `extension/mock_chat.html`. No API key is required for this offline demo. Placeholder values in `.env.example` stay `__FILL_ME__`.

```bash
python scripts/preflight.py  # before a recording; prints FIX lines, never secret values
python -m pytest tests -q
python -m src.cli            # one-supplier walkthrough
python -m src.cli --multi    # four-supplier auction
```

Recording clicks and narration: [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md). Form fields: [docs/LABLAB_FORM.md](docs/LABLAB_FORM.md). The slide deck is [results/pitch.pptx](results/pitch.pptx) and [results/pitch.pdf](results/pitch.pdf) (nine slides, rebuilt from the SUBMISSION.md outline).

## Human + AI workflow

Hybrid desk. The workflow is written for the Hybrid, Becreatives, and Vibe Generation criteria. The model reads a supplier message and may phrase a reply. The solver sets every number. The human copies the draft and sends it. The Chrome extension cannot press Send.

## Market and business

Target user: a small cross-border seller who sources from factories and negotiates in chat.

Revenue model, proposed and not earned: a desk subscription, or a share of savings versus the first-round best quote. No revenue is claimed.

Competitors: LLM negotiation agents that let the model set the price; sourcing agents who take a commission; marketplace tools built into Alibaba-style sites.

Roadmap: deploy the offline demo, turn on AMD live mode only when `VLLM_API_KEY` is a real secret, publish extraction accuracy only after a logged Gemma run, and keep the human on the send button.

No market-size number is stated. None was sourced for this pack.

## Public demo

[render.yaml](render.yaml) is a Docker web service on the free plan, health check `GET /health`, offline by default. Secrets (`VLLM_API_KEY`, `EVOLUS_API_KEY`, `EVOLUS_APP_ID`, `TRADEFLOW_API_KEY`, `TRADEFLOW_WEBHOOK_TOKEN`) are `sync: false` so a Blueprint sync does not overwrite them. The file does not set `PORT`; Render injects it and the process binds `0.0.0.0:$PORT`. This repository does not name a public hostname until a deploy exists. The filesystem on that plan is ephemeral, so session files do not survive a restart.

Routes that can call the AMD endpoint (`POST /api/v1/quotes/read` with AMD live mode, and `POST /api/v1/classify/semantic` when stage 2 is requested) are capped in-process at 8 requests a minute per client address and 30 an hour for the whole process. A 429 does not echo the quote or any credential. With no real `VLLM_API_KEY`, those routes stay on the offline reader and the cap is not consumed.

## Interface

The desk is set in self-hosted Fraunces and Outfit (SIL Open Font License) on a bone-paper palette. The four-supplier auction is the first panel. Each quote is drawn as a share of the walk-away ceiling returned by the auction API. The page says the suppliers are simulated archetypes, and approval stays with the buyer.

The auction headline is savings versus the first-round best quote. In the committed run ([results/multi_supplier_auction.md](results/multi_supplier_auction.md)) that is ¥13.26 down to ¥12.60, which is 5.0% and ¥6,600 on 10,000 units. Versus target is on the same panel, including when that figure is negative (this run is −5.0% versus the ¥12.00 target, ¥−6,000). Savings versus the walk-away ceiling, 22.2% and ¥36,000, are secondary and labeled as such. Those three baselines are not interchangeable with the self-play range in Honest limits.

`extension/mock_chat.html` is the same desk language used as a sandbox. It is not a live supplier site. The banner on that page says the quotes are simulated.

Screenshots:

- Desktop, before a run: [docs/screenshots/dashboard-desktop.png](docs/screenshots/dashboard-desktop.png)
- Mobile, before a run: [docs/screenshots/dashboard-mobile.png](docs/screenshots/dashboard-mobile.png)
- Auction panel before Run auction: [docs/screenshots/auction-before-desktop.png](docs/screenshots/auction-before-desktop.png), [docs/screenshots/auction-before-mobile.png](docs/screenshots/auction-before-mobile.png)
- Auction panel after Run: [docs/screenshots/auction-after-desktop.png](docs/screenshots/auction-after-desktop.png), [docs/screenshots/auction-after-mobile.png](docs/screenshots/auction-after-mobile.png)
- Full desk after Run: [docs/screenshots/dashboard-desktop-after-run.png](docs/screenshots/dashboard-desktop-after-run.png)
- Simulated supplier sandbox: [docs/screenshots/mock-chat-desktop.png](docs/screenshots/mock-chat-desktop.png), [docs/screenshots/mock-chat-mobile.png](docs/screenshots/mock-chat-mobile.png)
- Same desk at 360px and 768px: [docs/screenshots/dashboard-360.png](docs/screenshots/dashboard-360.png), [docs/screenshots/dashboard-768.png](docs/screenshots/dashboard-768.png). The mobile shot above is 390px. Desktop is 1280px.
- Circuit-breaker card with a long Traditional Chinese product name: [docs/screenshots/circuit-360.png](docs/screenshots/circuit-360.png)
- Extension side panel at 360px, with a long reference and a long price: [docs/screenshots/sidepanel-360.png](docs/screenshots/sidepanel-360.png)

## Architecture

```mermaid
flowchart LR
    Buyer["Human buyer"]
    Ext["Chrome MV3 copilot"]
    API["FastAPI dashboard"]
    Solver["Expectimax solver<br/>NumPy, or CuPy if configured"]
    Phrase["Optional phrasing call<br/>AMD tokenfactory or vLLM"]
    Buyer --> Ext --> API
    API --> Solver
    Solver --> API
    API -.-> Phrase
    API --> Buyer
```

The solver updates a reserve-price belief and searches opening prices and concession curves. The Chrome extension shows a decision card. The buyer copies and sends. Nothing in the extension clicks Send.

## AMD integration

Three buckets. They are not interchangeable.

**Verified.** A committed log, [results/real_llm_benchmark_100.json](results/real_llm_benchmark_100.json), records completed calls to the AMD tokenfactory base `https://developer.amd.com.cn/radeon/api/v1` (2026-10-15, model `Qwen3.8-Flash-Next`, 348 calls, average latency 16.81 s, cost $0.108444). That file does not store an HTTP status code. The default model for a new Stage-2 call is `Gemma-4-31B-IT`. Set `VLLM_MODEL=Qwen3.8-Flash-Next` to select the logged model again. No committed Gemma latency. `python scripts/verify_amd_endpoint.py` writes `results/amd_endpoint_evidence.json` (gitignored). With a real key it GETs `/models` and POSTs one short chat, and the file records chat status and latency. `status: ok` means that process received HTTP 200. No evidence file is committed. `skipped_no_credentials` is not a successful check. The key is not written to the file. `--no-chat` lists models only.

**Estimated.** The MI300X numbers in [results/bench.json](results/bench.json) and [results/mi300x_roofline.json](results/mi300x_roofline.json) are a theoretical roofline lower-bound estimate, not measured. Formula: `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)`. `working_set_mb` is the tracemalloc peak already stored on each `results/bench.json` row (`src/engine/benchmark.py:_measure_working_set_mb`, peak bytes / (1024 * 1024)). `scripts/regen_mi300x_roofline.py` rebuilds the JSON from those rows. It does not re-time the CPU. The denominator is the AMD Instinct MI300X datasheet figure "5.3 TB/s max. peak theoretical" (https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf). The same PDF says "Up to 192 GB HBM3". The product page repeats Peak Memory Bandwidth 5.3 TB/s and Dedicated Memory Size 192 GB (https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html). Footnote MI300-18 on https://www.amd.com/en/products/accelerators/instinct/mi300.html writes 5.325 TB/s as `8192 bits * 5.2 Gbps / 8`; the estimate uses the datasheet's 5.3 TB/s. A host DDR speed is not in the datasheet and `results/bench.json` does not name a CPU, so no DDR ratio is stated.

**Not run.** This repository has no measured MI300X or other AMD GPU solve. [results/bench.json](results/bench.json) has `is_gpu_active: false`. `ran_on_amd_gpu` is true only when CuPy sees a device name that looks like AMD. [docs/deploy.md](docs/deploy.md) is a runbook for a future GPU box, not a log of a run that happened.

What is wired in code:

1. **Language endpoint.** Stage-2 intent classification can call an OpenAI-compatible API. `.env.example` points `VLLM_BASE_URL` at the tokenfactory base above and `VLLM_MODEL` at `Gemma-4-31B-IT`. The dashboard AMD live mode toggle calls `POST /api/v1/quotes/read` in parallel with the solver round. The round's price comes from the explicit offer or the deterministic reader. Model output cannot change it.
2. **Solver backend, selectable.** `TRADEFLOW_SOLVER_BACKEND=numpy` (default) or `cupy` / `rocm` / `auto`. `cupy` requests CuPy and falls back to NumPy if the import fails. Explicit `use_gpu=True/False` still overrides the environment. Code: `src/engine/backend.py`, `src/engine/solver.py`.
3. **GPU-box benchmark.** On a machine with ROCm and CuPy:

   ```bash
   TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py
   ```

   The same command runs on CPU. Read `ran_on_amd_gpu` as printed. The HBM3 column it prints is the theoretical roofline lower-bound estimate, not measured.

## Offline comparison

`python scripts/compare_llm_vs_tradeflow.py` writes [results/llm_vs_tradeflow.json](results/llm_vs_tradeflow.json). Fixed seed 42, 24 episodes, solver sample count in the file. `ran_on_amd` is false. The stand-in is `offline_accept_opening` (not an AMD call). TradeFlow budget-violation rate 0.00%, deal rate 50.00%, average savings versus the supplier opening 22.24% over deals only. The stand-in budget-violation rate is 100.00%, deal rate 100.00%, average savings 0.00%. That 22.24% is not the auction headline and not the secondary walk-away ceiling figure. `--live` still does not spend a multi-episode AMD run. One real chat is `python scripts/verify_amd_endpoint.py`.

## Honest limits

- No closed-loop purchase. Supplier lines in the stress transcript are a script.
- Synthetic self-play ([results/self_play_benchmark.md](results/self_play_benchmark.md), 20 matches × 5 archetypes, seed 42) is a parameterised simulator. Engine savings in that table run from **8.8% to 20.0%** versus the walk-away ceiling used by that table. Against the Hard-Bargainer the engine's 8.8% is behind the fixed 5%-per-round baseline's 9.2%. Seed 42 reproduces that table; it is not a variance estimate. It is not the auction headline.
- The real-model log above is a different experiment. Against a Bluffing Hardliner the recorded deal rate is 61.11%, with 0 budget violations in 94 completed matches.
- Held-out accuracy of the on-device intent model on the synthetic set is **63.08%** (195 of 500 held out). An older 92% figure was a training-set score.
- Stage-2 is off the synchronous round path by default because the recorded AMD calls average 16.81 s. AMD live mode is a separate request with a timeout and an offline fallback label. No committed Gemma latency.
- What was checked, and what was not: [docs/VERIFICATION.md](docs/VERIFICATION.md). `python scripts/check_claims.py` fails if a number in this file or in SUBMISSION.md drifts from its source, and if a doc states an MI300X figure that is not in `results/mi300x_roofline.json`.
- The Evolus client is an optional email-draft adapter. The Evolus workflow was not created. This submission is not a second hackathon track.

Submission copy, a 3–5 minute demo script, and a slide outline: [SUBMISSION.md](SUBMISSION.md).

License: MIT.
