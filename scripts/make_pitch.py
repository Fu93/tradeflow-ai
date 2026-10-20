"""Build the deck in results/pitch.pptx and results/pitch.pdf.

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
PDF = ROOT / "results" / "pitch.pdf"
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


def _comparison() -> dict:
    path = ROOT / "results" / "llm_vs_tradeflow.json"
    if not path.is_file():
        raise SystemExit("results/llm_vs_tradeflow.json is missing; run scripts/compare_llm_vs_tradeflow.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("ran_on_amd") is not False:
        raise SystemExit("llm_vs_tradeflow.json must say ran_on_amd false unless a real call was made")
    return payload


def build_slides() -> list[tuple[str, list[str]]]:
    """Return (title, bullets) for the SUBMISSION.md slides."""
    claims = _claims()
    play = claims["play"]
    llm = json.loads((ROOT / "results" / "real_llm_benchmark_100.json").read_text(encoding="utf-8"))
    bench = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    comp = _comparison()
    auction = auction_baseline_lines(_read(ROOT / "results" / "multi_supplier_auction.md"))
    logged_on = str(llm["meta"]["generated_at_utc"])[:10]
    endpoint = str(llm["meta"]["endpoint"])
    gpu_flag = "true" if bench.get("is_gpu_active") else "false"
    engine = comp["tradeflow"]
    stand_in = comp["offline_llm"]

    return [
        (
            "1. Title",
            [
                "TradeFlow AI. Track: Reinvent Commerce.",
                "Small cross-border sellers lose margin negotiating factory quotes alone, or pay an agent to do it.",
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
            "3. Market, revenue, competition",
            [
                "Target user is a hypothesis: a small cross-border seller who sources from factories and negotiates in chat.",
                "HMRC 2025: 334,294 UK goods importers. SAM proxy 132,484 to 239,616 (formula in docs/MARKET_ANALYSIS.md).",
                "UK goods from China, 2025: GBP 69.7 billion (ONS/DBT). Includes precious metals and large corporates.",
                "Savings-pool ceiling GBP 1.05 billion to GBP 2.09 billion. Large-buyer 1.5 to 3 percent band. Not a TradeFlow result.",
                "Umbrellas (HS 6601) are the demo wedge. Other codes use the Comtrade preview script. Customs unit value is reference only and does not set the price.",
                "Revenue model, proposed and not earned: a desk subscription, or a share of savings versus the first-round best quote. No revenue is claimed.",
                "Public prices: Accio, and Alibaba RFQ free for buyers. Pactum, Keelvar, Fairmarkit, Jaggaer, Zycus, Globality are quote-based.",
            ],
        ),
        (
            "4. Split",
            [
                "Hybrid desk. The criteria this workflow is written for are Hybrid, Becreatives, and Vibe Generation.",
                "Solver decides. The decision core does not ask a language model what to pay.",
                "Model phrases. An optional call can classify a message or draft wording.",
                "Human sends. The Chrome extension (Manifest V3) shows a card. It cannot press Send.",
                "Code: src/engine/solver.py, src/engine/multi_solver.py, extension/.",
            ],
        ),
        (
            "5. Decision core",
            [
                "The next offer maximises expected landed saving minus incremental walk risk. It is a per-round heuristic, not a multi-round equilibrium.",
                "Read the recommended price and the breakdown probability off the dashboard you just captured.",
                "This slide does not state a latency. Use the number on the screen.",
            ],
        ),
        (
            "6. AMD language layer",
            [
                f"Sample endpoint: {endpoint}.",
                (
                    f"Committed log results/real_llm_benchmark_100.json ({logged_on}): "
                    f"model {claims['model']}, {claims['calls']} calls, "
                    f"average latency {claims['latency']} s, cost ${claims['cost']}."
                ),
                "That file does not store an HTTP status. status: ok from scripts/verify_amd_endpoint.py means that process received HTTP 200. skipped_no_credentials is not a successful check.",
                "The default model for a new Stage-2 call is Gemma-4-31B-IT. The committed log above is Qwen3.8-Flash-Next, not Gemma. No committed Gemma latency.",
            ],
        ),
        (
            "7. AMD compute layer",
            [
                "TRADEFLOW_SOLVER_BACKEND selects NumPy (default) or a CuPy/ROCm request.",
                f"results/bench.json is a CPU measurement (is_gpu_active: {gpu_flag}).",
                f"The MI300X column is a {claims['roofline_label']}.",
                f"Formula: {claims['roofline_formula']}.",
                "No AMD GPU measurement is in the repo unless a sibling benchmark JSON says ran_on_amd_gpu: true. Do not narrate a speedup.",
            ],
        ),
        (
            "8. Evidence, with the baseline named",
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
                (
                    f"Offline comparison results/llm_vs_tradeflow.json: {comp['episode_count']} episodes, "
                    f"seed {comp['seed']}, buyer {comp['llm_buyer']}. Not an AMD call. "
                    f"TradeFlow budget violations {engine['budget_violation_rate_label']}, "
                    f"deal rate {engine['deal_rate_label']}, "
                    f"average savings versus the supplier opening {engine['average_savings_label']}. "
                    f"Stand-in budget violations {stand_in['budget_violation_rate_label']}, "
                    f"deal rate {stand_in['deal_rate_label']}, "
                    f"average savings {stand_in['average_savings_label']}. "
                    "Simulation. Not the auction headline."
                ),
            ],
        ),
        (
            "9. Limits and roadmap",
            [
                "cp .env.example .env && docker compose up --build, then open http://localhost:8000. render.yaml is the public-demo blueprint. No hostname is claimed until a deploy exists.",
                "Placeholder keys are ignored. /health reports offline_demo and the solver backend. AMD live mode stays off unless a real VLLM_API_KEY is set and the operator turns it on.",
                "No factory deal. No shipped MI300X timing. No committed Gemma latency. MIT. Public repo.",
                "Roadmap: keep the human on the send button, log a real Gemma chat only when a key is present, and publish extraction accuracy only after that log exists.",
                "Deadline per third-party listing: 18 Oct 2026 23:59 UTC (19 Oct 00:59 BST); on-site Luma pages list 16:00 local on 18 Oct; confirm on lablab.",
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


def _pdf_escape(text: str) -> str:
    ascii_text = (
        text.replace("¥", "CNY ")
        .replace("−", "-")
        .replace("—", "-")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )
    return ascii_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_line(text: str, width: int) -> list[str]:
    """Break on spaces so a bullet is not cut off mid-sentence."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def write_pdf(slides: list[tuple[str, list[str]]], out: Path) -> None:
    """Text-only PDF. One widescreen page per slide, lines wrapped, not truncated."""
    page_width = 960
    page_height = 540
    pages: list[bytes] = []
    for title, bullets in slides:
        raw = [title, ""]
        raw.extend(f"- {bullet}" for bullet in bullets)
        # 9pt Helvetica is about 5 units wide. 900pt of text fits well under 160 chars.
        lines: list[str] = []
        for raw_line in raw:
            lines.extend(_wrap_line(raw_line, 150))
        font = 11
        leading = 13
        if len(lines) > 36:
            font = 8
            leading = 10
        commands = ["BT", f"/F1 {font} Tf", f"36 {page_height - 36} Td", f"{leading} TL"]
        for line in lines:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        pages.append("\n".join(commands).encode("latin-1", errors="replace"))

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("ascii"))
    font_id = 3 + len(pages) * 2
    for index, content in enumerate(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Contents {4 + index * 2} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode("ascii")
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    blob = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(blob))
        blob.extend(f"{number} 0 obj\n".encode("ascii"))
        blob.extend(obj)
        blob.extend(b"\nendobj\n")
    xref = len(blob)
    blob.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    blob.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        blob.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    blob.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    out.write_bytes(blob)


def main() -> None:
    slides = build_slides()
    if len(slides) != 9:
        raise SystemExit(f"expected 9 slides, got {len(slides)}")
    render(slides, OUT)
    write_pdf(slides, PDF)
    print(f"saved: {OUT} slides: {len(slides)}")
    print(f"saved: {PDF}")


if __name__ == "__main__":
    main()
