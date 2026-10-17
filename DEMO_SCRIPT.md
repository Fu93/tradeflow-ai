# TradeFlow AI — Demo Video Script (3–5 min target)

> 現況：`results/demo_video.mp4`（~15s silent visual, 3 Wows）已用真實數據離線產出。
> 本稿是旁白腳本；接上 Evolus key + vLLM 後，把 Scene 5 換成真 demo 錄影即可成片。

---

## Scene 0 — Hook (0:00–0:12)
> "Every B2B procurement team negotiates the same way: gut feel and a round number.
> Today's AI buyers are just chatbots — they'll draft the email but they can't tell you
> **why** an offer is optimal. So we built the opposite: a decision engine that does the math,
> and an LLM that only writes the words."

**Visual**: cover image / title slide.

## Scene 1 — Wow #1: Belief update (0:12–1:05)
> "We model the supplier as one of four archetypes with an unknown reserve price. We start with
> a prior. Then we watch evidence arrive. Every supplier reply triggers a Bayesian update — and
> the recommended opening move **moves with it**: 12.41, then 13.12, then back down to 12.51
> as the supplier concedes, with the posterior mean going from 1.08 to 1.14 and settling near 1.10.
> That's not the LLM guessing. That's the posterior."

**Visual**: `results/belief_update.png` / demo_video frames 0–120.
**Key line**: "Belief moves with the evidence — not a chatbot guess."

## Scene 2 — Wow #2: CPU vs MI300X (1:05–1:45)
> "The engine evaluates twenty thousand opponents against four hundred and eighty strategies —
> nine point six million negotiation rollouts — every time you ask for a recommendation.
> Vectorized CPU takes about one point seven seconds for that default twenty-thousand-sample grid.
> Architecturally, the data-movement lower bound on an AMD MI300X is on the order of a tenth of a
> millisecond — an analytical estimate from memory bandwidth, not a measured runtime."

**Visual**: `results/benchmark.png` / demo_video frames 140+.
**Key line**: "The engine's batched rollouts run as one vectorized broadcast on CPU — not chat tokens."

## Scene 3 — Wow #3: Robustness (1:45–2:35)
> "Here's the honest part. The opponent model is an assumption, so we tested what happens when
> we're **wrong**. The result is not flattering: our own report prints the word DEGRADES.
> If we underestimate the supplier's reserve by half, the breakdown rate goes from six point seven
> to eighty-eight percent. If we overestimate, margin turns negative and breakdown hits fifty percent.
> Across fixed-concession human baselines of three, five and ten percent we do break fewer deals —
> six point seven versus twenty-eight, twenty-eight and twenty-one — for about forty cents more per
> unit. But at a fifteen-percent-per-round baseline, we lose on margin: zero point two four seven
> against their zero point two five three, and their breakdown is worse at fourteen point five.
> So the honest claim is narrow: **our edge is conditional on an accurate prior.** That's the risk
> we're exposing, not hiding."

**Visual**: `results/robustness.png` (the panel literally prints "DEGRADES under prior miss").
**Key line**: "Our edge is conditional on the prior — at half the true reserve, breakdown is 88%."

## Scene 4 — Live agent (2:35–4:00)
> "Let's meet our Evolus agent — the Supplier Negotiation Analyst — backed by a Personal Library
> of live customs data, should-cost baselines and compliance policy. Tell it a supplier claims raw
> materials are up thirty percent, and it pulls the real HS6601 customs data — umbrella export
> prices actually fell — and refuses to invent numbers it doesn't have. It hands the case back to
> a human with two draft replies to choose from, so the LLM never decides alone. Our end-to-end
> workflow (email-in → extraction → callback) is the planned next step."

**Visual** (live-verified 2026-10-14; RAG latency ~40–120 s — narrate it as the deep async
decision layer): screen recording of the Evolus agent chat answering a supplier message with
knowledge-grounded analysis, ending in two human-selectable drafts.

## Scene 5 — Business value (4:00–4:35)
> "Professional procurement typically saves five to ten percent. On a one-million-dollar order,
> five percent is fifty thousand dollars. We're measuring that number in a vertical where it's real:
> umbrella export, anchored to actual HS6601 FOB prices."

## Scene 6 — Close (4:35–5:00)
> "Bayesian belief, Vectorized Expectimax on CPU (x86), Evolus agents driving the process, humans
> approving the risk. Procurement negotiation, quantified — not a chatbot.
> It's open source at github dot com slash Fu93 slash tradeflow dash AI."

---

## Recording checklist
- [ ] Scene 4 screen recording of the live Evolus agent (Personal Library RAG; allow 40–120 s)
- [ ] Voice-over, ~150 words per minute
- [ ] 1280×720 minimum (1080p preferred)
- [ ] Export MP4 (H.264) + host on YouTube/Streamable
- [ ] Link in lablab submission + README
