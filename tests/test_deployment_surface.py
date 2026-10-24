"""The deployment surface: build identity, and the backup/restore drill.

The compose volume keeps the desk database alive across rebuilds, but a
volume is not a backup — `docker compose down -v` destroys it. These tests
pin the two tools that make the deployment operable:

1. ``GET /version`` answers "which build am I talking to" — the app version,
   the schema version, and the deploy-stamped git sha — with no secret
   material, on the ungated probe surface.
2. ``scripts/backup_desk_db.py`` takes an online backup whose chains verify,
   refuses to leave a broken backup behind, and restores it over the live
   path — refusing to clobber an existing database without ``--yes``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

KEY = "deploy-surface-key"

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup_desk_db.py"


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
    from src.desk.store import reset_stores

    reset_stores()
    yield tmp_path
    reset_stores()


def _make_deal_with_chain() -> dict:
    from src.desk.service import create_deal
    from src.desk.store import get_store

    snapshot = create_deal(
        get_store(),
        product="umbrella",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        suppliers=SUPPLIERS,
    )
    return {"deal_id": snapshot["deal"]["id"]}


class TestVersionEndpoint:
    def test_version_reports_build_identity_without_secrets(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_GIT_SHA", "abc1234")
        from src.app.main import app

        body = TestClient(app).get("/version").json()
        assert body["version"]
        assert isinstance(body["schema_version"], int)
        assert body["git_sha"] == "abc1234"
        assert body["python"]
        flattened = str(body).lower()
        for secret_name in ("key", "token", "password", "secret"):
            assert f'"{secret_name}"' not in flattened

    def test_version_defaults_to_unknown_without_a_deploy_stamp(self, monkeypatch):
        monkeypatch.delenv("TRADEFLOW_GIT_SHA", raising=False)
        from src.app.main import app

        assert TestClient(app).get("/version").json()["git_sha"] == "unknown"


class TestBackupDrill:
    def test_backup_verifies_its_own_chains_and_restores(self, desk_env):
        _make_deal_with_chain()
        output = desk_env / "backup.db"
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "backup", "--output", str(output)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        assert run.returncode == 0, run.stderr
        assert "all chains verify" in run.stdout
        assert output.is_file()

        # The restore drill: refuse to clobber a live database without --yes,
        # then restore with it and confirm the chains verify at the new home.
        refusal = subprocess.run(
            [sys.executable, str(SCRIPT), "restore", "--from", str(output),
             "--target", str(desk_env / "desk.db")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        assert refusal.returncode == 1
        assert "--yes" in refusal.stderr

        restored = subprocess.run(
            [sys.executable, str(SCRIPT), "restore", "--from", str(output),
             "--target", str(desk_env / "restored.db"), "--yes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        assert restored.returncode == 0, restored.stderr
        assert "all chains verify" in restored.stdout

    def test_a_missing_source_database_is_a_clean_error(self, desk_env, tmp_path):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "backup", "--source", str(tmp_path / "absent.db"),
             "--output", str(tmp_path / "out.db")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        assert run.returncode == 2
        assert "no desk database" in run.stderr
        assert not (tmp_path / "out.db").exists()

    def test_an_existing_output_file_is_never_overwritten(self, desk_env, tmp_path):
        _make_deal_with_chain()
        occupied = tmp_path / "occupied.db"
        occupied.write_bytes(b"not a database")
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "backup", "--output", str(occupied)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        assert run.returncode == 2
        assert occupied.read_bytes() == b"not a database"
