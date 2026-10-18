# Optional GPU-box runbook

This file is a checklist for a machine you have not used yet. Nothing in this repository was produced by following it. The default demo does not need a GPU.

## App, anywhere

```bash
cp .env.example .env
docker compose up --build
```

The service listens on port **8000** (`PORT` inside the container). `GET /health` is the healthcheck. Placeholder keys stay unset, and the solver runs on NumPy.

## Solver on an AMD GPU

Install a ROCm build of CuPy in the environment that runs the process, then:

```bash
TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py
```

Read `ran_on_amd_gpu` in `results/amd_gpu_benchmark.json`. If it is false, the file is a CPU timing plus a theoretical roofline lower-bound estimate, not measured. The formula is `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)` in `results/mi300x_roofline.json`. The 5.3 TB/s figure is the datasheet quote "5.3 TB/s max. peak theoretical" (https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/data-sheets/amd-instinct-mi300x-data-sheet.pdf), which also says "Up to 192 GB HBM3". Do not replace `results/bench.json` with a projected MI300X runtime. This runbook has not been executed in this repository.

## vLLM on a ROCm host

A separate container can serve an OpenAI-compatible API. Point `VLLM_BASE_URL` and `VLLM_API_KEY` in `.env` at that server, then:

```bash
python scripts/verify_amd_endpoint.py
```

Do not publish the app and vLLM on the same host port. The TradeFlow compose mapping is `8000:8000`.

## What not to claim afterwards

- A green `/health` does not mean the solver used a GPU.
- `status: ok` from the endpoint script means HTTP 200 from the URL in the evidence file. It does not name the GPU that served the tokens.
- Evolus, if you configure it, is an optional drafting adapter. It is not a second submission track.
