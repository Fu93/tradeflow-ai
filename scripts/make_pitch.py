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
      "Dual-Tier Defense on AMD ROCm Architecture.",
      "AMD Developer Hackathon: ACT III",
      "Reinvent Commerce + Evolus Business Agents on AMD"]),
    ("One-liner",
     ["TradeFlow computes optimal concession strategies using Vectorized Expectimax + AST code guardrails.",
      "'Math governs policy, LLM renders coastal jargon.'",
      "Decision core outputs pure numerical action objects; AST physically intercepts policy breaches."]),
    ("Why not a chatbot",
     ["Empirical 750-match benchmark against 5 adversary archetypes.",
      "Budget Violation Rate: TradeFlow 0.0% (AST hard interception) vs Vanilla LLM 12.0% ~ 44.0%.",
      "Savings Rate vs Bluffing Hardliner: TradeFlow 18.5% vs Vanilla LLM 3.7%."]),
    ("Architecture",
     ["Dual-Agent Decoupling: Decision Core (NumPy Tensor Solver) + Surface Renderers.",
      "Dual-Tier Defense: Tier-1 AST Python AST Guardrail + Tier-2 Coastal Jargon FSM.",
      "Corporate Mandate Pareto Logrolling: Liquidity deposit for expedited lead-time & tooling waiver.",
      "Zero-LLM Core Fallback: Local CPU <3ms deterministic execution guaranteed."]),
    ("Demo: 3 Wows",
     ["1. Belief Convergence: Dynamic Bayesian floor estimation (1.08 -> 1.25).",
      "2. AST Guardrail Interception: Physically blocks deposit >60% & solver prices >ceiling.",
      "3. Multi-Supplier Logrolling: Resolves deadlocks via Pareto-optimal multi-term swap."]),
    ("User Story: Alex Rivera",
     ["Alex Rivera (Amazon top outdoor seller, $3M GMV): buys 50,000 umbrellas/yr from China.",
      "Painful Status Quo: Google Translate on 1688 triggers 20% foreigner markup & hidden mold fees;",
      "Sourcing agent charges 7% fee, takes kickbacks, and demands 80% deposit with zero accountability.",
      "With TradeFlow: CPO executive WhatsApp card, AST limits deposit to 50%, saves $2,500 on first batch."]),
    ("AMD Value: Unified ROCm",
     ["CPU-First Design: Sub-2ms per round, negotiation bottlenecks are human & network, not GPU math.",
      "AMD Real Moat: Unified ROCm stack enables zero-refactor code portability via _xp() tensor bridge.",
      "Enterprise Scale: Co-locates Qwen2.5-14B/72B and parallel reverse-auction tensor solver in HBM3."]),
    ("Evolus Integration",
     ["4 Building Blocks: Workflow + Document Extractor + AI Tools + Knowledge Library.",
      "End-to-End: PDF RFQ extraction -> AST-guarded email drafting -> business agent closure.",
      "Rate-limit safe: 60 req/min untouched; zero LLM token cost during core negotiation."]),
    ("Honest Limitations",
     ["Expectimax vs CFR: Clear academic boundary (heuristic decision prototype, not yet full CFR).",
      "Belief metric: Represents posterior variance convergence, not macro credit default rate.",
      "Cost Model: Zero token runtime, but requires 10~20 engineer hours per new vertical category."]),
    ("Next steps",
     ["Collect 10,000+ real trading logs to train offline CFR reinforcement learning.",
      "AMD Cloud instance live benchmark on native ROCm/vLLM stack.",
      "Double-Track Submission: Reinvent Commerce (Main) + Evolus Business Agents."]),
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
