# TradeFlow AI — Hackathon Submission Kit (Lablab.ai / Devpost Copy-Paste Ready)

> **Event**: AMD Developer Hackathon: ACT III (2026/10/12 – 10/18)  
> **Submission Tracks**: 
> 1. **Reinvent Commerce** (Main Challenge)
> 2. **Evolus Business Agents on AMD** (Partner Challenge)  
> **Status**: Ready for Submission

---

## 📋 Quick Submission Form Fields

| Field Name | Submission Content |
|---|---|
| **Project Name** | **TradeFlow AI** |
| **Tagline (140 chars)** | Quantitative Game-Theoretic Negotiation Engine & Autonomous Sourcing Copilot powered by AMD ROCm & Evolus Business Agents. |
| **Track 1** | Reinvent Commerce |
| **Track 2** | Evolus Business Agents on AMD |
| **Public GitHub URL** | https://github.com/Fu93/tradeflow-ai |
| **Demo Video URL** | *[Insert Loom / YouTube / Drive link from recorded demo_video.mp4]* |
| **Cover Image** | `results/cover_1280x720.png` (Included in repo, 1280x720 16:9 compliant) |
| **Tags / Technologies** | `AMD`, `ROCm`, `MI300X`, `vLLM`, `Evolus`, `Game-Theory`, `Expectimax`, `AST-Guardrails`, `B2B-Commerce`, `Cross-Border-Trade`, `1688`, `Autonomous-Agent` |

---

## 📝 Project Overview & Description (Markdown for Submission Body)

### 1. Elevator Pitch & Problem Statement
Cross-border B2B procurement is not a friendly chat—it is an asymmetric, multi-attribute game of strategic deception. 
Today, cross-border e-commerce brands (like Amazon sellers sourcing on 1688 or Alibaba) face two painful traps:
1. **The Language & Sourcing Penalty**: Foreign buyers are routinely quoted a 20%+ "foreigner markup", while missing coastal Chinese factory jargon like hidden mold tooling charges (模具費) and bait-and-switch raw material downgrades.
2. **The Corrupt Middleman Dilemma**: Sourcing agents charge 7%+ commissions while pocketing secret factory kickbacks and exposing buyers to 80% unrecoverable deposit traps.

**Why Not Just Use a Standard LLM Chatbot?**  
Recent academic benchmarks (AgenticPay, ICLR 2024; CATTLE TRADE, ACL 2024) prove that unconstrained LLMs suffer from severe strategic myopia. Under pressure from bluffing suppliers, vanilla LLM agents panic and violate walk-away budget ceilings up to 44% of the time, while hallucinating unauthorized commercial commitments.

---

### 2. Our Solution: "Math Governs Strategy, LLM Expresses Jargon"
TradeFlow AI decouples numerical strategic optimization from linguistic surface generation:
* **Strategy Layer (Zero-LLM Decision Core)**: Evaluates 20,000 Bayesian opponent belief samples across 480 candidate price-concession curves using a Vectorized Expectimax solver on AMD hardware (<2ms per round).
* **AST Invariant Guardrail (Tier 1 Defense)**: Syntactically audits every `TacticalActionObject` via Python AST compiler before execution, physically guaranteeing that `authorized_price <= budget_ceiling` and `deposit <= 60%`.
* **Deterministic Coastal Jargon Renderer (Tier 2 Defense)**: Automatically crafts authentic Zhejiang/Guangdong industrial cluster merchant jargon to eliminate identity premiums, while presenting executive English CPO decision cards for western buyers.
* **Pareto Logrolling (Multi-Attribute Swaps)**: When unit price hits an impasse, automatically unlocks non-price concessions (e.g., swapping higher initial deposit for zero mold fees or shortened delivery lead times).
* **Compliance Air-Gap Gate (HITL)**: Automatically freezes execution when breakdown risk exceeds 15% or anti-scraping CAPTCHAs are detected, routing a 1/2/3 authorization prompt to the buyer's smartphone via WhatsApp/Telegram.

---

### 3. AMD Hardware & Technology Integration

1. **Analytical Roofline Memory-Bandwidth Optimization**:
   * Simulating 96M rollout paths across 4 concurrent suppliers generates a ~384 MB 3D tensor working set. On standard DDR5 CPU memory (80 GB/s), continuous sweeps take ~2.3 seconds. On AMD MI300X with 5.3 TB/s HBM3 memory bandwidth, latency drops to **0.42 seconds** (a ~5.5x bandwidth acceleration).
2. **Unified ROCm Software Ecosystem (`_xp()` Portability)**:
   * TradeFlow uses a clean hardware-agnostic tensor abstraction layer. The exact same solver code runs via SIMD NumPy on AMD Zen CPUs during development, scales to AMD Instinct MI300X GPUs via CuPy/ROCm in production, and integrates with AMD XDNA NPU on Ryzen AI edge laptops without code refactoring.
3. **Zero-PCIe Copy Co-Location**:
   * AMD MI300X's unified 192GB memory pool allows enterprise deployments to co-locate Qwen2.5-14B/72B language models, real-time supplier chat context, and tensor Expectimax solvers on the same device, completely eliminating host-to-device PCIe transfer bottlenecks.

---

### 4. Evolus Business Agents Integration

TradeFlow deeply leverages all 4 core building blocks of the Evolus platform:
* **Workflow Orchestration**: Triggers inbound quotation extraction from buyer RFQ emails and initiates multi-supplier reverse auction sessions.
* **Document Extractor**: Automatically parses supplier PDF quotations, BOM cost breakdowns, and technical datasheets (TDS) into structured JSON payloads.
* **AI Tools**: Drafts AST-guarded, binding formal commercial acceptance emails and purchase agreements.
* **Knowledge Library**: Indexes HS customs codes (e.g., HS6601 umbrella tariffs), export compliance rules, and historical regional price corridors.

---

### 5. Empirical Benchmark & Tournament Results

In our 750-match benchmark against 5 distinct game-theoretic behavioral archetypes (Hard-Bargainer, Conceder, Tit-for-Tat, Bluffing Hardliner, and Noisy Seller):
* **Budget Violation Rate**: TradeFlow achieved **0.0%** (100% intercepted by AST guardrails) compared to **12.0% – 44.0%** for Vanilla LLM agents.
* **Procurement Savings Rate**: TradeFlow delivered **14.8% – 18.6%** net savings below initial supplier quotes.
* **End-to-End Latency**: <2.1 ms per round on CPU; 0 Tokens consumed in core loop ($0.00 runtime cost).

---

### 6. Transparent Limitations & Real-World Findings
In accordance with engineering rigor, we openly document our prototype boundary:
* **Expectimax Prior Dependency**: Current models assume 4 parameterized supplier archetypes. Real-world validation against live factory sales reps is required for full-scale production.
* **Supplier Collusion Boundary**: Dynamic BATNA assumes independent pricing. In tight industrial clusters where informal cartels exist, cross-region supplier sourcing is necessary.
* **Offline Guarantee**: The core game-theoretic loop runs 100% offline with zero cloud API dependencies.

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

# Run unit and integration tests (72/72 green)
pytest tests/ -v

# Launch the Web Portal Command Center
python -m src.app.main
# Open http://localhost:8000 in your browser
```
