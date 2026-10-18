#!/usr/bin/env python3
"""Fail if README.md or SUBMISSION.md quote a number that disagrees with its source.

Every checked string is derived here from a file in the repo. The script does
not scrape every integer (ports, years, and character counts would false-alarm).
It checks an explicit set of claims, plus a few phrases that must not reappear.

The deadline line in SUBMISSION.md records two public listings and tells a
person to confirm on lablab. It is not a measured result. The check rejects a
return to the unverified 11:00 London clock.
"""
from __future__ import annotations

import json
import math
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
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import scripts.regen_mi300x_roofline as regen

    roofline = regen.build_payload(bench)
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
        "roofline_label": roofline["label"],
        "roofline_formula": roofline["formula"],
        "roofline_datasheet": roofline["datasheet_url"],
        "roofline_product": roofline["product_page_url"],
        "roofline_bandwidth_quote": roofline["datasheet_quote_bandwidth"],
        "roofline_capacity_quote": roofline["datasheet_quote_capacity"],
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
            (claims["roofline_label"], "MI300X roofline label"),
            (claims["roofline_formula"], "MI300X roofline formula"),
            (claims["roofline_datasheet"], "MI300X datasheet URL"),
            (claims["roofline_product"], "MI300X product-page URL"),
            (claims["roofline_bandwidth_quote"], "MI300X datasheet bandwidth quote"),
            (claims["roofline_capacity_quote"], "MI300X datasheet capacity quote"),
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
            "11:00 London",
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
    problems.extend(_deadline_problems(submission))

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

    problems.extend(_savings_framing_problems(texts))
    problems.extend(_mi300x_problems())
    problems.extend(_gemma_and_comparison_problems(texts))
    problems.extend(_form_length_problems(submission))
    problems.extend(_integrity_doc_problems())

    if problems:
        print("Documented claims do not match their source files:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("check_claims: README.md, SUBMISSION.md, and MI300X figures match the source files.")
    return 0


_NUM = r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
_BAND = re.compile(_NUM + r"\s*(TB/s|GB/s|Gbps)\b", re.I)
_CAP = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*GB(?![A-Za-z/])", re.I)
_TIME = re.compile(
    _NUM + r"\s*(milliseconds|seconds|millisecond|second|msec|sec|ms|s)\b",
    re.I,
)
_SPEEDUP = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*[×xX](?!\s*\d)")
_TENTH = re.compile(r"tenth of a (?:milli)?second", re.I)
_MI_WORD = re.compile(r"MI300X", re.I)
_MI_FILE = re.compile(r"MI300X|HBM3|roofline", re.I)
_UNSOURCED = (
    "60 GB/s",
    "80 GB/s",
    "88.3",
    "531.8",
    "1843.7",
)


def _savings_framing_problems(texts: dict[Path, str]) -> list[str]:
    """Auction headline is the first-round best quote.

    Versus target must be described as visible when negative. A 22.2% figure,
    when quoted, has to sit in a paragraph that calls the walk-away ceiling
    secondary. Self-play percents are a different experiment and are not
    checked here.
    """
    problems: list[str] = []
    for path, text in texts.items():
        if "first-round best quote" not in text:
            problems.append(
                f"{path.name} does not name the auction headline baseline: first-round best quote"
            )
        if "including when that figure is negative" not in text:
            problems.append(
                f"{path.name} does not say auction versus-target is shown "
                "including when that figure is negative"
            )
        for para in re.split(r"\n\s*\n", text):
            if "22.2%" not in para:
                continue
            if "walk-away ceiling" not in para or "secondary" not in para.lower():
                problems.append(
                    f"{path.name} quotes 22.2% without calling it a secondary "
                    "walk-away ceiling baseline"
                )
    return problems


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _gemma_and_comparison_problems(texts: dict[Path, str]) -> list[str]:
    """Gemma is the configured default. The 348-call log stays Qwen.

    The offline comparison file is the only source for its three rates.
    A market-size dollar figure or the word TAM is rejected: this pack
    does not cite one.
    """
    problems: list[str] = []
    comp_path = ROOT / "results" / "llm_vs_tradeflow.json"
    comp = json.loads(comp_path.read_text(encoding="utf-8"))
    if comp.get("ran_on_amd") is not False or comp.get("live_endpoint_used") is not False:
        problems.append("results/llm_vs_tradeflow.json claims an AMD call this script did not verify")
    if comp.get("llm_buyer") != "offline_accept_opening":
        problems.append("results/llm_vs_tradeflow.json buyer is not the offline stand-in")
    labels = []
    for side in ("tradeflow", "offline_llm"):
        row = comp[side]
        labels.append(row["budget_violation_rate_label"])
        labels.append(row["deal_rate_label"])
        labels.append(row["average_savings_label"])
    for path, text in texts.items():
        if "Gemma-4-31B-IT" not in text:
            problems.append(f"{path.name} does not name the default Stage-2 model Gemma-4-31B-IT")
        if "No committed Gemma latency" not in text and "no committed Gemma latency" not in text:
            problems.append(f"{path.name} does not say there is no committed Gemma latency")
        for sentence in _sentences(text):
            if "Gemma-4-31B-IT" in sentence and "348 calls" in sentence:
                problems.append(f"{path.name} puts Gemma-4-31B-IT in the same sentence as the 348-call log")
        if "offline_accept_opening" not in text:
            problems.append(f"{path.name} does not name the offline comparison buyer")
        if "not an AMD call" not in text:
            problems.append(f"{path.name} does not say the comparison was not an AMD call")
        for label in labels:
            if label not in text:
                problems.append(f"{path.name} is missing comparison label {label}")
        if re.search(r"\bTAM\b", text):
            problems.append(f"{path.name} mentions TAM; this pack does not cite a sourced market size")
        if re.search(r"\$\s*\d[\d,]*\s*(?:billion|million)\b", text, re.I):
            problems.append(f"{path.name} states a market dollar size without a fetched source")
    return problems


def _integrity_doc_problems() -> list[str]:
    """The audit note must exist, and uncommitted latency rows must stay labeled."""
    problems: list[str] = []
    path = ROOT / "docs" / "DATA_INTEGRITY.md"
    if not path.is_file():
        return ["docs/DATA_INTEGRITY.md is missing"]
    text = path.read_text(encoding="utf-8")
    for snippet in (
        "not an AMD call",
        "No committed Gemma latency",
        "results/real_llm_benchmark.json",
        "not re-timed",
        "offline_accept_opening",
        "Qwen3.8-Flash-Next",
        "Gemma-4-31B-IT",
    ):
        if snippet not in text:
            problems.append(f"docs/DATA_INTEGRITY.md is missing {snippet!r}")
    if re.search(r"\bTAM\b", text):
        problems.append("docs/DATA_INTEGRITY.md mentions TAM; this pack does not cite a sourced market size")
    if re.search(r"\$\s*\d[\d,]*\s*(?:billion|million)\b", text, re.I):
        problems.append("docs/DATA_INTEGRITY.md states a market dollar size without a fetched source")
    limitations = (ROOT / "docs" / "LIMITATIONS_AND_REAL_WORLD_FINDINGS.md").read_text(encoding="utf-8")
    if "Measured latency" in limitations:
        problems.append("LIMITATIONS still titles a latency section as Measured latency")
    if "4.6 s" in limitations and "No file in this repository regenerates them" not in limitations:
        problems.append("LIMITATIONS quotes 4.6 s without saying no file regenerates that row")
    return problems


_DEADLINE = (
    "Deadline per third-party listing: 18 Oct 2026 23:59 UTC (19 Oct 00:59 BST); "
    "on-site Luma pages list 16:00 local on 18 Oct; confirm on lablab."
)


def _deadline_problems(submission: str) -> list[str]:
    """The 11:00 London clock was unverified. Keep the two public listings."""
    problems: list[str] = []
    if _DEADLINE not in submission:
        problems.append(
            "SUBMISSION.md must state the third-party listing, the on-site Luma time, "
            "and confirm on lablab"
        )
    for snippet in (
        "quote reading uses the AMD tokenfactory and Gemma-4-31B-IT",
        "generated by the script and is not committed unless present",
        "not a quote extraction",
        "does not prove which GPU served the request",
    ):
        if snippet not in submission:
            problems.append(f"SUBMISSION.md is missing the quote-reading limit: {snippet!r}")
    for path in (
        ROOT / "docs" / "LABLAB_FORM.md",
        ROOT / "docs" / "VERIFICATION.md",
        ROOT / "docs" / "DATA_INTEGRITY.md",
        ROOT / "scripts" / "make_pitch.py",
    ):
        text = path.read_text(encoding="utf-8")
        if "11:00 London" in text:
            problems.append(f"{path.as_posix()} still states the unverified 11:00 London deadline")
        if path.name != "DATA_INTEGRITY.md" and _DEADLINE not in text:
            problems.append(f"{path.as_posix()} is missing the listing deadline")
    if "generated by the script and is not committed unless present" not in (
        ROOT / "docs" / "DATA_INTEGRITY.md"
    ).read_text(encoding="utf-8"):
        problems.append("docs/DATA_INTEGRITY.md must say the evidence file is generated and not committed unless present")
    return problems


def _form_length_problems(submission: str) -> list[str]:
    """lablab field limits: title 5–50, short 50–255, long 600–2000."""
    problems: list[str] = []
    form = (ROOT / "docs" / "LABLAB_FORM.md").read_text(encoding="utf-8")
    title_match = re.search(r"## Title\s+```\s*([^\n`]+)\s*```", form)
    if title_match is None:
        problems.append("docs/LABLAB_FORM.md has no fenced title")
    else:
        title = title_match.group(1).strip()
        if not 5 <= len(title) <= 50:
            problems.append(f"form title is {len(title)} characters; allowed range is 5-50")
    paras = _section_paragraphs(submission, "## Short description")
    if paras:
        short = paras[0]
        if not 50 <= len(short) <= 255:
            problems.append(f"short description is {len(short)} characters; allowed range is 50-255")
    start = submission.index("## Long description") + len("## Long description")
    end = submission.index("## Demo video script")
    long_text = submission[start:end].strip()
    if not 600 <= len(long_text) <= 2000:
        problems.append(f"long description is {len(long_text)} characters; allowed range is 600-2000")
    return problems


def _near(value: float, targets: list[float]) -> bool:
    return any(math.isclose(value, target, rel_tol=1e-4, abs_tol=1e-9) for target in targets)


def _scan_roots() -> list[Path]:
    found = [README, SUBMISSION, ROOT / "docker-compose.yml", ROOT / "Dockerfile"]
    found.extend(sorted((ROOT / "docs").rglob("*.md")))
    found.append(ROOT / "results" / "bench.json")
    found.append(ROOT / "results" / "mi300x_roofline.json")
    return [path for path in found if path.is_file()]


def _mi300x_problems() -> list[str]:
    """Fail when a doc states an MI300X figure the regenerator does not emit."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import scripts.regen_mi300x_roofline as regen

    problems = [f"results: {item}" for item in regen._problems()]
    payload = regen.build_payload()
    label = payload["label"]
    bandwidth = [
        ("tb/s", float(payload["peak_bandwidth_tb_s"])),
        ("gb/s", float(payload["peak_bandwidth_gb_s"])),
        ("gbps", float(payload["footnote"]["pin_rate_gbps"])),
        ("gb/s", float(payload["footnote"]["product_gb_s"])),
        ("tb/s", float(payload["footnote"]["quoted_rounding_tb_s"])),
    ]
    times = [float(row["lower_bound_seconds"]) for row in payload["rows"]]
    capacity = [float(payload["hbm3_capacity_gb"])]
    quoted_bandwidth_units = {"tb/s", "gb/s"}

    for path in _scan_roots():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if not _MI_FILE.search(text):
            continue
        for banned in _UNSOURCED:
            if banned in text:
                problems.append(f"{rel} still contains unsourced {banned!r}")
        cites_bandwidth = False
        for match in _BAND.finditer(text):
            unit = match.group(2).lower()
            value = float(match.group(1))
            allowed = [item for item in bandwidth if item[0] == unit]
            if not _near(value, [item[1] for item in allowed]):
                problems.append(
                    f"{rel} states {match.group(0).strip()} and that figure is not in results/mi300x_roofline.json"
                )
            elif unit in quoted_bandwidth_units and _near(
                value, [payload["peak_bandwidth_tb_s"], payload["peak_bandwidth_gb_s"]]
            ):
                cites_bandwidth = True
        flat_for_capacity = re.sub(r"\s+", " ", text)
        hw_spans = [item.span() for item in re.finditer(r"MI300X|HBM3", flat_for_capacity, re.I)]
        for match in _CAP.finditer(flat_for_capacity):
            if not any(abs(match.start() - left) <= 80 or abs(match.start() - right) <= 80 for left, right in hw_spans):
                continue
            value = float(match.group(1))
            if not _near(value, capacity):
                problems.append(
                    f"{rel} states {match.group(0).strip()} beside an MI300X/HBM3 note and that capacity is not in results/mi300x_roofline.json"
                )
        flat = re.sub(r"\s+", " ", text)
        mi_spans = [item.span() for item in _MI_WORD.finditer(flat)]

        def _beside(start: int) -> bool:
            return any(abs(start - left) <= 140 or abs(start - right) <= 140 for left, right in mi_spans)

        for tenth in _TENTH.finditer(flat):
            if _beside(tenth.start()):
                problems.append(
                    f"{rel} gives MI300X a 'tenth of a second/millisecond' with no sourced bound"
                )
        for time_match in _TIME.finditer(flat):
            if not _beside(time_match.start()):
                continue
            raw = float(time_match.group(1))
            unit = time_match.group(2).lower()
            seconds = raw / 1000.0 if unit in {"ms", "msec", "millisecond", "milliseconds"} else raw
            if not _near(seconds, times):
                problems.append(
                    f"{rel} states MI300X time {time_match.group(0).strip()} which is not a regenerated lower bound"
                )
        for speed_match in _SPEEDUP.finditer(flat):
            if not _beside(speed_match.start()):
                continue
            problems.append(
                f"{rel} states MI300X multiplier {speed_match.group(0).strip()} and no sourced ratio exists"
            )
        if cites_bandwidth and label not in text:
            problems.append(f"{rel} quotes MI300X bandwidth without the label {label!r}")
    unique: list[str] = []
    seen: set[str] = set()
    for item in problems:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


if __name__ == "__main__":
    raise SystemExit(main())
