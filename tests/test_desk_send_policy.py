"""Connection policy shown by the desk must reflect runtime, not cached state."""
from contextlib import closing

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.app.security import require_desk_mutation
from src.desk import routes, service
from src.desk.store import DeskStore


def test_cached_deal_reports_current_send_policy_without_repricing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "policy-test-chain")
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "0")
    monkeypatch.delenv("DESK_SMTP_SEND", raising=False)
    with closing(DeskStore(str(tmp_path / "desk.db"))) as store:
        snapshot = service.create_deal(
            store, product="umbrella", quantity=1000, target_price=20, ceiling_price=40,
            risk_tolerance=.5, seed=11, currency="CNY",
            suppliers=[{"name": n, "address": f"{n.lower()}@example.test", "channel": "email"} for n in "ABCD"],
        )
        deal_id = snapshot["deal"]["id"]
        assert snapshot["send_policy"] == {"email_enabled": False, "signed_approval_required": False}
        monkeypatch.setattr(routes, "get_store", lambda: store)
        app = FastAPI()
        app.dependency_overrides[require_desk_mutation] = lambda: "test-authenticated"
        router = APIRouter(prefix="/api/v1")
        routes.register_desk_routes(router)
        app.include_router(router)
        with TestClient(app) as client:
            monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "1")
            current = client.get(f"/api/v1/desk/deals/{deal_id}").json()
            assert current["send_policy"] == {"email_enabled": False, "signed_approval_required": True}
            # Fake transport configuration is inspected only; this test never sends.
            monkeypatch.setenv("DESK_SMTP_SEND", "1")
            for key in ("SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"):
                monkeypatch.setenv(key, "local-policy-test-only")
            current = client.get(f"/api/v1/desk/deals/{deal_id}").json()
            assert current["send_policy"] == {"email_enabled": True, "signed_approval_required": True}
            assert current["suppliers"] == snapshot["suppliers"]
            assert store.get_snapshot(deal_id)["send_policy"]["email_enabled"] is False
            monkeypatch.delenv("EMAIL_PASSWORD")
            current = client.get(f"/api/v1/desk/deals/{deal_id}").json()
            assert current["send_policy"]["email_enabled"] is False
