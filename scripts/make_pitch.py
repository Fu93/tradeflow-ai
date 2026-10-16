"""Build pitch.pptx (10 slides) from our strategy. Uses python-pptx."""
from __future__ import annotations
import os
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "results", "pitch.pptx")
COVER = os.path.join(ROOT, "results", "cover_1280x720.png")

NAVY = RGBColor(0x0B, 0x10, 0x20)
BLUE = RGBColor(0x2C, 0x6F, 0xB5)
RED = RGBColor(0xED, 0x1C, 0x24)
GREEN = RGBColor(0x55, 0xA8, 0x68)
CREAM = RGBColor(0xEE, 0xF2, 0xF7)
GREY = RGBColor(0x6B, 0x78, 0x90)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

SLIDES = [
    ("TradeFlow AI",
     ["Constrained Decision Prototype: Vectorized Expectimax & AST Guardrails.",
      "Decision core computes; the LLM only renders language.",
      "AMD Developer Hackathon: ACT III",
      "Reinvent Commerce + Evolus Business Agents on AMD"]),
    ("One-liner: an AI-native trading company of one",
     ["TradeFlow computes concession strategies with Vectorized Expectimax + AST code guardrails.",
      "'Math governs strategy, LLM expresses jargon.'",
      "The LLM never decides a price; it renders an already-computed, AST-validated decision.",
      "One operator can run supplier-facing procurement that traditionally needs a sourcing team."]),
    ("Why not a chatbot — measured, not asserted",
     ["We ran our own real-model benchmark on the AMD endpoint: 100 matches, 348 real inference calls, $0.108.",
      "Real model budget violations: 0/94 (exact 95% upper bound 3.1%) — the simulator's violation figures (12–44% historically; 20–40% in the current 100-match artifact) were NOT reproduced.",
      "The real model's actual weakness is BREAKDOWN, not overspending: 61.1% deal-closure vs a Bluffing Hardliner (our solver in the synthetic arena closes 20/20 — but those archetypes carry reserves far below the ceiling, so that gap is scenario structure, NOT solver superiority).",
      "Academic context: NegotiationArena (ICLR 2024), AgenticPay (arXiv:2602.06008), Cattle Trade (arXiv:2605.14537)."]),
    ("Architecture: why decouple",
     ["Numerical decision core (NumPy tensor solver, no LLM in the decision path).",
      "Dual-tier defense: Tier-1 AST invariant guardrail on the strategy object + Tier-2 deterministic templates for language.",
      "Pareto Logrolling: swap non-price terms (deposit for lead time, volume for price) when unit price deadlocks.",
      "Deterministic degradation: Stage-1 regex + on-device ONNX runs offline; Stage-2 LLM is optional.",
      "Latency, honestly: Stage-1 mean ~0.13 ms (p99 ~0.49 ms); full HTTP round ~3.3 s (solver-bound), not '<3 ms end-to-end'."]),
    ("Demo: three verifiable moments",
     ["1. Belief convergence: Bayesian posterior reserve tracking across rounds (results/belief_update.png).",
      "2. AST guardrail rejects a >60% deposit demand — the object is blocked before it can be rendered.",
      "3. Multi-supplier reverse auction: 4 dynamic adversary agents quote round-by-round; BATNA ceilings tighten.",
      "All reproducible: verify_all.py (6 pillars), verify_live.py (7 E2E checks), 327 tests + 68 subtests green."]),
    ("User story: a single operator",
     ["An Amazon outdoor seller buys ~50,000 umbrellas/yr from China.",
      "Status quo: Google Translate on 1688 invites a foreigner markup and hidden mould fees; agents charge 7% and take kickbacks.",
      "With TradeFlow: the engine proposes an opening price and concession curve; the operator approves via a mobile card.",
      "Savings claim: 7% commission + foreigner markup + mould fees — each component documented separately, not one fabricated figure."]),
    ("AMD integration — honest version",
     ["Model layer really runs on AMD: Stage-2 classification and the Evolus Agent both call AMD tokenfactory (Qwen3.8-Flash-Next), verified HTTP 200.",
      "Engine is GPU-shaped: 20,000 opponent samples × 480 strategies = 9.6M rollouts/round, memory-bandwidth bound.",
       "Multi-supplier demo path: 4 suppliers × 20,000 samples × 20 opens × 6 scheds = 9.6M grid cells/round × 3 rounds = 28.8M parameterised cells (NOT a measured iteration count).",
      "Measured here (CPU): ~6.3M rollouts/sec peak at n=50,000 (results/bench.json, regenerated after the decision-core fix).",
      "MI300X: no measurement exists — the analytical roofline LOWER BOUND (working set ÷ 5300 GB/s) is a physical bound, not a projection.",
      "One-line portability via the _xp() tensor abstraction (NumPy ↔ CuPy/ROCm)."]),
    ("Evolus integration — all real calls",
     ["Building blocks actually exercised (real usage stats): email 2.46, doc-structure 2.12, sentiment 3.75 credits.",
      "Agent 'Supplier Negotiation Analyst' live-verified via POST /v1/chat/completions (HTTP 200), model bound own/qwen3.8-flash-next.",
      "App-driven REST: orchestrator.on_webhook() → engine → real Evolus AI Tool draft (no longer an offline stub).",
      "Knowledge library: real UN Comtrade customs data + live FX feed the agent's cost analysis.",
      "Remaining gap: the Evolus-side Automation (workflow) has not yet been created in the console."]),
    ("Honest limitations",
     ["Expectimax, not CFR: a heuristic solver under coarse priors; optimality fails if real factories fall outside the 4 archetypes.",
      "The simulator's violation claim (12–44% historically; 20–40% in the current artefact) was disproven by our own experiment (0/94, upper bound 3.1%).",
      "Prior miss is asymmetric: overestimating a supplier's reserve costs real margin (−0.102); underestimating is nearly free.",
      "Supplier quotes in demos are synthetic (adversary models), not real factory quotes. See LIMITATIONS §零 data classification.",
      "Calibration constants are chosen, not fitted; no real negotiation logs exist yet."]),
    ("Next steps",
     ["Collect 10,000+ real negotiation logs to fit the priors properly (currently chosen, not calibrated).",
      "If AMD Developer Cloud credits are granted: obtain a real MI300X measurement to replace the roofline bound.",
      "Rotate the two API keys exposed in plaintext during development.",
      "Extend the real-model benchmark beyond 100 matches to tighten the confidence bound.",
      "Double-track submission: Reinvent Commerce (main) + Evolus Business Agents on AMD."]),
]


def add_title_only(prs, title, bullets, color=BLUE):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # top bar
    bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.1))
    bar.fill.solid(); bar.fill.fore_color.rgb = NAVY
    bar.line.fill.background()
    # accent dot
    dot = slide.shapes.add_shape(9, Inches(0.4), Inches(0.35), Inches(0.25), Inches(0.25))
    dot.fill.solid(); dot.fill.fore_color.rgb = color; dot.line.fill.background()
    # title
    tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.22), prs.slide_width - Inches(1.2), Inches(0.8))
    tf0 = tb.text_frame; tf0.margin_top = Pt(0); tf0.margin_bottom = Pt(0)
    p = tf0.paragraphs[0]
    r = p.add_run(); r.text = title
    r.font.size = Pt(30); r.font.bold = True; r.font.color.rgb = WHITE
    # bullets — uniform style, top-aligned
    bt = slide.shapes.add_textbox(Inches(0.6), Inches(1.45), prs.slide_width - Inches(1.2),
                                  prs.slide_height - Inches(1.7))
    tf = bt.text_frame; tf.word_wrap = True
    tf.margin_top = Pt(0); tf.margin_left = Pt(0); tf.margin_right = Pt(0)
    tf.auto_size = None
    for i, b in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run(); run.text = "•  " + b
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0x14, 0x1E, 0x36)
        para.space_after = Pt(8)
        para.space_before = Pt(0)
    return slide


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    # All slides are title+bullets (cover image is submitted separately as 1280x720 png).
    for title, bullets in SLIDES:
        add_title_only(prs, title, bullets)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    prs.save(OUT)
    print("saved:", OUT, "slides:", len(prs.slides._sldIdLst))


if __name__ == "__main__":
    main()
