# lablab form cheat-sheet

Copy the fenced blocks into the AMD Developer Hackathon ACT III form. Track: **Reinvent Commerce**. Working deadline used for this pack: **11:00 London (BST), 18 October 2026**. Confirm that timestamp on the live form before you submit. This file cannot click the form for you.

The long description is the same text as [SUBMISSION.md](../SUBMISSION.md). If either file changes, keep them identical.

## Title

```
TradeFlow AI
```

## Short description

```
One-person sourcing desk: a Bayesian Expectimax engine sets the price, an AMD-hosted model only phrases it, and a human sends the offer.
```

136 characters. If the form wants a shorter line:

```
Expectimax prices the offer. An AMD-hosted model only phrases it. A human sends it.
```

83 characters.

## Long description

~~~
Cross-border buyers sourcing from Chinese factories either negotiate alone, often with a language penalty, or pay an agent. TradeFlow is a prototype of the third arrangement: one operator, with the software doing the arithmetic and the person keeping the send button.

The decision core does not ask a language model what to pay. It keeps a belief about the supplier's reserve price, then runs a vectorised Expectimax search over opening prices and concession curves (`src/engine/solver.py`, `src/engine/multi_solver.py`). A Chrome extension (Manifest V3) shows the card. The buyer copies the text and sends it. The extension cannot press Send.

An optional second stage can call an OpenAI-compatible model to classify a supplier message or draft wording. The sample environment points that call at AMD's tokenfactory (`https://developer.amd.com.cn/radeon/api/v1`).

**Verified.** A committed log, `results/real_llm_benchmark_100.json`, records 348 calls to `Qwen3.8-Flash-Next` on 15 October 2026 (average latency 16.81 seconds, cost $0.108444). That file does not store an HTTP status code. Re-check with `python scripts/verify_amd_endpoint.py`. `status: ok` means that process received HTTP 200. A run without a key writes `skipped_no_credentials`. That is not a successful check, and no evidence file is committed.

**Estimated.** MI300X figures in `results/bench.json` are a theoretical roofline lower-bound estimate, not measured. Formula: `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)`. `working_set_mb` is the tracemalloc peak already stored on each bench row (`src/engine/benchmark.py:_measure_working_set_mb`). `scripts/regen_mi300x_roofline.py` rebuilds `results/mi300x_roofline.json` from those rows. The denominator is the AMD Instinct MI300X datasheet figure "5.3 TB/s max. peak theoretical" (https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf). The same PDF says "Up to 192 GB HBM3". The product page repeats Peak Memory Bandwidth 5.3 TB/s and Dedicated Memory Size 192 GB (https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html). Footnote MI300-18 (https://www.amd.com/en/products/accelerators/instinct/mi300.html) writes 5.325 TB/s as `8192 bits * 5.2 Gbps / 8`; the estimate uses the datasheet's 5.3 TB/s. No host DDR ratio is stated: the bench file does not name a CPU.

**Not run.** This repository has **not** recorded an AMD GPU run. `results/bench.json` is a CPU measurement (`is_gpu_active: false`). `scripts/benchmark_amd_gpu.py` is the command to time the solver on a GPU box. It sets `ran_on_amd_gpu` only when the visible device name looks like AMD.

On the synthetic self-play table (`results/self_play_benchmark.md`, 20 matches × 5 archetypes, 100 matches, seed 42) the engine's savings versus its walk-away ceiling range from 8.8% to 20.0%. Against a Hard-Bargainer it is slightly behind a fixed 5%-per-round concession (8.8% vs 9.2%). Seed 42 reproduces that table; it is not a variance estimate. The real-model log above is a different experiment: the AMD-hosted model, used as the buyer, closed at a deal rate of 61.11% of completed matches against a Bluffing Hardliner and recorded 0 budget violations in 94 completed matches. Those are sandbox results, not factory results.

The multi-supplier auction uses a different baseline again. Its headline is savings versus the first-round best quote (committed report: ¥13.26 to ¥12.60, 5.0%, ¥6,600). Versus target is shown on the same panel, including when that figure is negative (this run is −5.0% versus ¥12.00). Savings versus the walk-away ceiling, 22.2% and ¥36,000, are secondary and labeled as such. Do not quote 22.2% as the headline, and do not mix it with the self-play range above.

`cp .env.example .env && docker compose up --build` starts the dashboard with no API keys. Placeholder keys are ignored. `/health` reports `offline_demo` and the solver backend.
~~~

## Track

```
Reinvent Commerce
```

## Tags

Paste as the form allows. These match the GitHub topics the repo settings still need:

```
procurement
negotiation
expectimax
fastapi
amd
hackathon
commerce
```

## Links

```
https://github.com/Fu93/tradeflow-ai
```

No public deployment is claimed. Leave a live-demo URL blank unless you have actually deployed one. The video is the demo.

## Demo video

Upload the narrated 3–5 minute file you record from [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md).

`results/demo_video.mp4` is a short silent clip already in the repo. It is not the submission video. Do not upload it as the narrated demo.

## Cover image

[results/cover_1280x720.png](../results/cover_1280x720.png) — 1280×720.

## Deck

[results/pitch.pptx](../results/pitch.pptx) — 8 slides from the SUBMISSION.md outline. Regenerate with `python scripts/make_pitch.py` after installing `python-pptx`. Every measured figure on the slides is read from a source file when that command runs.
