"""The deck, the form sheet, preflight, and the container files stay honest."""
from __future__ import annotations

import html
import re
import socket
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fence(text: str, marker: str = "~~~") -> str:
    start = text.index(marker)
    end = text.index(marker, start + len(marker))
    return text[start + len(marker) : end].strip()


def _section(text: str, heading: str, stop: str) -> str:
    start = text.index(heading) + len(heading)
    end = text.index(stop, start)
    return text[start:end].strip()


def test_lablab_form_copies_submission_text():
    submission = (ROOT / "SUBMISSION.md").read_text(encoding="utf-8")
    form = (ROOT / "docs" / "LABLAB_FORM.md").read_text(encoding="utf-8")
    short = "One-person sourcing desk: a Bayesian Expectimax engine sets the price, an AMD-hosted model only phrases it, and a human sends the offer."
    shorter = "Expectimax prices the offer. An AMD-hosted model only phrases it. A human sends it."
    assert short in submission and short in form
    assert shorter in submission and shorter in form
    assert len(short) == 136
    assert len(shorter) == 83
    long_sub = _section(submission, "## Long description", "## Demo video script")
    long_form = _fence(form)
    assert long_form == long_sub
    assert 600 <= len(long_sub) <= 2000
    assert 5 <= len("TradeFlow AI") <= 50
    assert 50 <= len(short) <= 255
    assert "first-round best quote" in long_form
    assert "including when that figure is negative" in long_form
    assert "Reinvent Commerce" in form


def test_pitch_slides_use_source_claims_and_the_new_savings_order():
    from scripts.check_claims import _claims
    from scripts.make_pitch import auction_baseline_lines, build_slides

    slides = build_slides()
    assert len(slides) == 9
    body = "\n".join(bullet for _, bullets in slides for bullet in bullets)
    claims = _claims()
    for snippet in (
        claims["model"],
        f"{claims['calls']} calls",
        claims["latency"],
        claims["cost"],
        claims["bluff_deal"],
        claims["violations"],
        claims["roofline_label"],
        claims["roofline_formula"],
        claims["held_out_acc"],
        claims["held_out_split"],
        claims["play"]["min_savings"],
        claims["play"]["max_savings"],
        claims["play"]["hard_engine"],
        claims["play"]["hard_fixed"],
        "first-round best quote",
        "including when that figure is negative",
        "18 Oct 2026 23:59 UTC (19 Oct 00:59 BST)",
    ):
        assert snippet in body, snippet
    baselines = auction_baseline_lines(
        (ROOT / "results" / "multi_supplier_auction.md").read_text(encoding="utf-8")
    )
    headline_at = body.index(baselines[0])
    target_at = body.index(baselines[1])
    ceiling_at = body.index(baselines[2])
    assert headline_at < target_at < ceiling_at
    assert "is_gpu_active: false" in body
    for banned in (
        "about $0.108",
        "Intel Core Ultra",
        "15:00 London",
        "11:00 London",
        "blind held-out",
        "92.00%",
        "332 tests",
        "Evolus Business Agents",
    ):
        assert banned not in body
    assert re.search(r"(?<![\d.])61\.1%(?!\d)", body) is None


def test_committed_deck_matches_the_builder():
    from scripts.make_pitch import build_slides

    path = ROOT / "results" / "pitch.pptx"
    assert path.is_file()
    slides = build_slides()
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        xml = html.unescape("\n".join(archive.read(name).decode("utf-8") for name in names))
    assert len(names) == 9
    pdf = (ROOT / "results" / "pitch.pdf").read_bytes()
    assert pdf.startswith(b"%PDF")
    assert b"Market, revenue, competition" in pdf
    # DrawingML keeps one run per bullet. Ampersands are escaped in the XML.
    for _, bullets in slides:
        for bullet in bullets:
            assert bullet in xml


def test_preflight_hides_secret_values_and_names_fixes():
    from scripts.preflight import check_env, check_server, classify_env, readme_result_paths

    secret = "sk-live-SHOULD-NOT-PRINT-99"
    text = "\n".join(
        [
            "VLLM_API_KEY=" + secret,
            "VLLM_BASE_URL=https://example.invalid",
            "TRADEFLOW_ENV=sandbox",
            "EMPTY=",
            "# comment",
        ]
    )
    rows = dict(classify_env(text))
    assert rows["VLLM_API_KEY"] == "set"
    assert rows["EMPTY"] == "placeholder"
    rendered = "\n".join(f"{key}: {status}" for key, status in rows.items())
    assert secret not in rendered
    assert "https://example.invalid" not in rendered

    missing = ROOT / "tests" / "_no_such_env"
    report = "\n".join(check_env(missing))
    assert "FIX: cp .env.example .env" in report
    assert secret not in report

    closed = check_server(59991)
    assert any(line.startswith("FIX:") for line in closed)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    paths = readme_result_paths(readme)
    assert "results/pitch.pptx" in paths
    assert "results/amd_endpoint_evidence.json" not in paths
    assert "docs/DEMO_RUNBOOK.md" in paths


def test_container_files_pin_user_health_and_optional_env():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1" in docker
    assert "USER tradeflow" in docker
    assert "useradd" in docker
    assert "HEALTHCHECK" in docker
    assert "required: false" in compose
    assert "healthcheck:" in compose
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        from scripts.preflight import port_is_open

        port = held.getsockname()[1]
        assert port_is_open(port)
    finally:
        held.close()
