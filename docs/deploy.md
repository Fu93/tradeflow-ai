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

## Persistence and backup (2026-10-24)

The desk's whole integrity story lives in one SQLite file: the audit chain,
the decisions, the drafts.

**Docker Compose.** `docker-compose.yml` mounts the named volume `desk-data`
at `/app/data` and pins `TRADEFLOW_DESK_DB=/app/data/desk.db` inside it, so
the desk database (and the session store beside it) survive container
rebuilds and image upgrades. The volume does **not** survive
`docker compose down -v`, and it is not a backup.

**Backup.** `scripts/backup_desk_db.py` takes an online backup with SQLite's
own backup API (consistent under WAL while the desk is writing), then opens
the copy and verifies every deal's chain before reporting success — a backup
that cannot prove its own integrity is deleted, not left behind wearing a
timestamp:

```bash
python scripts/backup_desk_db.py backup                 # data/backups/desk-<stamp>.db
python scripts/backup_desk_db.py backup --output /srv/backups/desk.db
```

Schedule it (host cron, once a day is enough at desk scale):

```cron
15 2 * * * cd /srv/tradeflow-ai && .venv/bin/python scripts/backup_desk_db.py backup >> data/backups/backup.log 2>&1
```

**Restore drill.** The desk must be stopped first — SQLite's backup API
takes copies, it does not merge a snapshot into a database that has moved
on. Move the live file aside, then restore with `--yes`; the restore
re-verifies every chain before reporting success:

```bash
docker compose stop tradeflow
mv data/desk.db data/desk.db.pre-restore
python scripts/backup_desk_db.py restore --from data/backups/desk-<stamp>.db --target data/desk.db --yes
docker compose start tradeflow
```

**Anchor.** A backup proves the chain verifies internally; the daily git
anchor (one line, or cron) is what makes tail truncation detectable from
outside the host:

```cron
0 3 * * * cd /srv/tradeflow-ai && .venv/bin/python scripts/anchor_chain_head.py --format json --output data/anchors/$(date +\%F).json
```

**Render.** The `starter` plan has an ephemeral disk: a redeploy or spin-down
loses the desk database, and no compose volume exists there. Persistent
disks on Render require a paid instance type (a `disk` block in
`render.yaml`). Until that is bought and configured, the honest description
of `tradeflow-ai-demo` is "demo state, lost on redeploy" — take backups
elsewhere before relying on it, and say so in any submission material.

**Build identity.** `GET /version` reports the app version, the schema
version, and `TRADEFLOW_GIT_SHA` (stamp it in the deploy pipeline; it prints
`unknown` when unset).
