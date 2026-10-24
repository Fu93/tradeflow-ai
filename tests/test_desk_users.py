"""Named desk operator keys: the authentication matrix and the chain actor.

Before Phase 2 the desk had one credential — ``TRADEFLOW_API_KEY``, shared
by every buyer — so revoking one person meant rotating the key for everyone
and the audit chain could not say who acted. These tests pin the control
that replaced it:

1. The matrix. The env key works as before (back-compat); a named key works;
   a revoked named key is refused exactly like an unknown one, so revocation
   cannot be probed by comparing error messages; and with no valid
   credential the answer is still 401 — named keys add a way in, they do not
   open one.
2. The actor. A chain entry written by a named-key request records
   ``user:<id>:<label>`` inside its signed payload, so the keyed MAC makes
   the attribution tamper-evident. The shared env key records ``env-key``
   because it names nobody; a write with no request behind it records
   nothing, keeping every pre-existing entry shape unchanged.
3. Storage hygiene. A raw secret appears exactly once — in the issue
   response — and never in the database or the listing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

KEY = "desk-users-matrix-key"

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.delenv("DESK_SMTP_SEND", raising=False)
    monkeypatch.delenv("TRADEFLOW_APPROVAL_PUBKEY", raising=False)
    from src.desk.store import reset_stores

    reset_stores()
    yield tmp_path
    reset_stores()


@pytest.fixture()
def client(desk_env):
    from src.app.main import app

    return TestClient(app, headers={"X-API-Key": KEY})


def _issue(label: str) -> dict:
    """Issue through the CLI, exactly as an operator would."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "manage_users.py"), "issue", "--label", label],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=None,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestIssueAndStorage:
    def test_issue_shows_the_raw_key_once_and_stores_only_its_hash(self, desk_env):
        issued = _issue("Wei (procurement)")
        assert issued["key"].startswith("tf_") and len(issued["key"]) >= 40
        assert issued["label"] == "Wei (procurement)"

        import sqlite3

        conn = sqlite3.connect(desk_env / "desk.db")
        rows = conn.execute(
            "SELECT key_hash, key_prefix, label, revoked_at FROM desk_users"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert issued["key"] not in rows[0][0]
        assert rows[0][1] == issued["key_prefix"]
        assert rows[0][3] is None

    def test_listing_shows_no_secret_material(self, desk_env):
        issued = _issue("Wei (procurement)")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "manage_users.py"), "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
        )
        assert result.returncode == 0
        assert issued["key"] not in result.stdout
        assert issued["key_prefix"] in result.stdout

    def test_revoke_is_idempotent_and_reports_it(self, desk_env):
        issued = _issue("Wei (procurement)")
        for expected_second_run in (0, 1):
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "manage_users.py"),
                 "revoke", "--id", str(issued["id"])],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT),
            )
            assert result.returncode == expected_second_run


class TestAuthenticationMatrix:
    def test_the_env_key_still_works(self, client):
        response = client.post(
            "/api/v1/desk/deals",
            json={
                "product": "umbrella",
                "quantity": 1000,
                "target_price": 12,
                "ceiling_price": 20,
                "risk_tolerance": 0.5,
                "suppliers": SUPPLIERS,
            },
        )
        assert response.status_code == 200, response.text

    def test_a_named_key_works(self, client, desk_env):
        issued = _issue("Wei (procurement)")
        from src.app.main import app

        named = TestClient(app=app, headers={"X-API-Key": issued["key"]})
        response = named.post(
            "/api/v1/desk/deals",
            json={
                "product": "umbrella",
                "quantity": 1000,
                "target_price": 12,
                "ceiling_price": 20,
                "risk_tolerance": 0.5,
                "suppliers": SUPPLIERS,
            },
        )
        assert response.status_code == 200, response.text

    def test_a_revoked_key_is_refused_like_an_unknown_one(self, client, desk_env):
        issued = _issue("Wei (procurement)")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "manage_users.py"),
             "revoke", "--id", str(issued["id"])],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
        headers = {"X-API-Key": issued["key"]}
        revoked = client.post("/api/v1/desk/deals", headers=headers)
        unknown = client.post("/api/v1/desk/deals", headers={"X-API-Key": "tf_" + "0" * 48})
        assert revoked.status_code == 401
        assert unknown.status_code == 401
        assert revoked.json()["detail"] == unknown.json()["detail"]

    def test_no_valid_credential_is_still_401(self, client):
        body = {
            "product": "umbrella",
            "quantity": 1000,
            "target_price": 12,
            "ceiling_price": 20,
            "risk_tolerance": 0.5,
            "suppliers": SUPPLIERS,
        }
        wrong = client.post("/api/v1/desk/deals", headers={"X-API-Key": "wrong-key"}, json=body)
        missing = client.post("/api/v1/desk/deals", headers={"X-API-Key": ""}, json=body)
        assert wrong.status_code == 401
        assert missing.status_code == 401
        assert wrong.json()["detail"] == missing.json()["detail"]

    def test_a_named_key_works_even_without_an_env_key(self, desk_env, monkeypatch):
        """Named keys are an alternative credential source, not an addition to one."""
        issued = _issue("Wei (procurement)")
        monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
        from src.app.main import app

        named = TestClient(app=app, headers={"X-API-Key": issued["key"]})
        body = {
            "product": "umbrella",
            "quantity": 1000,
            "target_price": 12,
            "ceiling_price": 20,
            "risk_tolerance": 0.5,
            "suppliers": SUPPLIERS,
        }
        assert named.post("/api/v1/desk/deals", json=body).status_code == 200
        assert TestClient(app=app).post("/api/v1/desk/deals", json=body).status_code == 401


class TestChainActor:
    def _make_deal(self, client):
        response = client.post(
            "/api/v1/desk/deals",
            json={
                "product": "umbrella",
                "quantity": 1000,
                "target_price": 12,
                "ceiling_price": 20,
                "risk_tolerance": 0.5,
                "suppliers": SUPPLIERS,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_named_key_request_records_its_actor_in_the_chain(self, desk_env):
        issued = _issue("Wei (procurement)")
        from src.app.main import app

        named = TestClient(app=app, headers={"X-API-Key": issued["key"]})
        snapshot = self._make_deal(named)
        chain = named.get(f"/api/v1/desk/deals/{snapshot['deal']['id']}/evidence.json").json()
        actors = [entry["payload"].get("actor") for entry in chain["entries"]]
        assert f"user:{issued['id']}:Wei (procurement)" in actors
        assert chain["ok"] is True

    def test_the_env_key_records_env_key_because_it_names_nobody(self, client):
        snapshot = self._make_deal(client)
        chain = client.get(f"/api/v1/desk/deals/{snapshot['deal']['id']}/evidence.json").json()
        actors = {entry["payload"].get("actor") for entry in chain["entries"]}
        assert actors == {"env-key"}

    def test_a_poller_write_with_no_request_records_no_actor(self, desk_env, monkeypatch):
        """Writes with no request behind them keep the pre-Phase-2 shape."""
        from src.desk.service import create_deal
        from src.desk.store import DeskStore, get_store

        store = get_store()
        snapshot = create_deal(
            store,
            product="umbrella",
            quantity=100,
            target_price=10,
            ceiling_price=12,
            risk_tolerance=0.15,
            seed=1,
            suppliers=SUPPLIERS,
        )
        chain = DeskStore(str(desk_env / "desk.db")).verify_chain(snapshot["deal"]["id"])
        assert chain["ok"] is True
        assert all("actor" not in entry["payload"] for entry in chain["entries"])
