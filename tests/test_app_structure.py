"""The app layer's dependency direction, proven in a fresh interpreter.

[ARCHITECTURE 2026-10-24] main.py went from one 3,004-line module to an
assembly plus route modules. The extraction is only real if the route modules
cannot reach back into the assembly: a route module that imports
``src.app.main`` would make the split cosmetic and reintroduce the god module
as a load-bearing dependency. These tests prove, in a subprocess where
``src.app.main`` import is poisoned, that:

1. every route module and ``state`` imports cleanly with ``src.app.main``
   made unimportable (fail loudly on any back-coupling);
2. no route module leaves ``src.app.main`` in ``sys.modules`` after import;
3. the shared ``session_mgr`` is one object across ``state`` and ``main``
   (the conftest re-init pattern relies on that identity).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

ROUTE_MODULES = [
    "src.app.state",
    "src.app.routes_meta",
    "src.app.routes_negotiate",
    "src.app.routes_command",
    "src.app.routes_sessions",
    "src.app.routes_round",
    "src.app.routes_analysis",
    "src.app.routes_auction",
]


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


@pytest.mark.parametrize("module", ROUTE_MODULES)
def test_route_module_loads_without_main(module):
    """Import the module with src.app.main poisoned: any back-coupling dies here."""
    script = (
        "import sys\n"
        "sys.path.insert(0, r'%s')\n"
        "class _Poison(ModuleNotFoundError):\n"
        "    pass\n"
        "class _Blocker:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name == 'src.app.main' else None\n"
        "    def load_module(self, name):\n"
        "        raise _Poison('src.app.main must not be imported by ' + name)\n"
        "sys.meta_path.insert(0, _Blocker())\n"
        "import importlib\n"
        "importlib.import_module(%r)\n"
        "assert 'src.app.main' not in sys.modules, module + ' imported src.app.main'\n"
        "print('ok')\n"
    ) % (str(ROOT), module)
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_session_manager_is_one_object_across_state_and_main():
    """conftest re-initialises api_main.session_mgr in place; handlers read it
    through state. That contract needs module identity, not equality."""
    import src.app.main as main_module
    import src.app.state as state_module

    assert main_module.session_mgr is state_module.session_mgr


def test_every_route_module_is_registered_on_the_app():
    """A module that exists but is not registered silently drops its routes."""
    from src.app import main as main_module

    registered = {route.path for route in main_module._iter_api_routes()}
    for path in (
        "/api/v1/heuristics",
        "/api/v1/tco/evaluate",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}/round",
        "/api/v1/quotes/read",
        "/api/v1/multi-auction/run",
        "/api/v1/desk/deals",
    ):
        assert path in registered, f"{path} is not registered"
