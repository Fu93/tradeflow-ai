"""Build the 8-slide deck in results/pitch.pptx from the SUBMISSION.md outline.

Every measured figure is read from a source file at generation time
(results/*.json, results/*.md, models/onnx_export_metrics.json). The slide
text does not invent a latency, a speedup, or a second hackathon track.

Regenerate after those sources change:

    python -m pip install python-pptx
    python scripts/make_pitch.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pitch.pptx"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_claims import _claims, _read  # noqa: E402


def auction_baseline_lines(text: str) -> list[str]:
    """The three labeled savings lines in results/multi_supplier_auction.md."""
    wanted_prefix = (
        "Headline savings",
        "Versus target",
        "Secondary,",
    )
    found: list[str] = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        if body.startswith(wanted_prefix):
            found.append(body)
    if len(found) != 3:
        raise SystemExit(
            "results/multi_supplier_auction.md no longer has the three baseline lines"
        )
    return found


def build_slides() -> list[tuple[str, list[str]]]:
    """Return (title, bullets) for the eight SUBMISSION.md slides."""
    claims = _claims()
    play = claims["play"]
    llm = json.loads((ROOT / "results" / "real_llm_benchmark_100.json").read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    auction = auction_baseline_lines(_read(ROOT / "results" / "multi_supplier_auction.md"))
    logged_on = str(llm["meta"]["generated_at_utc"])[:10]
    endpoint = str(llm["meta"]["endpoint"])
    gpu_flag = "true" if bench.get("is_gpu_active") else "false"

    return [
        (
            "1. Title",
            [
                "TradeFlow AI. Track: Reinvent Commerce.",
                "One operator. The engine sets the price. A human sends the offer.",
                "Prototype. No factory has closed a deal through this.",
                "Public repo: https://github.com/Fu93/tradeflow-ai. License: MIT.",
            ],
        ),
        (
            "2. Problem",
            [
                "Cross-border buyers either negotiate alone, often with a language penalty, or pay an agent.",
                "TradeFlow is a prototype of a third arrangement: one operator, software doing the arithmetic, the person keeping the send button.",
                "It is not a deployed company and it has not bought from a real factory.",
            ],
        ),
        (
            "3. Split",
            [
                "Solver decides. The decision core does not ask a language model what to pay.",
                "Model phrases. An optional call can classify a message or draft wording.",
                "Human sends. The Chrome extension (Manifest V3) shows a card. It cannot press Send.",
                "Code: src/engine/solver.py, src/engine/multi_solver.py, extension/.",
            ],
        ),
        (
            "4. Decision core",
            [
                "A belief about the supplier reserve, then a vectorised Expectimax search over openings and concession curves.",
                "Read the recommended price and the breakdown probability off the dashboard you just captured.",
                "This slide does not state a latency. Use the number on the screen.",
            ],
        ),
        (
            "5. AMD language layer",
            [
                f"Sample endpoint: {endpoint}.",
                (
                    f"Committed log results/real_llm_benchmark_100.json ({logged_on}): "
                    f"model {claims['model']}, {claims['calls']} calls, "
                    f"average latency {claims['latency']} s, cost ${claims['cost']}."
                ),
                "That file does not store an HTTP status. status: ok from scripts/verify_amd_endpoint.py means that process received HTTP 200. skipped_no_credentials is not a successful check.",
            ],
        ),
        (
            "6. AMD compute layer",
            [
                "TRADEFLOW_SOLVER_BACKEND selects NumPy (default) or a CuPy/ROCm request.",
                f"results/bench.json is a CPU measurement (is_gpu_active: {gpu_flag}).",
                f"The MI300X column is a {claims['roofline_label']}.",
                f"Formula: {claims['roofline_formula']}.",
                "No AMD GPU measurement is in the repo unless a sibling benchmark JSON says ran_on_amd_gpu: true. Do not narrate a speedup.",
            ],
        ),
        (
            "7. Evidence, with the baseline named",
            [
                (
                    f"Self-play (results/self_play_benchmark.md): engine savings versus that table's "
                    f"walk-away ceiling run from {play['min_savings']} to {play['max_savings']}. "
                    f"Hard-Bargainer {play['hard_engine']} versus the fixed baseline {play['hard_fixed']}. "
                    f"{play['per_archetype']} matches, {play['n_archetypes']} archetypes, seed {play['seed']}. "
                    "Not the auction headline."
                ),
                f"Real-model log, different experiment: Bluffing Hardliner deal rate {claims['bluff_deal']}. {claims['violations']}.",
                f"Held-out intent accuracy on the synthetic set: {claims['held_out_acc']} ({claims['held_out_split']} held out).",
                "Auction headline is savings versus the first-round best quote. Versus target is shown, including when that figure is negative. The walk-away ceiling figure is secondary.",
                *auction,
            ],
        ),
        (
            "8. Limits and how to run",
            [
                "cp .env.example .env && docker compose up --build, then open http://localhost:8000.",
                "Placeholder keys are ignored. /health reports offline_demo and the solver backend.",
                "No factory deal. No shipped MI300X timing. MIT. Public repo.",
                "Working deadline used for this pack: 11:00 London (BST), 18 October 2026. Confirm the form.",
            ],
        ),
    ]


def render(slides: list[tuple[str, list[str]]], out: Path) -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    # 13.333in x 7.5in widescreen, in English Metric Units.
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    ink = RGBColor(0x1C, 0x17, 0x12)
    navy = RGBColor(0x1E, 0x3A, 0x4C)
    white = RGBColor(0xFB, 0xF7, 0xF0)
    for title, bullets in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.05))
        bar.fill.solid()
        bar.fill.fore_color.rgb = navy
        bar.line.fill.background()
        box = slide.shapes.add_textbox(Inches(0.45), Inches(0.22), prs.slide_width - Inches(0.9), Inches(0.7))
        para = box.text_frame.paragraphs[0]
        run = para.add_run()
        run.text = title
        run.font.size = Pt(28)
        run.font.bold = True
        run.font.color.rgb = white
        body = slide.shapes.add_textbox(
            Inches(0.5), Inches(1.35), prs.slide_width - Inches(1.0), prs.slide_height - Inches(1.6)
        )
        frame = body.text_frame
        frame.word_wrap = True
        for index, bullet in enumerate(bullets):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            bullet_run = paragraph.add_run()
            bullet_run.text = bullet
            bullet_run.font.size = Pt(16)
            bullet_run.font.color.rgb = ink
            paragraph.space_after = Pt(10)
            paragraph.level = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)


def main() -> None:
    slides = build_slides()
    if len(slides) != 8:
        raise SystemExit(f"expected 8 slides, got {len(slides)}")
    render(slides, OUT)
    print(f"saved: {OUT} slides: {len(slides)}")


if __name__ == "__main__":
    main()
