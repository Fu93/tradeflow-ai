"""Decision modules stay free of I/O, and the counter ignores the search budget."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DECISION_MODULES = (
    "src/engine/decision.py",
    "src/engine/reserve.py",
    "src/engine/hazard_fit.py",
    "src/desk/decide.py",
)

BANNED_MODULES = {
    "openai",
    "httpx",
    "requests",
    "urllib",
    "urllib.request",
    "numpy.random",
    "src.desk.extract",
    "src.sim",
    "src.engine.solver",
    "src.engine.multi_solver",
    "src.engine.coupled",
    "src.engine.bayesian_v2",
    "src.engine.logrolling",
    "src.engine.one_step",
    "src.engine.hazard",
    "src.legacy",
    "src.legacy.solver",
    "src.legacy.coupled",
    "src.legacy.hazard",
    "src.legacy.bayesian_v2",
}

DESK_BANNED = {
    "src.engine.solver",
    "src.engine.multi_solver",
    "src.engine.coupled",
    "src.engine.bayesian_v2",
    "src.engine.logrolling",
    "src.engine.one_step",
    "src.engine.hazard",
    "src.legacy",
    "openai",
}


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
            if node.module == "numpy":
                for alias in node.names:
                    if alias.name == "random" or alias.name.startswith("random."):
                        found.append((node.lineno, "numpy.random"))
    return found


def test_decision_modules_do_not_import_llm_http_or_legacy_engines():
    problems = []
    for rel in DECISION_MODULES:
        for line, name in _imported_modules(ROOT / rel):
            banned = name in BANNED_MODULES or any(name.startswith(item + ".") for item in BANNED_MODULES)
            if banned:
                problems.append(f"{rel}:{line} imports {name}")
    assert problems == []


def test_desk_does_not_import_a_second_engine():
    problems = []
    for path in sorted((ROOT / "src" / "desk").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for line, name in _imported_modules(path):
            if name in DESK_BANNED or any(name.startswith(item + ".") for item in DESK_BANNED):
                problems.append(f"{rel}:{line} imports {name}")
    assert problems == []


def test_counter_is_invariant_to_the_sample_and_grid_budget(tmp_path, monkeypatch):
    from src.desk.service import accept_review, create_deal, ingest_message
    from src.desk.store import DeskStore

    counters = []
    reported = []
    for samples, grid in (("40", "8"), ("4000", "30")):
        monkeypatch.setenv("DESK_N_SAMPLES", samples)
        monkeypatch.setenv("DESK_N_GRID", grid)
        store = DeskStore(str(tmp_path / f"desk-{samples}.db"))
        snapshot = create_deal(
            store,
            product="umbrella",
            quantity=1000,
            target_price=12,
            ceiling_price=20,
            risk_tolerance=0.5,
            seed=11,
            suppliers=[
                {"name": "Ningbo", "address": "ningbo@example.test"},
                {"name": "Wenzhou", "address": "wenzhou@example.test"},
                {"name": "Yiwu", "address": "yiwu@example.test"},
                {"name": "Foshan", "address": "foshan@example.test"},
            ],
        )
        deal_id = snapshot["deal"]["id"]
        opened = ingest_message(
            store,
            deal_id,
            source="paste",
            raw_text="单价 15.60 元",
            supplier_id=snapshot["suppliers"][0]["id"],
        )
        accept_review(
            store, deal_id, opened["event"]["id"], snapshot["suppliers"][0]["id"], chosen_token="15.60"
        )
        fresh = store.get_snapshot(deal_id)
        card = fresh["suppliers"][0]
        counters.append(card["counter"])
        reported.append((fresh["n_samples"], fresh["n_grid"]))
        store.close()
    assert counters[0] is not None
    assert counters[0] == counters[1]
    assert reported[0] != reported[1]


def test_legacy_routes_stay_unmounted_without_the_flag():
    env = os.environ.copy()
    env["TRADEFLOW_LEGACY"] = "0"
    env["TRADEFLOW_MARKET_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    script = (
        "from fastapi.testclient import TestClient\n"
        "from src.app.main import app\n"
        "client = TestClient(app)\n"
        "negotiate = client.post('/negotiate', json={'target_price': 12, 'ceiling_price': 20})\n"
        "sessions = client.post('/api/v1/sessions', json={})\n"
        "webhook = client.post('/webhook/evolus', json={'extracted': {'target_price': 1}, 'offer': 1})\n"
        "assert negotiate.status_code == 404, negotiate.status_code\n"
        "assert sessions.status_code == 404, sessions.status_code\n"
        "assert webhook.status_code == 404, webhook.status_code\n"
        "health = client.get('/health')\n"
        "assert health.status_code == 200, health.status_code\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_llm_numeric_keys_never_change_the_counter():
    """A price key on an extractor row is dropped, and the offer does not move."""
    from src.desk.extract import mentions_from_payload
    from src.engine.decision import choose_offer
    from src.engine.reserve import ReserveState

    state = ReserveState.policy().with_reference(15.6)
    before = choose_offer(ask=15.6, outside=14.0, state=state, risk_cap=0.5)
    payload = mentions_from_payload(
        {
            "offer": 1,
            "mentions": [
                {
                    "class": "own_unit_offer",
                    "evidence_span": "15.60",
                    "speaker": "supplier",
                    "polarity": "affirmative",
                    "is_question": False,
                    "currency": "CNY",
                    "unit": "per_piece",
                    "price": 1,
                    "amount": 0.01,
                    "unit_price": 9,
                },
                {
                    "class": "own_unit_offer",
                    "evidence_span": "1",
                    "speaker": "supplier",
                    "polarity": "affirmative",
                    "is_question": False,
                    "currency": "CNY",
                    "unit": "per_piece",
                    "nested": {"counter": 1, "bid": 2},
                },
            ],
        }
    )
    assert payload["numeric_field_discarded"] is True
    assert payload["rejected_rows"] >= 1
    row = payload["mentions"][0]
    assert "price" not in row and "amount" not in row and "unit_price" not in row
    after = choose_offer(ask=15.6, outside=14.0, state=state, risk_cap=0.5)
    assert after.default_counter == before.default_counter


def test_a_tighter_rival_never_raises_the_counter():
    from src.engine.decision import choose_offer
    from src.engine.reserve import ReserveState

    state = ReserveState.policy().with_reference(15.0)
    loose = choose_offer(ask=15.0, outside=14.0, state=state, risk_cap=0.5)
    tight = choose_offer(ask=15.0, outside=12.0, state=state, risk_cap=0.5)
    assert tight.default_counter <= loose.default_counter
    assert tight.default_counter == 12.0
