"""Every environment variable the desk reads is named in the README."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESK = ROOT / "src" / "desk"
README = ROOT / "README.md"


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_environ_get(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and _is_environ(node.func.value)


def _is_getenv(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getenv"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    )


def _const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _names_in(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (_is_environ_get(node) or _is_getenv(node)) and node.args:
            name = _const(node.args[0])
            if name:
                found.add(name)
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            name = _const(node.slice)
            if name:
                found.add(name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_env":
            for arg in node.args:
                name = _const(arg)
                if name:
                    found.add(name)
        if isinstance(node, ast.GeneratorExp):
            for generator in node.generators:
                if not isinstance(generator.iter, (ast.Tuple, ast.List)):
                    continue
                values = [item for item in (_const(elt) for elt in generator.iter.elts) if item]
                target = generator.target.id if isinstance(generator.target, ast.Name) else ""
                if values and target and _get_uses_name(node.elt, target):
                    found.update(values)
    return found


def _get_uses_name(node: ast.AST, target: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and (_is_environ_get(child) or _is_getenv(child)) and child.args:
            arg = child.args[0]
            if isinstance(arg, ast.Name) and arg.id == target:
                return True
    return False


def desk_env_names() -> set[str]:
    found: set[str] = set()
    for path in DESK.rglob("*.py"):
        found.update(_names_in(ast.parse(path.read_text(encoding="utf-8"))))
    return found


def test_readme_names_every_desk_env_var():
    readme = README.read_text(encoding="utf-8")
    missing = sorted(name for name in desk_env_names() if name not in readme)
    assert missing == []
    assert "TRADEFLOW_LEGACY" in readme
    assert "Code freeze after 27 Oct 2026" in readme
