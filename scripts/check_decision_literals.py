"""Fail when decision code grows an unregistered number or imports the simulator.

The registry is ``docs/constants_registry.json``. Every assignment in
``src/config/heuristics.py`` needs a row: value, category, and a
justification. Categories are ``c`` (cited identity), ``d`` (labelled
user-adjustable policy), and ``n`` (numerical guard). Decision modules may
use 0, 1, -1, and 2 (the variance factor and a binary split). Any other
numeric literal in those modules must be listed under ``inline``.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "constants_registry.json"
HEURISTICS = ROOT / "src" / "config" / "heuristics.py"
DECISION = (
    "src/engine/coupled.py",
    "src/engine/bayesian_v2.py",
    "src/engine/hazard.py",
    "src/engine/one_step.py",
    "src/engine/proposal.py",
    "src/engine/solver.py",
    "src/engine/multi_solver.py",
    "src/engine/logrolling.py",
    "src/engine/tco.py",
    "src/desk/recompute.py",
    "src/desk/parse.py",
    "src/desk/service.py",
    "src/app/orchestrator.py",
    "src/app/main.py",
)
MIN_JUSTIFICATION = 25
PRODUCTION = ("src/engine", "src/desk", "src/app")
DEMO_IMPORT = "src/app/demo_data.py"
# 0, 1, and -1 are identities. 2 is the variance factor. 3 and 4 are display
# rounding (round(x, 4)) and a default round count, not business prices.
STRUCTURAL = {0, 1, -1, 2, 3, 4}


def _assignments(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[target.id] = node.value
    return found


def _literals(path: Path) -> list[tuple[int, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if node.value in STRUCTURAL:
                continue
            if isinstance(node.value, float) and (abs(node.value) < 1e-3 or abs(node.value) >= 1e6):
                continue
            found.append((node.lineno, node.value))
    return found


def _data(value):
    if isinstance(value, tuple):
        return [_data(item) for item in value]
    if isinstance(value, list):
        return [_data(item) for item in value]
    return value


def _same(left, right) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        return abs(float(left) - float(right)) <= 1e-9
    return left == right


def _sim_imports() -> list[str]:
    problems = []
    for folder in PRODUCTION:
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel == DEMO_IMPORT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.sim"):
                    problems.append(f"{rel}:{node.lineno} imports {node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("src.sim"):
                            problems.append(f"{rel}:{node.lineno} imports {alias.name}")
    return problems


def scan() -> list[str]:
    problems = []
    if not REGISTRY.is_file():
        return ["docs/constants_registry.json is missing"]
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = registry.get("constants") or {}
    assignments = _assignments(HEURISTICS)
    module = {}
    sys.path.insert(0, str(ROOT))
    import src.config.heuristics as heuristics

    for name in assignments:
        module[name] = _data(getattr(heuristics, name))
    for name, value in module.items():
        row = rows.get(name)
        if row is None:
            problems.append(f"unregistered heuristic {name}")
            continue
        if row.get("category") not in {"c", "d", "n"}:
            problems.append(f"{name} category must be c, d, or n")
        justification = str(row.get("justification") or "").strip()
        if len(justification) < MIN_JUSTIFICATION or justification.lower() == name.lower():
            problems.append(f"{name} justification must be a sentence of at least {MIN_JUSTIFICATION} characters")
        registered = row.get("value")
        if isinstance(value, list):
            if list(registered) != value and [float(item) if isinstance(item, (int, float)) else item for item in registered] != value:
                if registered != value:
                    problems.append(f"{name} registry value {registered} != {value}")
        elif isinstance(value, float):
            if not _same(registered, value):
                problems.append(f"{name} registry value {registered} != {value}")
        elif registered != value:
            problems.append(f"{name} registry value {registered} != {value}")
    extra = sorted(set(rows) - set(module))
    for name in extra:
        problems.append(f"registry entry {name} is not an assignment in heuristics.py")
    for item in registry.get("inline") or []:
        justification = str(item.get("justification") or "").strip()
        if len(justification) < MIN_JUSTIFICATION:
            problems.append(
                f"inline {item.get('file')} {item.get('value')} justification must be a sentence of at least {MIN_JUSTIFICATION} characters"
            )
    allowed = {(item["file"], item["value"]) for item in registry.get("inline") or []}
    for rel in DECISION:
        for line, value in _literals(ROOT / rel):
            key = (rel, value if not isinstance(value, float) else value)
            matched = any(item[0] == rel and _same(item[1], value) for item in allowed)
            if not matched:
                problems.append(f"unregistered literal {rel}:{line} {value}")
    problems.extend(_sim_imports())
    return problems


def main() -> int:
    problems = scan()
    if problems:
        print("Decision literals or simulator imports are not registered:", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"decision registry ok ({len(json.loads(REGISTRY.read_text())['constants'])} constants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
