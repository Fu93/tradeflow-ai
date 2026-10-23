"""Structural guarantees from the 2026-10-23 refactor round.

These tests assert properties of the *dependency graph*, not of behaviour.
Behaviour is covered elsewhere; what these protect is the shape that makes the
behaviour safe: the send gate cannot reach back into the service layer, the
configuration module is a leaf, and the module that used to hold the gate no
longer defines it.
"""
from __future__ import annotations

import ast
import logging
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from src.app import main as api_main

REPO = pathlib.Path(__file__).resolve().parents[1]


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every ``src.*`` module the file imports, including function-level ones."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    found.add(alias.name)
    return found


# ---------------------------------------------------------------------------
# The send gate is a module of its own, and it cannot reach back
# ---------------------------------------------------------------------------

class TestSendGateIsIsolated:
    def test_verify_does_not_import_the_service_layer(self):
        """The gate must not be able to be bypassed by not loading the service.

        Before the extraction, the gate and the IMAP poller shared a file, so a
        reviewer could not see the gate as a single object and a change in one
        could not be reasoned about independently of the other.
        """
        imports = _imported_modules(REPO / "src" / "desk" / "verify.py")
        assert "src.desk.service" not in imports
        assert "src.desk.ingest" not in imports

    def test_service_no_longer_defines_the_gate(self):
        """The definition moved. If it comes back, the extraction was undone."""
        tree = ast.parse((REPO / "src" / "desk" / "service.py").read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assert "_guard_send" not in defined
        assert "_envelope" not in defined
        assert "DeskBus" not in defined
        assert "publish" not in defined

    def test_the_gate_loads_in_a_fresh_interpreter_without_the_service_layer(self):
        """A caller that loads only the gate can use it.

        This is what makes the isolation real rather than cosmetic. It cannot be
        tested in-process, because the suite's conftest imports ``src.app.main``
        and therefore the service layer anyway; a subprocess is the only way to
        observe a clean import graph.
        """
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "import src.desk.verify as v; assert v._guard_send; assert v._envelope"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        assert result.returncode == 0, result.stderr

    def test_the_compatibility_surface_still_resolves(self):
        """Every existing import path keeps working."""
        from src.desk.service import (  # noqa: F401
            BUS,
            DeskBus,
            DraftStale,
            SendRefused,
            _body_currency,
            _currencies_agree,
            _deal_opened,
            _envelope,
            _guard_send,
            _send_is_new,
            draft_digest,
            publish,
        )

    def test_the_errors_live_below_both(self):
        """errors.py must not import anything from the desk or the app."""
        imports = _imported_modules(REPO / "src" / "desk" / "errors.py")
        assert imports == set()


# ---------------------------------------------------------------------------
# Configuration is a leaf
# ---------------------------------------------------------------------------

class TestConfigIsALeaf:
    def test_heuristics_imports_nothing_from_src(self):
        """It used to import ``src.engine.hazard_fit`` inside ``public_payload``.

        ``hazard_fit`` imports ``heuristics`` at module scope, so the two formed
        a cycle that only worked because the import was inside a function and
        wrapped in a bare ``except``. The view moved to ``src/app`` instead.
        """
        imports = _imported_modules(REPO / "src" / "config" / "heuristics.py")
        assert imports == set(), imports

    def test_the_view_module_lives_above_both(self):
        view = REPO / "src" / "app" / "heuristics_view.py"
        assert view.is_file()
        imports = _imported_modules(view)
        assert "src.config.heuristics" in imports
        assert "src.engine.hazard_fit" in imports

    def test_the_view_still_answers(self):
        payload = api_main.heuristics_payload()
        assert payload["documented"] is True
        assert payload["hazard_status"] in {"ebay_seller_ended", "fit-missing"}
        assert payload["breakdown_trip_threshold"] > 0.0


# ---------------------------------------------------------------------------
# Write rate limiting
# ---------------------------------------------------------------------------

class TestWriteRateLimit:
    def test_mutating_requests_are_limited(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_WRITES_PER_MINUTE", "3")
        from src.app.rate_limit import write_rate_limiter

        write_rate_limiter.reset()
        client = TestClient(api_main.app)
        codes = [
            client.post("/api/v1/tco/evaluate", json={"raw_price": 12.0}).status_code
            for _ in range(5)
        ]
        assert codes[:3] == [200, 200, 200], codes
        assert codes[3:] == [429, 429], codes
        rejected = client.post("/api/v1/tco/evaluate", json={"raw_price": 12.0})
        assert rejected.headers.get("retry-after") == "60"
        assert "limit_scope" in rejected.json()

    def test_reads_are_not_limited(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_WRITES_PER_MINUTE", "1")
        from src.app.rate_limit import write_rate_limiter

        write_rate_limiter.reset()
        client = TestClient(api_main.app)
        client.post("/api/v1/tco/evaluate", json={"raw_price": 12.0})
        for _ in range(4):
            assert client.get("/api/v1/heuristics").status_code == 200

    def test_the_limit_can_be_switched_off(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_WRITES_PER_MINUTE", "1")
        monkeypatch.setenv("TRADEFLOW_RATE_LIMIT", "0")
        from src.app.rate_limit import writes_enabled, write_rate_limiter

        assert writes_enabled() is False
        write_rate_limiter.reset()
        client = TestClient(api_main.app)
        codes = [
            client.post("/api/v1/tco/evaluate", json={"raw_price": 12.0}).status_code
            for _ in range(4)
        ]
        assert 429 not in codes

    def test_an_unset_variable_means_limited_not_unlimited(self, monkeypatch):
        """Fail-safe direction: a missing config must not disable the guard."""
        monkeypatch.delenv("TRADEFLOW_RATE_LIMIT", raising=False)
        from src.app.rate_limit import writes_enabled

        assert writes_enabled() is True

    def test_probes_are_never_counted(self):
        from src.app.rate_limit import is_counted

        assert is_counted("POST", "/api/v1/desk/quotes") is True
        assert is_counted("GET", "/api/v1/desk/quotes") is False
        assert is_counted("HEAD", "/api/v1/desk/quotes") is False
        assert is_counted("POST", "/health") is False
        # Long-lived streams are GETs, so they are excluded by method, not by
        # path; the guard does not need a special case for them.
        assert is_counted("GET", "/api/v1/desk/stream/session") is False
        assert is_counted("POST", "/api/v1/config/telemetry") is True

    def test_the_caller_key_never_stores_a_raw_key(self):
        from src.app.rate_limit import caller_key

        key = caller_key([(b"x-api-key", b"a-real-configured-key-1234")], "10.0.0.1")
        assert key.startswith("key:")
        assert "a-real-configured-key-1234" not in key
        assert caller_key([], "10.0.0.1") == "ip:10.0.0.1"


# ---------------------------------------------------------------------------
# Request identity
# ---------------------------------------------------------------------------

class TestRequestContext:
    def test_a_request_id_is_assigned_and_returned(self):
        client = TestClient(api_main.app)
        response = client.get("/health")
        assert response.headers.get("X-Request-ID")
        assert response.headers.get("X-Response-Time-Ms")

    def test_a_supplied_request_id_is_echoed(self):
        client = TestClient(api_main.app)
        response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers.get("X-Request-ID") == "trace-abc-123"

    def test_a_malicious_request_id_is_rejected_not_reflected(self):
        """A request id ends up in a header; it must not be able to inject one."""
        client = TestClient(api_main.app)
        response = client.get("/health", headers={"X-Request-ID": "evil\r\nX-Injected: 1"})
        value = response.headers.get("X-Request-ID", "")
        assert value != "evil\r\nX-Injected: 1"
        assert "X-Injected" not in response.headers
        assert len(value) == 16  # a generated id replaced the bad input

    def test_the_access_log_carries_the_request_id(self, caplog):
        client = TestClient(api_main.app)
        with caplog.at_level(logging.INFO, logger="tradeflow.access"):
            client.get("/health", headers={"X-Request-ID": "trace-log-1"})
        assert "trace-log-1" in caplog.text
        assert "GET /health" in caplog.text

    def test_the_query_string_is_not_logged(self, caplog):
        """It is the part that can carry a token someone pasted into a URL."""
        client = TestClient(api_main.app)
        with caplog.at_level(logging.INFO, logger="tradeflow.access"):
            client.get("/api/v1/heuristics?secret-token=abc123")
        assert "secret-token" not in caplog.text

    def test_current_request_id_defaults_outside_a_request(self):
        from src.app.observability import current_request_id

        assert current_request_id() == "-"
