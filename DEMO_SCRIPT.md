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
> the recommended opening move **moves with it**: from ¥12.30 in round one to ¥14.23 by round five.
> That's not the LLM guessing. That's the posterior."

**Visual**: `results/belief_update.png` / demo_video frames 0–120.
**Key line**: "Belief moves with the evidence — not a chatbot guess."

## Scene 2 — Wow #2: CPU vs MI300X (1:05–1:45)
> "The engine evaluates twenty thousand opponents against four hundred and eighty strategies —
> nine point six million negotiation rollouts — every time you ask for a recommendation.
> A naive loop takes forty-six seconds. Vectorized CPU takes about one second.
> On an AMD MI300X running ROCm, it's under a tenth of a second."

**Visual**: `results/benchmark.png` / demo_video frames 140+.
**Key line**: "The GPU runs the engine's batched rollouts — not chat tokens."

## Scene 3 — Wow #3: Robustness (1:45–2:35)
> "Here's the honest part. The opponent model is an assumption. So we tested what happens if we're
> **wrong** — off by fifty percent on the supplier's reserve price. The recommendation holds.
> We also compared against how humans actually negotiate: fixing a flat concession every round.
> Whatever the human baseline — three, five, ten, or fifteen percent — the engine breaks the deal
> **less often**: six point seven percent versus thirty-one point seven, and earns about two yuan
> more per unit."

**Visual**: `results/robustness.png` / demo_video frames 250+.

## Scene 4 — Live end-to-end (2:35–4:00)
> "Let's run a real negotiation. We start a document-extraction workflow triggered by an inbound
> email. The extractor turns the PDF quotation into structured JSON. Every number the agent uses
> for reasoning comes from this engine — the agent calls our service, not its own arithmetic.
> It hands the case back to a human when the breakdown risk exceeds fifteen percent, so LLMs never
> invent a price."

**Visual** (requires Evolus key + vLLM live): screen recording of `POST /webhook/evolus` →
engine JSON → Evolus AI Tool email draft → human approval.

## Scene 5 — Business value (4:00–4:35)
> "Professional procurement typically saves five to ten percent. On a one-million-dollar order,
> five percent is fifty thousand dollars. We're measuring that number in a vertical where it's real:
> umbrella export, anchored to actual HS6601 FOB prices."

## Scene 6 — Close (4:35–5:00)
> "Bayesian belief, vectorized MCTS on AMD MI300X, Evolus agents driving the process, humans
> approving the risk. Procurement negotiation, quantified — not a chatbot.
> It's open source at github dot com slash Fu93 slash tradeflow dash AI."

---

## Recording checklist
- [ ] Scene 4 screen recording with live Evolus + vLLM
- [ ] Voice-over, ~150 words per minute
- [ ] 1280×720 minimum (1080p preferred)
- [ ] Export MP4 (H.264) + host on YouTube/Streamable
- [ ] Link in lablab submission + README
