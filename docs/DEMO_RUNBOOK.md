# Demo recording runbook

Use this to record the 3–5 minute narrated video. Speak the lines in quotes. If the screen shows a different number, say the number on the screen. `results/demo_video.mp4` is a short silent clip and is not this video.

The click path below was exercised in headless Chromium (`tests/test_dashboard_smoke.py`). The dashboard has no Approve button. The human-send beat is Copy on the simulated supplier page.

## Pre-flight

From the repo root:

```bash
cp -n .env.example .env
ss -ltnp 'sport = :8000' || true
python scripts/preflight.py
curl -sS localhost:8000/health
```

`cp -n` creates `.env` only when it is missing. Placeholder keys stay `__FILL_ME__`. The offline demo does not need a real key.

`preflight.py` never prints secret values. A free port 8000 fails that script on purpose, with the start command, because the recording needs the dashboard. A port held by something that is not TradeFlow fails with the `ss` command so you can see the pid.

Stop a stale server only after `ss` shows it is the process you started:

```bash
kill <pid>
```

Then start again and re-run preflight. `/health` should include `offline_demo` and `solver`.

## Two ways to start

Docker, which is what the narration in the first minute says:

```bash
cp -n .env.example .env
docker compose up --build
```

Compose starts when `.env` is absent (`env_file` is optional). The image listens as user `tradeflow` (uid 10001) on `0.0.0.0:8000`.

Without Docker:

```bash
python -m pip install -r requirements.txt
python -m src.app.main
```

If the process says the port is taken, use the `PORT=` command it prints.

## Clock and what to say

**0:00–0:25 — Title.** On camera, the repo and the dashboard masthead.

> TradeFlow AI. The engine picks the price. The model only phrases it. I send it.

**0:25–0:55 — Start.** Terminal shows the start command you actually ran. Then:

```bash
curl -sS localhost:8000/health
```

Open http://localhost:8000. Read `offline_demo` and `solver.actual` off the JSON.

> They are NumPy on CPU unless the JSON says otherwise.

**0:55–1:40 — One decision.**

1. On the dashboard, change Commodity to **Aerospace CNC metal case**. The status line names `DEMO_CNC_CASE_01`.
2. In a second terminal, record one simulated supplier line (this desk has no quote box):

```bash
curl -sS -X POST localhost:8000/api/v1/sessions/DEMO_CNC_CASE_01/round \
  -H 'Content-Type: application/json' \
  -d '{"opponent_raw_text":"原料上涨，最低 14.80","opponent_offer_price":14.80,"mode":"copilot"}'
```

3. Click **Reload** under Deals, then click `DEMO_CNC_CASE_01`.
4. Point at the recommended price and the breakdown probability on the decision card.

> Both come from the solver, not from a chat model.

**1:40–2:30 — Auction.** Click **Run auction**. Wait until the summary appears.

> The headline is savings versus the first-round best quote.

Read the versus-target figure next, including when that figure is negative. Then read the walk-away ceiling percent.

> The walk-away ceiling percent is secondary and labeled on the page.

Do not quote the ceiling figure as the headline. The committed report (`results/multi_supplier_auction.md`) is the source if you need the three labeled lines after the take. Say the numbers the page just showed.

**2:30–3:10 — Human send.** Open `extension/mock_chat.html` in the browser (the dashboard does not serve that file). Click **Simulate a normal concession**. Show the supplier line. If the extension side panel is loaded, show the decision card and **Copy**.

> The extension has no send button.

Do not paste the text into a live factory chat.

**3:10–3:50 — AMD.**

```bash
python scripts/verify_amd_endpoint.py
```

If it prints `status: ok`, show `models_http_status` and stop. If it prints `skipped_no_credentials`, say the language endpoint was not called in this take. Then open `results/mi300x_roofline.json` and read the label on screen: theoretical roofline lower-bound estimate, not measured.

> This column is a theoretical roofline lower-bound estimate, not measured.

On an AMD GPU box only:

```bash
TRADEFLOW_SOLVER_BACKEND=cupy python scripts/benchmark_amd_gpu.py
```

Read `ran_on_amd_gpu` exactly as printed. Otherwise do not run it, and do not narrate a speedup.

**3:50–4:20 — Limits.**

> No real factory has bought anything through this. The self-play table is a simulator. The held-out intent accuracy on the synthetic set is 63.08 percent, 195 of 500 held out, not the old training-set figure.

## After the take

Upload the narrated file on the form. Leave `results/demo_video.mp4` as the short silent clip it is. Form fields are in [LABLAB_FORM.md](LABLAB_FORM.md). The deck is `results/pitch.pptx`.
