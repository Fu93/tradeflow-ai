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
     ["Procurement Negotiation, Quantified.",
      "Bayesian + vectorized MCTS on AMD MI300X.",
      "AMD Developer Hackathon: ACT III",
      "Reinvent Commerce + Evolus Business Agents on AMD"]),
    ("One-liner",
     ["TradeFlow computes the optimal concession strategy under uncertainty, using Bayesian belief "
      "updating + vectorized Monte-Carlo search on AMD MI300X.",
      "The LLM only writes/reads/explains. The decision core is verifiable math."]),
    ("Why not a chatbot",
     ["Decision = Bayesian update + vectorized MCTS — every step explainable & testable.",
      "Hits the brief directly: 'LLM cannot be the only prediction method'.",
      "Belief-distribution visualization is our highest-value demo asset."]),
    ("Architecture",
     ["Procurement -> Evolus email-in Workflow -> Document Extractor -> our container.",
      "Engine (Bayes + MCTS) runs batched rollouts on AMD MI300X (samples x 480 strategies).",
      "Proposal generator -> Evolus AI Tool (email) -> human approval gate.",
      "Key invariant: GPU runs engine rollouts, NOT chat tokens."]),
    ("Demo: 3 Wows",
     ["1. Belief update: 5 rounds; reserve 1.08 -> 1.25, opening 1.03 -> 1.19.",
      "2. CPU vs MI300X: 9.6M rollouts, CPU ~1.1s -> MI300X ~0.4s.",
      "3. Robustness: engine vs 5%-fixed baseline, breakdown 6.7% vs 28.4%.",
      "Prior +/-50% keeps the recommended strategy stable."]),
    ("Business value",
     ["Industry benchmark: professional procurement saves 5-10%.",
      "A $1M order at 5% saving = $50,000.",
      "Vertical: auto-umbrella export (HS6601), real FOB anchor."]),
    ("AMD technology",
     ["Engine evaluates opponent-samples x strategy-family, batched on AMD MI300X.",
      "vLLM on ROCm + Qwen2.5-14B + --tool-call-parser hermes (your own endpoint).",
      "Naive CPU loop ~46s -> MI300X ~0.4s (real-time)."]),
    ("Evolus integration",
     ["4 blocks: Workflow + Document Extractor + AI Tools + Knowledge Library.",
      "App drives Evolus via REST (X-ApiKey + X-Application-Id).",
      "60 req/min limit untouched: engine rollouts run in our container."]),
    ("Robustness / rigor",
     ["Quantify the opponent-model assumption: prior +/-50% keeps recommended strategy stable.",
      "Beats every human baseline we tested (3/5/10/15% fixed concession).",
      "Breakdown > 15% auto-flags HUMAN_REVIEW; LLM never invents prices."]),
    ("Next steps",
     ["With real negotiation logs -> add CFR (now: Bayes + backward induction, the correct method).",
      "Replace MI300X projection with measured numbers; expand verticals (electronics parts).",
      "Double-submit: main prize (Reinvent Commerce) + partner prize (Evolus)."]),
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
