# TradeFlow AI — Hackathon Submission Kit (Lablab.ai / Devpost Copy-Paste Ready)

> **Event**: AMD Developer Hackathon: ACT III (Online Build: 2026/10/12 – **10/17**)  
> **Submission Tracks**: 
> 1. **Reinvent Commerce** (Main Challenge)
> 2. **Evolus Business Agents on AMD** (Partner Challenge)  
> **Status**: Core integration live-verified; presentation assets pending

---

## 📋 Quick Submission Form Fields

| Field Name | Submission Content |
|---|---|
| **Project Name** | **TradeFlow AI** |
| **Tagline (140 chars)** | AI-native trading company of one: a quantitative negotiation engine running cross-border sourcing end-to-end, powered by AMD ROCm + Evolus. |
| **Track 1** | Reinvent Commerce |
| **Track 2** | Evolus Business Agents on AMD |
| **Public GitHub URL** | https://github.com/Fu93/tradeflow-ai |
| **Demo Video URL** | *TODO: paste the Loom / YouTube link to the 3–5 minute narrated demo once it is uploaded. The `results/demo_video.mp4` currently in the repo is a ~15-second silent clip and does not meet the submission requirement — see `DEMO_SCRIPT.md` and `DEMO_VIDEO_DIRECTOR_SCRIPT.md` for the shot list.* |
| **Cover Image** | `results/cover_1280x720.png` (Included in repo, 1280x720 16:9 compliant) |
| **Tags / Technologies** | `AMD`, `ROCm`, `Evolus`, `vLLM`, `Game-Theory`, `Expectimax`, `AST-Guardrails`, `Bayesian`, `B2B-Commerce`, `Cross-Border-Trade`, `1688`, `AI-Native-Company` |

---

## 📝 Project Overview & Description (Markdown for Submission Body)

### 1. Elevator Pitch & Problem Statement
Cross-border B2B procurement is not a friendly chat—it is an asymmetric, multi-attribute game of strategic deception.

**Our thesis: the company itself is the product.** TradeFlow is built and operated as an **AI-native trading company of one** — a single operator running cross-border sourcing end-to-end, where the supplier-facing work is performed by an agent stack rather than a hired team. We are not selling a tool to procurement departments; we are demonstrating that one person plus an agent stack can run a process that traditionally requires a sourcing team or a 7%-commission intermediary.

The process we automate is one a real company runs every day. Today, cross-border sellers sourcing on 1688 or Alibaba face two painful traps:
1. **The Language & Sourcing Penalty**: Foreign buyers are routinely quoted a 20%+ "foreigner markup", while missing coastal Chinese factory jargon like hidden mold tooling charges (the "mould fee" a factory keeps off the quote) and bait-and-switch raw material downgrades.
2. **The Corrupt Middleman Dilemma**: Sourcing agents charge 7%+ commissions while pocketing secret factory kickbacks and exposing buyers to 80% unrecoverable deposit traps.

**Why Not Just Use a Standard LLM Chatbot?**  
Academic benchmarks (NegotiationArena, ICLR 2024; AgenticPay, arXiv:2602.06008; Cattle Trade, arXiv:2605.14537) report that unconstrained LLMs suffer severe strategic myopia under hard and bluffing opponents. We additionally ran our **own** real-model measurement on the AMD endpoint (**100 matches, 348 real inference calls, $0.108**) and found the dominant failure mode to be **breakdown and over-concession, not budget violation**: against a Bluffing Hardliner the real model closed only **61.1%** of deals. Notably, the **20.0%–40.0%** violation figures from our *parametric simulator* (the committed artifact's values) were **not reproduced** against the real AMD-hosted model (**0/94 violations, one-sided 95% CI upper bound ≈ 3.1%**); we report that negative result in `LIMITATIONS` §2.3 instead of quoting the simulator.

---

### 2. Our Solution: "Math Governs Strategy, LLM Expresses Jargon"
TradeFlow AI decouples numerical strategic optimization from linguistic surface generation:
* **Strategy Layer (Numerical Decision Core, no LLM in the decision path)**: Evaluates 20,000 Bayesian opponent belief samples across 480 candidate price-concession curves using a Vectorized Expectimax solver. Measured on the development machine (Intel Core Ultra 7 355, x86-64 CPU; `results/bench.json`): the full 20K-sample × 480-curve grid (9.6M rollouts) solves in **~1.7s**; a 2K-sample grid in ~0.30s. Sole reliance on math — the LLM is never allowed to decide a price; it only renders an already-computed and AST-validated decision.
* **AST Invariant Guardrail (Tier 1 Defense)**: Syntactically audits every `TacticalActionObject` via Python AST compiler before execution, physically guaranteeing that `authorized_price <= budget_ceiling` and `deposit <= 60%` (AST red line; the Logrolling business rule is stricter, at `deposit <= 50%`).
* **Deterministic Coastal Jargon Renderer (Tier 2 Defense)**: Automatically crafts authentic Zhejiang/Guangdong industrial cluster merchant jargon to eliminate identity premiums, while presenting executive English CPO decision cards for western buyers.
* **Pareto Logrolling (Multi-Attribute Swaps)**: When unit price hits an impasse, automatically unlocks non-price concessions (e.g., swapping higher initial deposit for zero mold fees or shortened delivery lead times).
* **Compliance Air-Gap Gate (HITL)**: The API circuit breaker freezes execution when breakdown risk exceeds 15% or a compliance rule trips, and routes a 1/2/3 authorization prompt to the buyer's smartphone via WhatsApp/Telegram. Captcha detection lives in the browser extension, not in that API breaker.

---

### 3. AMD Hardware & Technology Integration

1. **Analytical Roofline Memory-Bandwidth Analysis**:
   * Simulating batched rollout paths across 4 concurrent suppliers generates a small 3D tensor working set. The demo path evaluates **28.8M rollout positions** (9.6M per round × 3 rounds); this is the **arithmetic product** $K \times S \times N_{open} \times N_{sched}$ of the parameterised grid, **not a measured execution count** — each sample chunk is a vectorised broadcast, and 28.8M is still the number of parameter combinations, not loop iterations or one allocation. The measured quantity is latency. The maximum strategy grid's theoretical upper bound is 96M. On CPU memory the sweep takes a few seconds. The MI300X figure is a theoretical roofline lower-bound estimate, not measured (`results/mi300x_roofline.json`). Formula: `lower_bound_seconds = (working_set_mb * 1024 * 1024) / (5.3e12)`. The denominator is the datasheet quote "5.3 TB/s max. peak theoretical".
   * **Honest scope note**: That bound is not a measured MI300X run and not a runtime prediction. **Our development machine is an Intel Core Ultra 7 355 (x86-64)** — this project has never executed on AMD CPU or AMD GPU hardware. The only genuinely AMD component is the model layer, which calls the AMD tokenfactory endpoint. We state this explicitly rather than presenting a bound as a benchmark.
2. **Unified ROCm Software Ecosystem (`_xp()` Portability)**:
   * TradeFlow uses a hardware-agnostic tensor abstraction layer (`src/engine/solver.py`). The same solver code runs via SIMD NumPy on any x86-64 CPU and *would* scale to AMD Instinct MI300X GPUs via CuPy/ROCm without code refactoring — but that scaling is **untested**, because no ROCm device was available to us.
3. **AMD tokenfactory Model Serving (Live-Verified)**:
   * TradeFlow's semantic layer is live-verified against the hackathon's AMD-hosted inference endpoint (`https://developer.amd.com.cn/radeon/api`), serving an open model for Stage-2 supplier-intent reasoning. Calls, latency, and token costs are all measured — see `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §10.

---

### 4. Evolus Business Agents Integration

TradeFlow drives Evolus through its REST API. **Live-verified on 2026-10-14** (real API calls, real usage stats):

* **AI Tools — `email`** (`POST /api/v1/ai-tools/email/run`): ✓ verified — drafts formal commercial replies. `creditsConsumed: 2.46`
* **AI Tools — `doc-structure`** (`POST /api/v1/ai-tools/doc-structure/run`): ✓ verified — parses supplier quotation text into structured JSON, correctly extracting unit price, MOQ, lead time, deposit and mold fee. `creditsConsumed: 2.12`
* **AI Tools — `sentiment`** (`POST /api/v1/ai-tools/sentiment/run`): ✓ verified — `creditsConsumed: 3.75`
* **App-driven REST integration**: ✓ verified — `src/app/orchestrator.py` receives an Evolus callback, runs the decision engine, then calls Evolus AI Tools to draft the reply. The draft correctly cites the engine-computed target price (¥13.12) against the supplier's actual quote (¥15, MOQ 500).
* **Workflow**: *Planned.* Our workspace currently has no workflow created (`GET /api/v1/workflows` → `[]`). The **agent** (`Supplier Negotiation Analyst`, AMD-hosted model bound) and the **Personal Library** knowledge base (market anchor / spec baseline / compliance policy) are **created and live-verified on 2026-10-14** (see `LIMITATIONS` §12). We do not claim the workflow as complete; it is a follow-up hardening item, not a qualification gap.

---

### 5. Empirical Benchmark & Tournament Results

In our synthetic self-play benchmark against 5 distinct game-theoretic behavioral archetypes (Hard-Bargainer, Conceder, Tit-for-Tat, Bluffing Hardliner, and Noisy Seller) — **the committed artifact is 20 matches per archetype (100 total, seed 42)**, not 750:
* **Budget Violation Rate (synthetic simulator, C-level)**: TradeFlow achieved **0.0%** versus **20.0%–40.0%** for the parametric buyer proxy. After the decision-core fix the *fixed-5% baseline* is deliberately allowed to breach the ceiling, so the engine's 0.0% is now a measured (not definitional) result. See `results/self_play_benchmark.md`.
* **Real-model replication (A-level, our own experiment)**: When the *real* AMD-hosted model (Qwen3.8-Flash-Next) played the buyer across **100 matches / 348 real inference calls** ($0.108), it recorded **0/94 budget violations** — with an exact one-sided 95% upper bound of **3.1%**, which is sufficient to *reject* the simulator's violation figures at that confidence level. Its actual weakness was **breakdown**: 61.1% deal-closure against the Bluffing Hardliner and 63.2% against Tit-for-Tat, materially below our solver's deal rate in the synthetic arena. 6 matches (1.7% of calls) aborted on unparseable output — reported, not hidden. **Caveat so this is not over-read**: the synthetic arena's five adversaries carry reserves of 1.05–1.15 against a walk-away ceiling of 1.35, so deal-closure there is structurally easy and the solver's near-100% deal rate is a property of the scenario, **not** evidence of general superiority over a real LLM. See `LIMITATIONS` §2.3.
* **Procurement savings (synthetic self-play)**: on `results/self_play_benchmark.md` the engine's savings versus the walk-away ceiling in that table run from **8.8% to 20.0%** (20 matches per archetype, 100 total, seed 42). That is not savings versus the supplier's first quote, and it is not the auction headline. Honest caveat: on the Hard-Bargainer archetype the engine trails the fixed-5% baseline (8.8% vs 9.2%); we report the grid as measured rather than tuning the engine to win every cell.
* **Latency (measured, two tiers)**:
  * **Stage-1 deterministic path** (regex + on-device ONNX, LLM not invoked): **~1 ms** per round, 0 tokens, $0.00.
  * **Stage-2 LLM semantic reasoning** (AMD tokenfactory, when Stage-1 abstains): **2 – 13 seconds**; the model is a reasoning model on a shared inference service.
  * We do **not** claim "<3 ms end-to-end" for requests that reach Stage-2.
* **Benchmark scope note**: The self-play figures come from a parametric behavioral simulator calibrated against published literature (NegotiationArena / Bianchi et al., ICLR 2024; AgenticPay, arXiv:2602.06008; Cattle Trade, arXiv:2605.14537), **not** from live cloud LLM API calls; the committed artifact holds **100 matches (20 per archetype, seed 42)** — the "750" label came from a hardcoded header string in the runner (since fixed) and did not describe the artifact. The real-model replication above *is* a live measurement. See `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §2 for both, and §2.3 for the negative result.

---

### 6. Transparent Limitations & Real-World Findings
In accordance with engineering rigor, we openly document our prototype boundary:
* **Expectimax Prior Dependency**: Current models assume 4 parameterized supplier archetypes. Real-world validation against live factory sales reps is required for full-scale production.
* **Supplier Collusion Boundary**: Dynamic BATNA assumes independent pricing. In tight industrial clusters where informal cartels exist, cross-region supplier sourcing is necessary.
* **Deterministic Degradation, not "Zero-LLM"**: The core negotiation loop has **no hard dependency** on external LLM APIs. Stage-2 is an *optional enhancement layer*: if no API key is configured or the call fails, the system silently degrades to Stage-1 (regex + on-device ONNX) and continues to function. We describe this as deterministic degradation rather than claiming the system never calls an LLM.
* **Evaluation Validity**: Our intent benchmark is an in-distribution regression set (its sentences overlap the Stage-1 regex vocabulary), and our 500-item set is programmatically generated (66 base templates recycled; 132 distinct sentences after masking digits). Neither is a hand-written held-out set, and neither is used to claim generalization.
  * **Train/eval separation (corrected)**: The 500-item set is split at the **base-template level** into `data/ood_train.json` (305) and `data/ood_test.json` (195) with zero template-level or exact-string overlap. The on-device ONNX head is trained **only** on the train split, and generalisation is reported **only** on the held-out split: measured **63.08% (123/195)** end-to-end. The **92.00%** figure cited in earlier revisions was a **train-set score** — the previous export script trained the ONNX head on the entire OOD-500 and then evaluated that same file (same model: 92.79% on the train split vs 63.08% held-out). Genuine classifier-vs-regex output overlap is **31/500** items (the earlier "199/500" figure counted overlap with the whole module's source text, including docstrings and f-strings).

---

## 🛠️ Verification & Run Instructions

```bash
# Clone and enter repo
git clone https://github.com/Fu93/tradeflow-ai.git
cd tradeflow-ai

# Install dependencies (pure Python, zero node/npm)
pip install -r requirements.txt

# Run complete 6-pillar verification test suite
python verify_all.py

# Run unit and integration tests (376 tests + 98 subtests, all green)
python -m pytest tests -q

# Launch the Web Portal Command Center
python -m src.app.main
# Open http://localhost:8000 in your browser
```
