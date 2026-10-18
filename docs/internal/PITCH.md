# TradeFlow AI — Pitch Deck (10 slides)

> AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD
> **Thesis**: we are not selling a procurement tool. We are a **one-person, AI-run cross-border trading company**.

---

## Slide 1 — Problem: cross-border sourcing is not a chat, it is a game of information asymmetry

A US seller who wants to source from a Chinese factory has two options today:

| Option | Cost |
|---|---|
| **Negotiate it yourself, overnight** | Grind through Google Translate. The moment the factory hears "foreign buyer", the price jumps 20%; you cannot read the subtext in "tooling is billed separately" or "we'll use second-grade material" |
| **Hire an on-shore agent** | 7%+ commission, and the agent takes a kickback from the factory; the deposit demand climbs to 80%, capacity slips, and both sides point at each other |

**Why can't one LLM chatbot solve this?**
The brief says it outright: *"Do not use a language model as the only forecasting method."*
Published evaluations (NegotiationArena / Bianchi et al., ICLR 2024; AgenticPay, arXiv:2602.06008; Cattle Trade, arXiv:2605.14537) show that an LLM without game-theoretic scheduling becomes strategically short-sighted against hard and bluffing opponents.

So we ran **our own experiment** on AMD endpoints (100 matches, 348 real inference calls, $0.108). Against the bluffing archetype the real model **closed only 61%** of deals (the same self-play artefact shows the engine at 100%, i.e. 20/20 on only 20 samples — cross-artefact comparison must not be read as a clean ratio). Its actual failure mode is **walking away and conceding too fast**, not overspending.

> **Honest disclosure.** Our parametric simulator predicted a "violation rate of 20%–40%" (current artefact; earlier versions said 12%–44%). The real model measured **0/94 violations**, an exact one-sided 95% upper bound of **3.1%** — enough to **reject** any of those simulator numbers at that confidence level. We report the **non-replication** as it happened (LIMITATIONS §2.3) and repositioned the AST guardrail from "catches violations" to **defense in depth**.

---

## Slide 2 — One-liner: a trading company with a staff of one

**TradeFlow AI is an AI-native trading company.**

We hand the day-to-day process of cross-border sourcing to a set of agents.
There is exactly one operator, and **understanding supplier messages, checking prices, risk control and tactic generation are all done by the system — but the send action is always human**: the system never issues an offer on your behalf.

> **"The math owns strategy; the LLM writes the idiom."**
> The decision core is a pure-numeric vectorised Expectimax solver that emits a language-neutral structured decision object.
> The LLM only renders it into idiomatic supplier-facing Chinese and into English executive decision cards.

**Why that framing?** Because the Evolus track asks for *"a real process that a real company runs every day"* — and nothing is more real than the company itself.

---

## Slide 3 — Why not a chatbot: adversarial self-play (recorded artefact, 100 matches)

> **Match-count correction.** The earlier title said "750 matches". That number came from a
> hardcoded header string in `scripts/run_arena_benchmark.py` and had nothing to do with the
> actual parameters. The header now interpolates the real parameters, and after the decision-core
> fixes (ghost buyer history, opponent reserve binding, a baseline that is allowed to violate),
> the artefact was regenerated with **20 matches per archetype × 5 archetypes = 100 matches**,
> seed=42 (`results/self_play_benchmark.md`). The table below is that artefact.

| Metric | TradeFlow Engine | Parametric Buyer Proxy |
|---|---|---|
| **Budget-violation rate** | **0.0%** (AST hard constraint; the baseline is now **allowed** to violate so the comparison means something) | **20.0% – 40.0%** (breaks the ceiling under pressure) |
| **Savings vs the bluffing archetype** | **15.0%** | 3.6% |
| **Savings range** (5 archetypes) | 8.8% – 20.0% | 3.6% – 17.0% |

**The key difference**: the parametric proxy panics under pressure; TradeFlow's overreach is physically blocked by the AST at compile time.

> **Honest disclosure.** These 100 matches come from a **parametric behavioural simulator calibrated against academic literature**, not from real cloud-LLM calls.
> Twenty matches per cell means limited resolution ("100.0%" is 20/20); the engine and a "fixed 5% per round" baseline differ on 5/5 archetypes, but the engine still trails the baseline on the Hard-Bargainer (8.8% vs 9.2%) — we keep that cell as measured instead of tuning the engine to win every cell.
> The full behavioural formulas are published in `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §2 for local reproduction.

---

## Slide 4 — Architecture: why the decoupling matters

```
supplier message ──► Stage 1: deterministic regex ladder (~1ms, 0 tokens)
                          │ no match
                          ▼
                   Stage 2: on-device ONNX + AMD-hosted open model (semantic inference)
                          │
                          ▼
        ┌───────────────────────────────────┐
        │  Numeric decision core (no LLM)   │
        │  · Bayesian belief update (Gaussian-uniform mixture) │
        │  · Vectorised Expectimax solver   │
        │  · Multi-dimensional logrolling   │
        └───────────────┬───────────────────┘
                        ▼
        TacticalActionObject (language-neutral single source of truth)
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
  AST hard guardrail (compile-time)   Bilingual surface rendering
  · authorized_price <= ceiling       · Chinese: coastal veteran-buyer idiom
  · deposit <= 60%                    · English: CPO decision card
```

**Design rule**: the LLM never picks a price. It can only render a decision that has already been **computed by the math and validated by the AST**.

> **Two deposit limits.** The logrolling business rule caps deposit at ≤50% (the stricter rule); the AST hard red line is 60% as a last line of defense (see `src/engine/logrolling.py`).

---

## Slide 5 — Demo: three verifiable moments

1. **Belief convergence.**
   When a supplier quote lands, the posterior reserve mean corrects immediately and the opening offer moves with it. Recorded trajectory (`src.cli` `run_demo()`, target ¥12.00, the same run plotted in `results/belief_update.png`):
   **¥12.41 → ¥13.12 → ¥12.91 → ¥12.51 → ¥12.51**, posterior mean 1.080 → 1.144 → 1.124 → 1.098.
   Note this is "adjust with evidence", not a monotone climb: when the supplier concedes, our counter moves down too. Every round is traceable and explainable.

2. **The AST guardrail catching something live.**
   When a factory demands an 85% deposit or an out-of-range unit price, the AST raises `ASTMandateViolationError` at compile time and the system degrades to a safe script — the out-of-range promise is **never** sent.

3. **Multi-supplier dynamic BATNA.**
   Four suppliers in parallel, each round computing the best outside option excluding supplier $i$, and splitting them into `LEAN_IN` / `CROSS_ANCHOR` / `SIDELINE`.

---

## Slide 6 — Business value: one company, one person

**This is what separates us from every other team: we are not proposing a product, we are operating a company.**

| | Traditional path | TradeFlow AI |
|---|---|---|
| Staff | A procurement team, or a 7% commission agent | **One person** |
| Per-round response | Human reading and replying | Stage-1 deterministic ~1ms (**default path**); a full round measures ~2.6s (solver body ~1.7s at 20K samples; measured on the x86-64 development CPU); Stage-2 semantic enhancement is **optional**, adding 15–45s (re-measured 2026-10-17) |
| Floor protection | Human self-control | **AST compile-time blocking, 0% breach** |
| Middleman kickbacks | Not auditable | **No middleman exists** |
| Idiom fluency | Years of experience | Templated coastal-veteran-buyer phrasing |
| Power to contract | The buyer | **The buyer** (the system proposes, it never issues the offer) |

**Quantified gain** (vs the opening quote): savings **8.8% – 20.0%**; budget violations **0%**.
For a first order of 10,000 umbrellas: open ¥14.80 → the engine's first counter ¥13.02 (recorded in `results/real_stress_test_log.json` R1), about ¥1.78 per unit, **¥17,800** total. (**A single-supplier worked example**; the multi-supplier auction is a different scenario — 22.2% / ¥36,000 against the walk-away ceiling, 5.0% / ¥6,600 against the first-round quote — different baselines, and the marginal gain over a fixed-concession baseline is about +¥0.40/unit; the three scenarios are not interchangeable.)

> *Provenance: the figures come from the recorded bids in `results/real_stress_test_log.json`, a single-supplier negotiation vs opening quote worked example. They are not the final contract price of any specific factory, nor a site-wide statistic.*

---

## Slide 7 — AMD integration (the honest version)

**We do not say "because it needs 192GB of VRAM" — that would be wrong.**
The multi-supplier demo path (the Web "Run Auction" default) evaluates **28.8M rollout positions** (9.6M per round × 3 rounds), the **arithmetic product** $K \times S \times N_{open} \times N_{sched}$ — **not a measured execution count**. The rollouts are produced by a single vectorised broadcast; 28.8M is only "how many parameter combinations are evaluated at once".
**The working set is whatever `src/engine/benchmark.py` measures with tracemalloc: a 9.6M-position run peaks at about 678.1 MB** (`results/bench.json`; the old `n × 8 bytes` formula gives 73.2 MB, an underestimate of roughly 9.3×, and is no longer used). Extrapolating the theoretical 96M upper bound with the same formula still gives only about 384 MB. This is **bandwidth-bound**, not capacity-bound.

**Where AMD actually matters:**
1. **One ROCm abstraction**: `_xp()` in `src/engine/solver.py` lets the same tensor-game code be developed on an x86-64 CPU (NumPy SIMD) and switched to an AMD Instinct GPU (ROCm/CuPy) for production in one line.
2. **AMD tokenfactory model service (measured)**: the Stage-2 semantic layer really calls an open model served at `https://developer.amd.com.cn/radeon/api`; latency and token cost are measured values.

> **Honest boundary.** The MI300X figure is a theoretical roofline lower-bound estimate, not measured (`results/mi300x_roofline.json`). This project has not executed the solver on an AMD GPU. `results/bench.json` does not name a CPU.

---

## Slide 8 — Evolus integration (all real calls)

The Evolus track is judged on workspace **usage statistics**, so we list only what has actually produced usage:

| Building block | Status | Measured usage |
|---|---|---|
| **AI Tool: `email`** | Verified | 2.46 credits — drafting the business reply |
| **AI Tool: `doc-structure`** | Verified | 2.12 credits — quote text → structured JSON (unit price / MOQ / lead time / deposit / tooling fee all extracted correctly) |
| **AI Tool: `sentiment`** | Verified | 3.75 credits |
| **App-driven REST integration** | Verified | `orchestrator.py` receives the callback → runs the engine → calls Evolus to draft |

**End-to-end measured output:**
> *"Based on our cost analysis, we can offer **¥13.12** per unit for an initial order of **500 units**."*

The draft correctly cites the engine's target price (¥13.12) and the supplier's actual quote (¥15 / MOQ 500), which shows Evolus and our decision engine are wired to **real data** rather than to a claim in a document.

> **Not done yet**: the workflow is not created (`GET /api/v1/workflows` returns an empty array). The agent (Supplier Negotiation Analyst) and the Personal Library knowledge base are created and verified (2026-10-14, LIMITATIONS §12). We do not claim the workflow is finished.

---

## Slide 9 — Facing the limitations

We believe **that in front of a technical review, drawing the boundary honestly is worth more than a perfectly self-consistent sandbox.**

1. **This is Expectimax, not CFR.** It assumes the opponent's strategy distribution is known (four archetypes at 25% prior each). If real factories fall outside that distribution, the optimality guarantee no longer holds.
2. **"Confidence" is not a calibrated probability**; it is a convergence measure of posterior variance.
3. **The AST only protects the structured object** and cannot understand rendered natural language; the language layer relies on templates and regex filtering.
4. **BATNA assumes suppliers price independently.** In industrial clusters such as Yiwu or Shaoxing, an informal price cartel weakens cross-pressure.
5. **Evaluation-validity boundary.** The intent benchmark is an in-distribution regression set (overlapping with the regex vocabulary); the 500-item set is programmatically generated (66 base templates recycled, only 132 distinct after masking numbers). Neither is a human-blind set. What we report is the **held-out split disjoint from training (195 items) at 63.08%**. The 92.00% quoted earlier was a **training-set score** (the old exporter trained the ONNX head on the whole OOD-500 set and evaluated on it) and must not be used as a generalisation number.
6. **Stage-2 latency is 15–45 seconds** (reasoning model on a shared inference service; re-measured locally 2026-10-17). We do not claim "end-to-end <3ms".
7. **No closed-loop validation with a real factory.** Every metric comes from the sandbox and simulation.

---

## Slide 10 — Next steps

1. **Finish the Evolus workflow**: create it (email-in → doc-structure → callback). The agent (bound to an AMD-hosted model) and the Personal Library knowledge base were created and verified on 2026-10-14 (Slide 8 and LIMITATIONS §12); the workflow is the remaining piece.
2. **Accumulate real negotiation logs**: once 10,000+ real trade negotiation logs exist, move to actual CFR (Counterfactual Regret Minimization) for offline adaptive iteration.
3. **Measure on AMD hardware**: once an instance is available on AMD Developer Cloud, replace the roofline projection with measured numbers.
4. **Productisation**: from "proving a one-person AI-native trading company works" to "a replicable one-person cross-border operating system".

---

> **Document status.** This deck supersedes every earlier pitch version. Architecture details follow
> [TARGET_ARCHITECTURE_AND_ROADMAP.md](TARGET_ARCHITECTURE_AND_ROADMAP.md); technical boundaries follow
> [LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](../LIMITATIONS_AND_REAL_WORLD_FINDINGS.md).
