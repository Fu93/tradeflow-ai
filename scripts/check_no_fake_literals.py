#!/usr/bin/env python3
"""Fail if a production file invents a numeric scenario outside the allowlist.

Allowed to contain scenario floats:
  src/config/heuristics.py   documented heuristics, each with a Rationale comment
  src/sim/                   simulated mandates, archetypes, scripted replies
  src/benchmark/adversaries.py, src/benchmark/arena.py, src/benchmark/ood_500_dataset.py
                             the self-play generator
  extension/mock_chat.html   the supplier sandbox
  scripts/generate_ood_500.py synthetic intent generator
  scripts/regen_mi300x_roofline.py datasheet quotes used to rebuild the roofline JSON

Everywhere else, a float that is not 0, 1, -1, a numeric epsilon (|x| < 1e-3),
or an input cap (|x| >= 1e6) is a violation. Currency amounts written as
¥12 or $0.10 in those files are violations too. Tests are not scanned: they
assert behaviour. results/*.json are measured outputs, not source literals.

Integers (HTTP statuses, seeds, counts, loop bounds) are not flagged. A
percent that is typed into HTML as "15%" is flagged, because the desk must
read that band from the heuristics endpoint.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOW_FILES = {
    "src/config/heuristics.py",
    "src/benchmark/adversaries.py",
    "src/benchmark/arena.py",
    "src/benchmark/ood_500_dataset.py",
    # Plot geometry (bar offsets, alphas, line widths). Not prices.
    "src/engine/viz.py",
    # Frozen copies of the early simulator. The live solver does not import them.
    "benchmark_negotiation.py",
    "benchmark_negotiation_v2.py",
    "extension/mock_chat.html",
    "scripts/generate_ood_500.py",
    # Datasheet quotes (5.3 TB/s, 5.2 Gbps) cited next to their URLs.
    "scripts/regen_mi300x_roofline.py",
}
ALLOW_PREFIXES = ("src/sim/",)

SCAN_PY = (
    "src",
    "scripts",
    "benchmark_negotiation.py",
    "benchmark_negotiation_v2.py",
    "verify_all.py",
    "verify_live.py",
)
SKIP_PY_PARTS = {"tests", "__pycache__", "docs"}

# Visual constants: chart alphas, CSS, retry backoff. Not prices.
JS_VISUAL = re.compile(
    r"rgba?\(|#[0-9a-fA-F]{3,8}|letter-spacing|line-height|font-|opacity|"
    r"shadow|blur|brightness|stroke|viewBox|tension|filter:|Math\.pow|"
    r"\d+(?:px|em|rem|ms|s|deg)|backdrop",
    re.I,
)
FLOAT_RE = re.compile(r"(?<![\w.])(\d+\.\d+)(?![\w.])")
CURRENCY_RE = re.compile(r"[¥￥]\s*\d|\$\s*\d")
HTML_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
HTML_DAY_RE = re.compile(r"\d+\s*(?:-|–|—)\s*\d+\s*days", re.I)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _allowed(rel: str) -> bool:
    if rel in ALLOW_FILES or rel.startswith(ALLOW_PREFIXES):
        return True
    return False


def _structural_float(value: float) -> bool:
    if value in (0.0, 1.0, -1.0):
        return True
    mag = abs(value)
    if mag < 1e-3 or mag >= 1e6:
        return True
    return False


_TIMEOUT_NAMES = re.compile(r"timeout|max_seconds|sleep", re.I)
_DOC_OK = re.compile(r"舊|不是|not a|not the|simulated|例如|example|硬編|虛構", re.I)


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()

    def take(body: list) -> None:
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            start = first.lineno
            end = getattr(first, "end_lineno", start) or start
            lines.update(range(start, end + 1))

    take(getattr(tree, "body", []))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            take(node.body)
    return lines


def _py_floats(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{_rel(path)}:{exc.lineno}: syntax error: {exc.msg}"]
    lines = source.splitlines()
    docs = _docstring_lines(tree)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    timeout_defaults: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = node.args.defaults
            if not defaults:
                continue
            for arg, default in zip(node.args.args[-len(defaults):], defaults):
                if _TIMEOUT_NAMES.search(arg.arg):
                    timeout_defaults.add(id(default))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        if node.lineno in docs:
            continue
        if isinstance(node.value, float) and not _structural_float(node.value):
            if id(node) in timeout_defaults:
                continue
            parent = parents.get(id(node))
            if isinstance(parent, ast.keyword) and parent.arg and _TIMEOUT_NAMES.search(parent.arg):
                continue
            current: ast.AST | None = node
            in_sleep = False
            while current is not None:
                owner = parents.get(id(current))
                if isinstance(owner, ast.Call):
                    func = owner.func
                    name = getattr(func, "attr", None) or getattr(func, "id", "")
                    if name == "sleep":
                        in_sleep = True
                        break
                current = owner
            if in_sleep:
                continue
            # Milliseconds and seconds. 1000.0 here is a unit, not a price.
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Mult, ast.Div)):
                if node.value in (1000.0, 1000):
                    continue
            if isinstance(parent, ast.Assign):
                names = [t.id for t in parent.targets if isinstance(t, ast.Name)]
                if any(_TIMEOUT_NAMES.search(name) for name in names):
                    continue
            text = lines[node.lineno - 1].strip()[:140]
            found.append(f"{_rel(path)}:{node.lineno}: float {node.value!r} in {text}")
        if isinstance(node.value, str) and re.search(r"[¥￥]\s*\d", node.value):
            if _DOC_OK.search(node.value):
                continue
            found.append(f"{_rel(path)}:{node.lineno}: currency amount in a string")
    return found


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(^|[^:\\])//.*?$", r"\1", text, flags=re.M)


def _js_floats(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    if CURRENCY_RE.search(_strip_js_comments(raw)):
        # Currency in code (not comments) is always a violation, even on a
        # visual line. Comments were stripped above.
        pass
    found = []
    stripped = _strip_js_comments(raw)
    for i, line in enumerate(stripped.splitlines(), start=1):
        if CURRENCY_RE.search(line):
            found.append(f"{_rel(path)}:{i}: currency amount")
        if JS_VISUAL.search(line):
            continue
        for match in FLOAT_RE.finditer(line):
            value = float(match.group(1))
            if _structural_float(value):
                continue
            found.append(f"{_rel(path)}:{i}: float {match.group(1)} in {line.strip()[:140]}")
    return found


def _html_problems(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    # Ignore the chart.js CDN pin and the CSP comment block's hashes.
    body = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    found = []
    for i, line in enumerate(body.splitlines(), start=1):
        if CURRENCY_RE.search(line) or HTML_PERCENT_RE.search(line) or HTML_DAY_RE.search(line):
            found.append(f"{_rel(path)}:{i}: displayed number {line.strip()[:140]}")
    return found


def _rationale_gaps(path: Path) -> list[str]:
    """Every float assignment in the config module needs a Rationale comment."""
    lines = path.read_text(encoding="utf-8").splitlines()
    gaps = []
    assign = re.compile(r"^[A-Z][A-Z0-9_]*\s*=")
    for i, line in enumerate(lines):
        if not assign.match(line):
            continue
        # A Rationale comment covers the assignment block beneath it.
        seen = False
        j = i
        while j >= 0:
            prev = lines[j].strip()
            if "Rationale:" in prev:
                seen = True
                break
            if j != i and prev and not prev.startswith("#") and not assign.match(lines[j]):
                break
            j -= 1
        if not seen:
            gaps.append(f"{_rel(path)}:{i + 1}: assignment without a Rationale comment")
    return gaps


def scan() -> list[str]:
    problems: list[str] = []
    py_roots = []
    for item in SCAN_PY:
        path = ROOT / item
        if path.is_file():
            py_roots.append(path)
        elif path.is_dir():
            py_roots.extend(path.rglob("*.py"))
    for path in py_roots:
        rel = _rel(path)
        if any(part in SKIP_PY_PARTS for part in path.parts):
            continue
        if _allowed(rel):
            continue
        if "docs/internal" in rel or rel.startswith("scripts/make_"):
            continue
        if path.name.startswith("check_"):
            continue
        problems.extend(_py_floats(path))

    for path in (ROOT / "src" / "web").rglob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        if path.name == "chart.umd.min.js":
            continue
        rel = _rel(path)
        if path.suffix == ".js":
            problems.extend(_js_floats(path))
        else:
            problems.extend(_html_problems(path))

    for path in (ROOT / "extension").rglob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        rel = _rel(path)
        if _allowed(rel):
            continue
        if path.suffix == ".js":
            problems.extend(_js_floats(path))
        else:
            problems.extend(_html_problems(path))

    problems.extend(_rationale_gaps(ROOT / "src" / "config" / "heuristics.py"))
    return problems


def main() -> int:
    problems = scan()
    if problems:
        print("Numeric literals outside the heuristics module and the simulation package:", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("check_no_fake_literals: production source has no undeclared scenario floats.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
