"""Fail when decision code grows an unregistered number or imports the simulator.

The registry is ``docs/constants_registry.json``. Every assignment in
``src/config/heuristics.py`` needs a row: value, category, and a
justification. Categories are ``c`` (cited identity), ``d`` (labelled
user-adjustable policy), and ``n`` (numerical guard). Decision modules may
use 0, 1, -1, and 2 (the variance factor and a binary split). 3, 4, and
tiny floats need a registry row. Any other numeric literal in those
modules must be listed under ``inline`` with the source line as ``context``.
A number that is already registered on a different line does not cover a new use.
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
    "src/desk/routes.py",
    "src/engine/quote_fields.py",
    "src/engine/bilingual.py",
    "src/app/orchestrator.py",
    "src/app/main.py",
    "src/engine/landed.py",
    "src/engine/quote_read.py",
    "src/engine/model.py",
    "src/desk/store.py",
    "src/engine/reserve.py",
    "src/engine/decision.py",
    "src/engine/hazard_fit.py",
    "src/desk/span_read.py",
    "src/desk/extract.py",
    "src/desk/shadow.py",
    "src/desk/headline.py",
)
MIN_JUSTIFICATION = 25
PRODUCTION = ("src/engine", "src/desk", "src/app", "src/legacy")
DEMO_IMPORT = "src/app/demo_data.py"
# 0, 1, and -1 are identities. 2 is the variance factor in a normal density.
STRUCTURAL = {0, 1, -1, 2}


def _assignments(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    found[target.id] = node.value
    return found


_FOLD_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


def _structural_expr(node: ast.AST) -> bool:
    """True when the node is arithmetic built only from 0, 1, -1, and 2."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value in STRUCTURAL
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _structural_expr(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _FOLD_OPS):
        return _structural_expr(node.left) and _structural_expr(node.right)
    return False


def _eval_structural(node: ast.AST) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        value = _eval_structural(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    left = _eval_structural(node.left)
    right = _eval_structural(node.right)
    if isinstance(node.op, ast.Add):
        return left + right
    if isinstance(node.op, ast.Sub):
        return left - right
    if isinstance(node.op, ast.Mult):
        return left * right
    if isinstance(node.op, ast.Div):
        return left / right
    if isinstance(node.op, ast.FloorDiv):
        return left // right
    if isinstance(node.op, ast.Mod):
        return left % right
    return left ** right


def _literals(path: Path) -> list[tuple[int, object, str]]:
    source = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(source))
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and _structural_expr(node):
            parent = parents.get(id(node))
            if isinstance(parent, (ast.BinOp, ast.UnaryOp)) and _structural_expr(parent):
                continue
            value = _eval_structural(node)
            if isinstance(value, bool) or value in STRUCTURAL:
                continue
            context = source[node.lineno - 1].strip()
            found.append((node.lineno, value, context))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if node.value in STRUCTURAL:
                continue
            context = source[node.lineno - 1].strip()
            found.append((node.lineno, node.value, context))
    return found


def _decision_files() -> list[str]:
    found = []
    for folder in ("src/engine", "src/desk"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            found.append(path.relative_to(ROOT).as_posix())
    for path in sorted((ROOT / "src" / "legacy").glob("*.py")):
        found.append(path.relative_to(ROOT).as_posix())
    for extra in ("src/app/main.py", "src/app/orchestrator.py"):
        if extra not in found:
            found.append(extra)
    return found


def _data(value):
    if isinstance(value, tuple):
        return [_data(item) for item in value]
    if isinstance(value, list):
        return [_data(item) for item in value]
    return value


def _same(left, right) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        lf, rf = float(left), float(right)
        scale = max(abs(lf), abs(rf), 1.0)
        return abs(lf - rf) <= 1e-9 * scale
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


def _live_literals() -> list[tuple[str, object, str]]:
    found = []
    for rel in _decision_files():
        for _line, value, context in _literals(ROOT / rel):
            found.append((rel, value, context))
    return found


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
    allowed = []
    for item in registry.get("inline") or []:
        justification = str(item.get("justification") or "").strip()
        context = str(item.get("context") or "").strip()
        if len(justification) < MIN_JUSTIFICATION:
            problems.append(
                f"inline {item.get('file')} {item.get('value')} justification must be a sentence of at least {MIN_JUSTIFICATION} characters"
            )
        if not context:
            problems.append(f"inline {item.get('file')} {item.get('value')} needs the source line as context")
            continue
        allowed.append((item.get("file"), item.get("value"), context))
    live = _live_literals()
    for rel in _decision_files():
        for line, value, context in _literals(ROOT / rel):
            matched = any(
                item[0] == rel and item[2] == context and _same(item[1], value)
                for item in allowed
            )
            if not matched:
                problems.append(f"unregistered literal {rel}:{line} {value} :: {context}")
    for rel, value, context in allowed:
        matched_live = any(
            site[0] == rel and site[2] == context and _same(site[1], value)
            for site in live
        )
        if not matched_live:
            problems.append(f"stale inline {rel} {value} :: {context}")
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
