# Demo Video — What to Film and Why

> Written 2026-10-15, after auditing the live system end to end.
> Companion to `DEMO_VIDEO_DIRECTOR_SCRIPT.md` (which is aspirational — see §5).
> **The current `results/demo_video.mp4` is 19.29s, 1280×720, and has no audio
> stream. It cannot be submitted as-is.**

---

## 1. Why the video is the highest-value remaining work

| Judging criterion | Weight | Currently served by |
|---|---|---|
| Application of Technology | 25% | README + repo (now correct) |
| **Presentation** | **25%** | **19s silent slideshow ← gap** |
| Business Value | 25% | PITCH + robustness numbers |
| Originality | 25% | PITCH + architecture |

Presentation is a full quarter of the score, and it is the only criterion with a
mandatory artifact still missing. Everything else is now in the repo and verified.

---

## 2. What to film — the two moments that are genuinely hard to fake

Most hackathon videos show a chatbot answering. That is exactly what this project
claims *not* to be, so a chat demo actively undercuts the pitch. Film the two
things a chatbot structurally cannot do:

### Moment A — The guardrail physically refuses (highest value, ~60s)

The AST guardrail is the one claim that is mechanically true and visually obvious.

1. Start the server: `python -m src.app.main`
2. Open `http://localhost:8000`
3. Create a session, then push a supplier message demanding **85% deposit** or a
   price **above the walk-away ceiling**.
4. The engine clamps the authorized price and trips the circuit breaker.

Verified live output for an ¥17.50 demand against a ¥16.20 ceiling:

```
POST /api/v1/sessions/guardrail-demo-02/round
  {"opponent_raw_text": "...先付 85% 定金，單價 ¥17.50 一分不少...",
   "opponent_offer_price": 17.50}

HTTP 200
autopilot_state   : "CIRCUIT_TRIPPED_ESCALATED"
circuit_tripped   : true
pending_human_approval : true
trip_reason       : "Opponent offer ¥17.50 breaches ceiling ¥16.20."
action_object.authorized_price : 13.62   (< ceiling, NOT 17.50)
action_object.deposit_pct      : 0.30    (85% demand rejected)
reply_text        : null                 (nothing emitted; human must decide)
```

**Why it lands:** you are showing a *refusal*, not a generation. The over-ceiling
price and the 85% deposit are both refused, and the API returns `reply_text: null`
with `pending_human_approval: true` — the system declines to answer on its own.
LLM demos cannot show a hard invariant enforced before generation.

**Framing note:** on a tripped round the reply field is `reply_text` (null by
design). On a normal round it is `auto_reply_text`. Don't script the wrong key.

### Moment B — Belief update moves with evidence (~45s)

1. Send round 1 at ¥14.80 → watch posterior mean and opening offer shift.
2. Send round 2 at ¥14.10 → posterior moves again, visibly.
3. Point at the chart: the curve is being *recomputed*, not re-prompted.

**Why it lands:** it is the visual proof of "math decides, LLM only writes."
Use the real chart in the Web Portal (SSE-driven), not the matplotlib frame.

### Moment C — Multi-supplier dynamic BATNA (~30s)

Click **Run Auction**. 4 suppliers get tactic labels
(`LEAN_IN` / `CROSS_ANCHOR` / `SIDELINE`) computed live from dynamic BATNA.
The 28.8M counter is a parameterised tensor-grid size (K x S x N_open x N_sched),
NOT a measured rollout/iteration count — say "tensor grid cells" on camera, not
"measured rollouts". It is still safe to show as a magnitude indicator.

---

## 3. What NOT to film (and why) — verified findings

### ❌ Do not film `extension/mock_chat.html` as if it were live

I read the file. It has **zero backend calls**. Typing into it echoes your text
back (`sendBtn` handler, line 222) and the "supplier" replies are a hardcoded
array `quoteSequence = [14.80, 14.10, 13.50, 13.00, 12.60]` (line 194). The
captcha slider is a CSS animation with a `setTimeout`.

If a judge inspects the repo after watching, a "live supplier negotiation" filmed
here is a visible misrepresentation — and this project's entire differentiation
is honesty about boundaries. Either:
- label it on screen as a **UI sandbox / mock**, or
- don't film it.

### ❌ Do not show the terminal without fixing the encoding

Your Python stdout is **cp950**. Any Chinese printed to the console renders as
`?????` (I reproduced this: `verify_live.py` output shows `�ӱ����B��`).
Run `chcp 65001` before recording, or set `PYTHONIOENCODING=utf-8`.

### ❌ Do not claim MI300X measurements

There is no AMD GPU on this host. `results/bench.json` is CPU-measured; the
MI300X figure is an analytical roofline lower bound. The existing silent video
frame `disc.png` already discloses this correctly — keep that discipline.

---

## 4. Suggested 3-minute structure

| Time | Shot | Criterion served |
|---|---|---|
| 0:00–0:20 | Problem: the two bad options (Translate markup / 7% agent kickback) | Business Value |
| 0:20–0:50 | One-liner + architecture diagram; "math decides, LLM writes" | Originality |
| 0:50–1:50 | **Moment A** — guardrail refuses an 85% deposit demand, live | Technology |
| 1:50–2:25 | **Moment B** — belief update across 2 real rounds | Technology |
| 2:25–2:45 | **Moment C** — Run Auction, 4 tactics, 28.8M tensor grid cells | Technology |
| 2:45–3:00 | Honest boundaries + repo URL + 332 tests green | Presentation |

Record with **audio narration**. A silent screen recording reads as unfinished.

---

## 5. On `DEMO_VIDEO_DIRECTOR_SCRIPT.md`

That script is well written but does **not** match what the system can currently
show. Two concrete mismatches I verified:

1. It specifies a **1080p, 3:30** video. The generated artifact is 1280×720 and
   19s. The generator (`scripts/make_demo_video.py`) is a matplotlib slideshow —
   it cannot produce the live-browser footage the script describes.
2. Shot 2 says "後驗底價在 **2ms** 內完成貝氏更新". Measured reality: a full
   round over HTTP took **2.54s** (solver is the bulk; Stage-1 classify is ~1ms).
   The script conflates Stage-1 classification latency with the whole round.
   Use ~2s, or say "Stage-1 classification in ~1ms" specifically.

Fix the script or film to reality — do not read the 2ms line over a 2.5s wait.

---

## 6. Pre-record checklist

- [ ] `chcp 65001` (or `PYTHONIOENCODING=utf-8`) — otherwise Chinese is `?????`
- [ ] `python -m src.app.main` running; `http://localhost:8000` returns 200
- [ ] Use **`opponent_raw_text` / `opponent_offer_price`** for API calls —
      misspelled fields now return HTTP 422 (was silently ignored before today's fix)
- [ ] `python -m pytest tests -q` → expect **327 passed + 68 subtests** (say this number)
- [ ] Narration audio recorded and verified on the exported file
- [ ] Re-check: `ffprobe results/demo_video.mp4` must show **both** a video and an
      audio stream, duration ≥ 180s
