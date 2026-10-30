"""A healthy process must not advertise readiness with unusable authentication."""

import pytest
from fastapi.testclient import TestClient

from src.app.main import app
from src.desk.store import reset_stores


@pytest.fixture
def production(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEFLOW_ENV", "production")
    monkeypatch.setenv("TRADEFLOW_SKIP_DOTENV", "1")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "readiness-test-chain")
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "0")
    reset_stores()
    yield TestClient(app)
    reset_stores()


@pytest.mark.parametrize("key", ["", "short"])
def test_production_readiness_rejects_unusable_api_key(production, monkeypatch, key):
    monkeypatch.setenv("TRADEFLOW_API_KEY", key)
    assert production.get("/health").status_code == 200
    response = production.get("/ready")
    assert response.status_code == 503
    assert "api_auth" in response.json()["blocking"]
    assert key not in response.text if key else True


@pytest.mark.parametrize("public", ["not-hex", "aa", "00" * 33, "00" * 32])
def test_readiness_rejects_malformed_pinned_approval_key(production, monkeypatch, public):
    monkeypatch.setenv("TRADEFLOW_API_KEY", "production-readiness-test-key")
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "1")
    monkeypatch.setenv("TRADEFLOW_APPROVAL_PUBKEY", public)
    response = production.get("/ready")
    assert response.status_code == 503
    assert "signed_approval" in response.json()["blocking"]
    assert public not in response.text


def test_valid_production_configuration_is_ready(production, monkeypatch):
    from src.desk.approval_signing import generate_keypair

    _, public = generate_keypair()
    monkeypatch.setenv("TRADEFLOW_API_KEY", "production-readiness-test-key")
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "1")
    monkeypatch.setenv("TRADEFLOW_APPROVAL_PUBKEY", public)
    assert production.get("/ready").status_code == 200
