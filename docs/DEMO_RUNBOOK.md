# Demo recording runbook

Use this to record the narrated video on the clock below. Speak the lines in quotes. If the screen shows a different number, say the number on the screen. `results/demo_video.mp4` is a short silent clip and is not this video.

The public URL is the hostname Render assigns after you apply `render.yaml`. This repository does not record one. On a laptop, http://localhost:8000 is the stand-in. Say which one you opened.

The click path for the auction and the quote box is covered by `tests/test_dashboard_smoke.py`. AMD live mode stays off unless `/health` reports `llm.amd_live_available`. If you turn it on without a key, read the offline fallback label. Do not say a model answered.

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

Then start again and re-run preflight. `/health` should include `offline_demo` and `solver`. `llm.amd_live_available` is true only when `VLLM_API_KEY` is a real secret.

## Two ways to start

Docker, for a local take:

```bash
cp -n .env.example .env
docker compose up --build
```

Compose starts when `.env` is absent (`env_file` is optional). The image listens as user `tradeflow` (uid 10001) on `0.0.0.0:$PORT` (compose publishes 8000).

Without Docker:

```bash
python -m pip install -r requirements.txt
python -m src.app.main
```

If the process says the port is taken, use the `PORT=` command it prints.

A public take uses the Render Blueprint in `render.yaml` (free Docker web service, health check `/health`). Do not put the API key in the Blueprint file. Set `VLLM_API_KEY` in the dashboard. Until that deploy exists, record against localhost and say so.

## Clock and what to say

**0:00 — Pain.** On camera, the masthead.

> Small sellers lose margin negotiating factory quotes alone, or they pay an agent. This desk prices the offer. I still send it.

**0:20–1:20 — Live demo.** Open the public URL if you have deployed, otherwise http://localhost:8000, and say which.

1. Change Commodity if you want a sandbox session. The status line names the session.
2. Click **Sample 1 — simulation**. The picture is labeled simulation. The text box fills with the transcript.
3. Leave **AMD live mode** off if `/health` says the key is not configured. Click **Read this quote**.
4. Point at the status line. Offline, it says the offline fallback. With a real key and the switch on, it says `AMD GPU Cloud`, the model name, the latency, and the token count from that response.
5. Point at the recommended counter.

> That price is the solver. The reading above does not set it.

**1:20 — How the price is computed.** Stay on the decision card.

> The next offer matches the best other confirmed quote, or a push-lower counter from a one-step heuristic. The model can word the reply. It does not choose the number.

**1:50 — Auction.** Click **Run auction**. Wait until the summary appears.

> The headline is savings versus the first-round best quote.

Read the versus-target figure next, including when that figure is negative. Then read the walk-away ceiling percent.

> The walk-away ceiling percent is secondary and labeled on the page.

Do not quote the ceiling figure as the headline. Say the numbers the page just showed.

**2:30 — LLM versus TradeFlow.** Open `results/llm_vs_tradeflow.json`, or run:

```bash
python scripts/compare_llm_vs_tradeflow.py
```

> This is a simulation. ran_on_amd is false. The stand-in is not Gemma.

Read the TradeFlow and stand-in budget-violation rate, deal rate, and average savings from the file. The savings figure is versus the supplier opening on deals, not the auction headline.

**3:10 — Business.**

> The user is a small cross-border seller. The revenue idea is a subscription or a share of savings. Nothing has been earned. The alternatives are LLM negotiators, commission agents, and marketplace tools. I am not stating a market size.

**3:40 — Limits and roadmap.**

> No factory has bought anything through this. There is no committed Gemma latency. The self-play table is a simulator. Held-out intent accuracy on the synthetic set is 63.08 percent, 195 of 500 held out. The MI300X column is a theoretical roofline lower-bound estimate, not measured. Next is a public URL, then a logged Gemma chat, and the human still sends.

## AMD probe, if you show a terminal

```bash
python scripts/verify_amd_endpoint.py
```

With a real key this lists models and sends one short chat. The evidence file is gitignored. If it prints `skipped_no_credentials`, say the language endpoint was not called. `--no-chat` skips the completion.

## After the take

Upload the narrated file on the form. Leave `results/demo_video.mp4` as the short silent clip it is. Form fields are in [LABLAB_FORM.md](LABLAB_FORM.md). The deck is `results/pitch.pptx` and `results/pitch.pdf`.
