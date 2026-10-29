"""Problems a judge hit on the live demo (reproduced on d895610, 2026-10-29)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
KEY = "a-test-key-long-enough"
SUPPLIERS = [{"name": n, "address": f"{n}@example.test"} for n in ("a", "b", "c", "d")]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.app.main import app
    from src.desk.store import reset_stores

    reset_stores()
    yield TestClient(app, raise_server_exceptions=False)
    reset_stores()


def _deal(client) -> str:
    body = {"product": "umbrella", "quantity": 1000, "target_price": 24, "ceiling_price": 30, "suppliers": SUPPLIERS}
    response = client.post("/api/v1/desk/deals", json=body, headers={"X-API-Key": KEY})
    assert response.status_code == 200, response.text
    return response.json()["deal"]["id"]


def test_root_opens_the_desk_in_production(client, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_ENV", "production")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/desk"
    # The older dashboard stays reachable.
    assert client.get("/lab").status_code == 200
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_dashboard_reads_the_key_the_desk_stored():
    script = (ROOT / "src/web/static/js/app.js").read_text(encoding="utf-8")
    assert 'tfStoredKey(typeof sessionStorage === "undefined" ? null : sessionStorage) ||' in script
    assert 'tfStoredKey(typeof localStorage === "undefined" ? null : localStorage)' in script


def test_a_reserve_prior_mean_of_one_is_a_422_not_a_500(client):
    deal_id = _deal(client)
    response = client.post(
        f"/api/v1/desk/deals/{deal_id}/assumptions", json={"reserve_prior_mean": 1}, headers={"X-API-Key": KEY}
    )
    assert response.status_code == 422
    assert "reserve_prior_mean" in response.text


def test_an_unexpected_error_answers_json(client, monkeypatch):
    deal_id = _deal(client)

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.desk.routes.get_store", broken)
    response = client.get(f"/api/v1/desk/deals/{deal_id}", headers={"X-API-Key": KEY})
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal error. The request did not finish; reload the deal to see its current state."
    }
    assert "boom" not in response.text


def test_health_names_the_model_in_use(monkeypatch):
    from src.engine import llm_config

    for name in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "VLLM_API_KEY", "EVOLUS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert llm_config.llm_status()["default_model"] is None
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-test-key")
    status = llm_config.llm_status()
    assert status["default_model"] == status["model"]
    assert status["default_model"].startswith("nvidia/")


def test_desk_page_shows_board_errors_and_folds_the_assumptions():
    page = (ROOT / "src/web/templates/desk.html").read_text(encoding="utf-8")
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert '<p id="board-error" class="desk-error" role="alert"></p>' in page
    assert "<summary>Advanced: walk-away assumptions</summary>" in page
    # Paste a quote comes before the cards, the assumptions after the drafts.
    assert page.index('id="paste-form"') < page.index('id="cards"') < page.index('id="assumptions"')
    assert "boardError.textContent = err.message" in script
    assert 'return field && !/^\\d+$/.test(field) ? field + ": " + msg : msg;' in script
    assert 'choose.classList.add("desk-emphasis")' in script


def test_no_amd_wording_on_live_surfaces():
    panel = (ROOT / "extension/sidepanel.js").read_text(encoding="utf-8")
    limiter = (ROOT / "src/app/rate_limit.py").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert '"AMD live"' not in panel and '"amd-live": "Live model"' in panel
    assert "Too many AMD live requests" not in limiter
    assert "VLLM_BASE_URL" not in render and "VLLM_MODEL" not in render
    assert "TRADEFLOW_REQUIRE_SIGNED_APPROVAL" in render


def test_env_example_starts_with_what_the_desk_needs():
    lines = [line for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#")]
    assert lines[0] == "TRADEFLOW_API_KEY=local-demo-key-change-me"
    assert lines[1] == "TRADEFLOW_CHAIN_KEY=local-demo-chain-key-change-me"
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert text.index("# --- Legacy, optional") < text.index("VLLM_API_KEY=")


def test_the_suite_never_sees_a_real_provider_key():
    import os

    for name in ("TRADEFLOW_LLM_API_KEY", "NVIDIA_API_KEY", "TAVILY_API_KEY", "VLLM_API_KEY", "EVOLUS_API_KEY"):
        assert name not in os.environ
    assert os.environ.get("TRADEFLOW_SKIP_DOTENV") == "1"


def test_the_old_submission_is_archived():
    assert not (ROOT / "SUBMISSION.md").exists()
    assert (ROOT / "docs/archive/AMD_SUBMISSION.md").exists()
