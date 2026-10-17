#!/usr/bin/env python3
"""Fail if README.md or SUBMISSION.md quote a number that disagrees with its source.

Every checked string is derived here from a file in the repo. The script does
not scrape every integer (ports, years, and character counts would false-alarm).
It checks an explicit set of claims, plus a few phrases that must not reappear.

The deadline line in SUBMISSION.md is the submission instruction for this pack,
not a measured result. It is checked so the pack does not drift back to an
older clock time.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SUBMISSION = ROOT / "SUBMISSION.md"
DOCS = (README, SUBMISSION)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _pct(rate: float) -> str:
    return f"{rate * 100:.2f}%"


def _self_play(text: str) -> dict:
    per = re.search(r"Matches per Archetype:\s*(\d+)", text)
    seed = re.search(r"Seed:\s*(\d+)", text)
    if not per or not seed:
        raise SystemExit("results/self_play_benchmark.md lost its archetype or seed header")
    engine = []
    fixed_hard = None
    current = None
    for line in text.splitlines():
        header = re.match(r"\|\s+\*\*(.+?)\*\*", line)
        if header:
            current = header.group(1)
        percents = re.findall(r"(\d+\.\d+)%", line)
        if "TradeFlow Engine" in line:
            if len(percents) < 2:
                raise SystemExit(f"engine row has no savings cell: {line}")
            engine.append((current, percents[1]))
        if current == "Hard-Bargainer" and "Fixed 5%/rd Baseline" in line:
            if len(percents) < 2:
                raise SystemExit(f"baseline row has no savings cell: {line}")
            fixed_hard = percents[1]
    if len(engine) != 5 or fixed_hard is None:
        raise SystemExit(
            f"self-play table parse failed: {len(engine)} engine rows, baseline={fixed_hard}"
        )
    savings = [float(s) for _, s in engine]
    return {
        "per_archetype": int(per.group(1)),
        "n_archetypes": len(engine),
        "total": int(per.group(1)) * len(engine),
        "seed": int(seed.group(1)),
        "min_savings": f"{min(savings):.1f}%",
        "max_savings": f"{max(savings):.1f}%",
        "hard_engine": f"{engine[0][1]}%",
        "hard_fixed": f"{fixed_hard}%",
    }


def _claims() -> dict:
    llm = json.loads((ROOT / "results" / "real_llm_benchmark_100.json").read_text(encoding="utf-8"))
    totals = llm["totals"]
    bench = json.loads((ROOT / "results" / "bench.json").read_text(encoding="utf-8"))
    metrics = json.loads((ROOT / "models" / "onnx_export_metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data" / "ood_split_manifest.json").read_text(encoding="utf-8"))
    play = _self_play(_read(ROOT / "results" / "self_play_benchmark.md"))

    correct = int(metrics["held_out_ood_correct"])
    held = int(metrics["held_out_ood_total"])
    if held != int(manifest["test_count"]):
        raise SystemExit("held-out total disagrees with data/ood_split_manifest.json test_count")
    split_n = int(manifest["train_count"]) + int(manifest["test_count"])
    bandwidth = float(bench["roofline_specs"]["mi300x_hbm3_bandwidth_gb_s"])
    bluff = float(llm["archetypes"]["Bluffing Hardliner"]["deal_rate"])
    completed = int(totals["completed_matches"])
    violations = float(totals["violation_rate"])
    if violations != 0.0:
        violation_phrase = f"violation rate {violations} in {completed} completed matches"
    else:
        violation_phrase = f"0 budget violations in {completed} completed matches"

    return {
        "model": str(llm["meta"]["model"]),
        "calls": str(int(totals["llm_calls"])),
        "latency": f"{float(totals['llm_avg_latency_s']):.2f}",
        "cost": f"{float(totals['llm_total_cost_usd']):.6f}",
        "bluff_deal": _pct(bluff),
        "violations": violation_phrase,
        "gpu_flag": f"is_gpu_active: {str(bool(bench['is_gpu_active'])).lower()}",
        "roofline": (
            f"roofline constant in results/bench.json "
            f"({bandwidth:.0f} GB/s, {bandwidth / 1000:.1f} TB/s), not a measured runtime"
        ),
        "held_out_acc": f"{100.0 * correct / held:.2f}%",
        "held_out_split": f"{held} of {split_n}",
        "play": play,
    }


def _section_paragraphs(md: str, heading: str) -> list[str]:
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i + 1
            break
    if start is None:
        return []
    paras: list[str] = []
    buf: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        if not line.strip():
            if buf:
                paras.append(" ".join(part.strip() for part in buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        paras.append(" ".join(part.strip() for part in buf))
    return paras


def main() -> int:
    problems: list[str] = []
    claims = _claims()
    texts = {path: _read(path) for path in DOCS}

    def need(path: Path, snippet: str, label: str) -> None:
        if snippet not in texts[path]:
            problems.append(f"{path.name} is missing {label}: {snippet!r}")

    for path in DOCS:
        text = texts[path]
        for snippet, label in (
            (claims["model"], "LLM model name"),
            (f"{claims['calls']} calls", "LLM call count"),
            (claims["latency"], "LLM average latency"),
            (claims["cost"], "LLM cost"),
            (claims["bluff_deal"], "Bluffing Hardliner deal rate"),
            (claims["violations"], "budget-violation count"),
            (claims["gpu_flag"], "bench.json GPU flag"),
            (claims["roofline"], "MI300X roofline constant"),
            (claims["held_out_acc"], "held-out accuracy"),
            (claims["held_out_split"], "held-out split size"),
            (claims["play"]["min_savings"], "self-play minimum engine savings"),
            (claims["play"]["max_savings"], "self-play maximum engine savings"),
            (claims["play"]["hard_engine"], "Hard-Bargainer engine savings"),
            (claims["play"]["hard_fixed"], "Hard-Bargainer fixed baseline savings"),
            (f"{claims['play']['per_archetype']} matches", "matches per archetype"),
            (f"{claims['play']['n_archetypes']} archetypes", "archetype count"),
            (f"seed {claims['play']['seed']}", "self-play seed"),
        ):
            need(path, snippet, label)

        for forbidden in (
            "Intel Core Ultra",
            "about $0.108",
            "15:00 London",
            "blind held-out",
            "92.00%",
        ):
            if forbidden in text:
                problems.append(f"{path.name} still contains {forbidden!r}")

        for match_n in re.findall(r"(\d+) matches", text):
            n = int(match_n)
            allowed = {claims["play"]["per_archetype"], claims["play"]["total"]}
            if n not in allowed:
                problems.append(
                    f"{path.name} says {n} matches; self-play source allows {sorted(allowed)}"
                )

        # 61.1% is a rounding of deal_rate 0.6111. 61.11% contains those characters,
        # so only flag a 61.1% that is not the longer 61.11%.
        if re.search(r"(?<![\d.])61\.1%(?!\d)", text):
            problems.append(f"{path.name} quotes 61.1%; the source formats as {claims['bluff_deal']}")
        if re.search(r"(?<![\d.])63%(?!\d)", text):
            problems.append(
                f"{path.name} quotes 63%; the held-out source formats as {claims['held_out_acc']}"
            )

    submission = texts[SUBMISSION]
    if "11:00 London" not in submission or "BST" not in submission or "18 October 2026" not in submission:
        problems.append(
            "SUBMISSION.md deadline must state 11:00 London (BST), 18 October 2026"
        )

    paras = _section_paragraphs(submission, "## Short description")
    if not paras:
        problems.append("SUBMISSION.md has no short description")
    else:
        tagline = paras[0]
        stated = re.search(r"\((\d+) characters", paras[1] if len(paras) > 1 else "")
        if stated is None or int(stated.group(1)) != len(tagline):
            problems.append(
                f"short description is {len(tagline)} characters; the stated count disagrees"
            )
        shorter = re.search(r'use: "([^"]+)" — (\d+) characters', submission)
        if shorter is None or int(shorter.group(2)) != len(shorter.group(1)):
            problems.append("the shorter tagline's stated character count disagrees with its text")

    if problems:
        print("Documented claims do not match their source files:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("check_claims: README.md and SUBMISSION.md match the source files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
