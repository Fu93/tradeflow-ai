"""[SECURITY REGRESSION SUITE] TradeFlow API surface hardening.

These tests are deliberately written to FAIL against the pre-fix code and PASS
after it. Each test names the audit id it closes (DEEP_DEFECT_AUDIT.md and the
task-1 / task-9 defect lists).

Coverage map:
  1.1  auth coverage on every /api/v1 route (router-level dependency)
  1.2  CORS never combines "*" with credentials; .env reaches os.environ
  1.3  DoS bounds on MultiAuctionRunRequest / Spec
  1.4  /webhook/evolus + /webhook/mobile-command shared-secret
  1.1/6.1 telemetry config endpoint auth + no credential preview + prod refusal
  5.1  human-in-the-loop default (copilot) and no unattended DEAL_CLOSED
  5.3  /command walk_away actually transitions, no %-false success
  5.4  supervisor low-price override lower bound
  9.A  Infinity poisoning cannot brick GET /api/v1/sessions
  9.B  path traversal via session_id is refused
  9.C  CAS is enforced even when expected_version is omitted
  9.D  out-of-range numerics are 422, not bare 500
  A/B  guardrail violations never produce HTTP 500
  SSE  session-read failures are surfaced, not swallowed
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from src.app import main as api_main  # noqa: E402
from src.session.schema import SessionSpec  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_manager() -> None:
    """Point the global SessionManager (and therefore every route) at a private
    temp store, so these tests never touch the repo's real data/ directory.

    Returns the previous (db_path, store_dir) so the caller can restore a LIVE
    location before deleting its temp dir — otherwise a later test class would
    inherit a manager whose store_dir no longer exists.
    """
    tmp = tempfile.mkdtemp(prefix="tf_api_security_")
    prev = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
    api_main.session_mgr.__init__(
        db_path=os.path.join(tmp, "sessions.db"),
        store_dir=os.path.join(tmp, "sessions_json"),
    )
    return tmp, prev


class _IsolatedCase(unittest.TestCase):
    """Base class: isolated session store + a client, and env restored after."""

    ENV_KEYS = (
        "TRADEFLOW_ENV", "TRADEFLOW_API_KEY", "TRADEFLOW_WEBHOOK_TOKEN",
        # [REGRESSION] telemetry 設定端點會寫入 `.env`。若不把路徑導向暫存檔，
        # 測試會改寫 repo 的真實 .env —— 本次修復期間實際發生過
        # （TELEGRAM_CHAT_ID=100200300 被寫進真實 .env）。
        "TRADEFLOW_ENV_PATH",
    )

    def setUp(self):
        self._orig_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp, self._prev_paths = _fresh_manager()
        # 讓 telemetry 寫入導向本次測試的暫存 .env，而非 repo 的真實檔案。
        os.environ["TRADEFLOW_ENV_PATH"] = os.path.join(self._tmp, "env_test")
        self.c = TestClient(api_main.app)

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Restore the global manager to a live location BEFORE deleting the temp dir.
        api_main.session_mgr.__init__(
            db_path=self._prev_paths[0], store_dir=self._prev_paths[1]
        )
        import shutil as _shutil
        _shutil.rmtree(self._tmp, ignore_errors=True)


def _valid_spec(session_id: str = "sec_sess_001", **over) -> dict:
    spec = {
        "session_id": session_id,
        "product_name": "Carbon Auto Umbrella",
        "target_price": 12.0,
        "ceiling_price": 16.2,
        "moq": 5000,
            "expected_markup": 0.0,
        }
    spec.update(over)
    return spec


# ---------------------------------------------------------------------------
# 1.1 — auth coverage
# ---------------------------------------------------------------------------

class TestApiV1AuthCoverage(_IsolatedCase):
    """[1.1] Every /api/v1 route must require X-API-Key (router-level)."""

    #: Deliberate, documented exception: a browser EventSource cannot set headers.
    OPEN_PATHS = {"/api/v1/stream/session"}

    def _api_v1_routes(self):
        return [r for r in api_main._iter_api_routes() if r.path.startswith("/api/v1")]

    def test_every_api_v1_route_declares_the_auth_dependency(self):
        missing = []
        for route in self._api_v1_routes():
            if route.path in self.OPEN_PATHS:
                continue
            gated = any(
                getattr(getattr(d, "dependency", None), "__name__", None) == "require_api_key"
                for d in (route.dependencies or [])
            )
            if not gated:
                missing.append(f"{sorted(route.methods)} {route.path}")
        self.assertEqual(
            missing, [],
            "these /api/v1 routes have no require_api_key dependency: " + ", ".join(missing),
        )

    def test_header_name_is_still_x_api_key(self):
        """w5-xss-remediation wires clients to this exact header; freeze it."""
        names = [
            getattr(d.dependency, "__name__", "")
            for d in (api_main.api_v1.dependencies or [])
        ]
        self.assertIn("require_api_key", names)
        # The documented contract is the literal header string; assert it at the
        # OpenAPI layer so a rename cannot slip through silently.
        schema = api_main.app.openapi()
        security_schemes = schema.get("components", {}).get("securitySchemes", {})
        self.assertTrue(
            any(v.get("name") == "X-API-Key" for v in security_schemes.values() or []),
            f"X-API-Key scheme missing from OpenAPI: {security_schemes}",
        )
        header = api_main.webhook_token_header
        # APIKeyHeader exposes the header spelling via its internal security model.
        self.assertEqual(header.model.name, "X-Webhook-Token")
        self.assertIsNotNone(header)

    def test_unauthenticated_requests_are_rejected_when_key_configured(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        # Probes are chosen to be side-effect-free or explicitly written to be safe:
        checks = [
            ("GET", "/api/v1/sessions", None),
            ("GET", "/api/v1/sessions/probe-sec-01", None),
            ("GET", "/api/v1/config/telemetry", None),
            ("POST", "/api/v1/tco/evaluate", {"raw_price": 12.0}),
            ("POST", "/api/v1/bayesian/update", {"observed_offer": 1.25}),
            ("POST", "/api/v1/multi-auction/run", {"n_samples": 100}),
            ("POST", "/api/v1/telemetry/test-push", {"channel": "none"}),
            ("POST", "/api/v1/classify/semantic", {"text": "hello", "enable_stage2": False}),
            ("POST", "/api/v1/sessions", _valid_spec("probe-sec-create")),
            ("POST", "/api/v1/sessions/probe-sec-02/round", {"supplier_offer": 13.0}),
            ("POST", "/api/v1/sessions/probe-sec-02/command", {"command": "2"}),
            ("POST", "/api/v1/sessions/probe-sec-02/approve", {"action": "approve"}),
        ]
        for method, path, payload in checks:
            with self.subTest(path=path):
                r = self.c.request(method, path, json=payload)
                self.assertEqual(
                    r.status_code, 401,
                    f"{method} {path} must 401 without X-API-Key, got {r.status_code}: {r.text[:200]}",
                )
                self.assertNotIn(
                    "probe-sec-create", r.text,
                    f"{method} {path} must not have processed the request body before auth",
                )

    def test_sandbox_without_key_stays_open_for_the_demo(self):
        """Fail-open in sandbox is intentional (hackathon demo + extension)."""
        r = self.c.get("/api/v1/sessions")
        self.assertEqual(r.status_code, 200)

    def test_health_root_and_static_stay_open(self):
        self.assertEqual(self.c.get("/health").status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_api_key_header_alone_unlocks_the_surface(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        r = self.c.get("/api/v1/sessions", headers={"X-API-Key": "a-real-configured-key-1234"})
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 1.4 — webhook shared secret
# ---------------------------------------------------------------------------

class TestWebhookAuthentication(_IsolatedCase):
    """[1.4] Supervisor approval channels must not be forgeable."""

    def test_mobile_command_requires_token_when_configured(self):
        os.environ["TRADEFLOW_WEBHOOK_TOKEN"] = "webhook-secret-abc"
        r = self.c.post("/webhook/mobile-command",
                        json={"session_id": "sess_x", "command": "1"})
        self.assertEqual(r.status_code, 401)
        r_ok = self.c.post(
            "/webhook/mobile-command",
            json={"session_id": "sess_x", "command": "1"},
            headers={"X-Webhook-Token": "webhook-secret-abc"},
        )
        self.assertNotEqual(r_ok.status_code, 401)

    def test_evolus_webhook_requires_token_when_configured(self):
        os.environ["TRADEFLOW_WEBHOOK_TOKEN"] = "webhook-secret-abc"
        r = self.c.post("/webhook/evolus", json={"extracted": {"target_price": 12.0}})
        self.assertEqual(r.status_code, 401)

    def test_production_is_fail_closed_without_a_configured_token(self):
        """Production MUST NOT fall back to open access when the token is unset."""
        os.environ["TRADEFLOW_ENV"] = "production"
        os.environ.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
        for path in ("/webhook/evolus", "/webhook/mobile-command"):
            with self.subTest(path=path):
                r = self.c.post(path, json={"command": "1", "extracted": {}})
                self.assertEqual(
                    r.status_code, 401,
                    f"{path} must fail closed in production with no token, got {r.status_code}",
                )

    def test_wrong_token_is_rejected_in_production(self):
        os.environ["TRADEFLOW_ENV"] = "production"
        os.environ["TRADEFLOW_WEBHOOK_TOKEN"] = "correct-token-value"
        r = self.c.post(
            "/webhook/mobile-command",
            json={"session_id": "s", "command": "1"},
            headers={"X-Webhook-Token": "wrong-token-value"},
        )
        self.assertEqual(r.status_code, 401)

    def test_sandbox_without_token_remains_fail_open_for_the_demo(self):
        """The extension's Concede/Hold/Abort buttons must keep working in a demo."""
        os.environ.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
        # No session exists -> authenticated, so the response is about the command,
        # not about auth (must not be 401).
        r = self.c.post("/webhook/mobile-command",
                        json={"session_id": "nope", "command": "2"})
        self.assertNotEqual(r.status_code, 401)


# ---------------------------------------------------------------------------
# 1.1 + 6.1 — telemetry config endpoint
# ---------------------------------------------------------------------------

class TestTelemetryConfigEndpoint(_IsolatedCase):
    """[1.1/6.1] Authenticated, allowlisted, no credential preview, prod refusal."""

    def test_unauthenticated_is_rejected_when_key_configured(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        self.assertEqual(self.c.get("/api/v1/config/telemetry").status_code, 401)

    def test_get_never_leaks_a_credential_preview(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "1234567890:AAF-super-secret-token"
        r = self.c.get("/api/v1/config/telemetry")
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertNotIn("preview", body, "credential preview must be removed")
        # The first 4 characters of a real token must not appear anywhere.
        self.assertNotIn("1234", body, "credential prefix leaked in response")
        for field, info in r.json()["config"].items():
            with self.subTest(field=field):
                self.assertEqual(set(info.keys()), {"set"})

    def test_production_refuses_to_write_env(self):
        os.environ["TRADEFLOW_ENV"] = "production"
        # Production also fail-closes on the API key itself, so present a valid one
        # (otherwise the request is rejected by require_api_key at 500 before it
        # ever reaches the telemetry handler).
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        h = {"X-API-Key": "a-real-configured-key-1234"}
        before = os.environ.get("TELEGRAM_BOT_TOKEN")
        r = self.c.post(
            "/api/v1/config/telemetry",
            json={"TELEGRAM_BOT_TOKEN": "PWNED"},
            headers=h,
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["status"], "refused")
        self.assertFalse(r.json()["hot_reload"])
        self.assertEqual(os.environ.get("TELEGRAM_BOT_TOKEN"), before)

    def test_sandbox_still_supports_hot_reload(self):
        os.environ.pop("TRADEFLOW_ENV", None)
        r = self.c.post("/api/v1/config/telemetry", json={"TELEGRAM_CHAT_ID": "100200300"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "saved")
        self.assertTrue(r.json()["hot_reload"])
        self.assertIn("TELEGRAM_CHAT_ID", r.json()["fields_updated"])

    def test_unknown_fields_are_not_written(self):
        r = self.c.post("/api/v1/config/telemetry", json={"EVOLUS_API_KEY": "should-not-persist"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["fields_updated"], [])


# ---------------------------------------------------------------------------
# 1.2 — CORS
# ---------------------------------------------------------------------------

class TestCorsPolicy(_IsolatedCase):
    """[1.2] Never combine a wildcard origin with credentials."""

    def test_wildcard_is_not_combined_with_credentials(self):
        for mw in api_main.app.user_middleware:
            if mw.cls.__name__ != "CORSMiddleware":
                continue
            opts = mw.kwargs
            self.assertNotIn(
                "*", opts.get("allow_origins") or [],
                "allow_origins must not contain a bare wildcard",
            )
            if opts.get("allow_credentials"):
                self.assertTrue(
                    opts.get("allow_origin_regex"),
                    "credentials require an explicit origin allowlist (regex)",
                )
            return
        self.fail("CORSMiddleware not installed")

    def test_localhost_origin_is_allowed(self):
        r = self.c.get("/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(
            r.headers.get("access-control-allow-origin"), "http://localhost:5173"
        )

    def test_extension_origin_is_allowed(self):
        r = self.c.get("/health", headers={"Origin": "chrome-extension://abcdefghijklmnop"})
        self.assertEqual(
            r.headers.get("access-control-allow-origin"),
            "chrome-extension://abcdefghijklmnop",
        )

    def test_arbitrary_origin_is_not_echoed(self):
        r = self.c.get("/health", headers={"Origin": "https://evil.example.com"})
        self.assertNotEqual(r.headers.get("access-control-allow-origin"), "https://evil.example.com")


# ---------------------------------------------------------------------------
# 1.3 — DoS bounds
# ---------------------------------------------------------------------------

class TestInputBounds(_IsolatedCase):
    """[1.3] Unauthenticated CPU/RAM DoS must be impossible."""

    def test_multi_auction_rejects_oversized_n_samples(self):
        r = self.c.post("/api/v1/multi-auction/run", json={"n_samples": 1_000_000})
        self.assertEqual(r.status_code, 422, "n_samples must be bounded")

    def test_multi_auction_rejects_oversized_n_rounds(self):
        r = self.c.post("/api/v1/multi-auction/run", json={"n_rounds": 10_000})
        self.assertEqual(r.status_code, 422)

    def test_multi_auction_rejects_oversized_rounds_quotes(self):
        r = self.c.post(
            "/api/v1/multi-auction/run",
            json={"rounds_quotes": [[13.0, 14.0]] * 500},
        )
        self.assertEqual(r.status_code, 422, "rounds_quotes rounds must be bounded")

    def test_multi_auction_rejects_non_finite_quote(self):
        # stdlib json cannot emit Infinity, so send a raw body.
        r = self.c.post(
            "/api/v1/multi-auction/run",
            content='{"rounds_quotes": [[Infinity, 14.0]]}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 422)

    def test_negotiate_bounds_n_samples(self):
        r = self.c.post("/negotiate", json={"target_price": 12.0, "n_samples": 5_000_000})
        self.assertEqual(r.status_code, 422)

    def test_legacy_spec_rejects_non_finite_lambda(self):
        r = self.c.post(
            "/negotiate",
            content='{"target_price": 12.0, "n_samples": 100, "lambda_break": Infinity}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 422)

    def test_valid_bounded_request_still_works(self):
        r = self.c.post("/api/v1/multi-auction/run", json={
            "demo": True, "n_samples": 200, "n_rounds": 1,
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["simulated"])


# ---------------------------------------------------------------------------
# 5.1 — human-in-the-loop default
# ---------------------------------------------------------------------------

_AUTOPILOT_DEAL_TEXT = "行吧服了你了，就按你說的 12.00，發合同過來吧。"


class TestHumanInTheLoop(_IsolatedCase):
    """[5.1] The default path must not declare a deal with zero human approval."""

    def test_default_mode_is_copilot(self):
        from src.app.main import RoundActionRequest
        self.assertEqual(RoundActionRequest().mode.value, "copilot")
        self.assertFalse(RoundActionRequest().is_autopilot())

    def test_null_mode_falls_back_to_copilot(self):
        from src.app.main import RoundActionRequest
        req = RoundActionRequest.model_validate({"mode": None})
        self.assertFalse(req.is_autopilot(), "null mode must never select autopilot")

    def test_unknown_mode_falls_back_to_copilot(self):
        from src.app.main import RoundActionRequest
        req = RoundActionRequest.model_validate({"mode": "SKYNET"})
        self.assertFalse(req.is_autopilot(), "unknown mode must never select autopilot")

    def test_explicit_autopilot_is_still_accepted(self):
        from src.app.main import RoundActionRequest
        self.assertTrue(RoundActionRequest.model_validate({"mode": "autopilot"}).is_autopilot())

    def test_default_round_does_not_reach_deal_closed_without_approval(self):
        """The exact audit 5.1 reproduction, now with the default (no mode sent)."""
        sid = "sec-hitl-default"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid))
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={"opponent_raw_text": _AUTOPILOT_DEAL_TEXT, "opponent_offer_price": 12.00},
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r.json()["detail"])
        stored = self.c.get(f"/api/v1/sessions/{sid}").json()
        self.assertEqual(stored["history"], [])
        self.assertNotEqual(stored["state"], "DEAL_CLOSED")

    def test_autopilot_deal_candidate_still_requires_human_approval(self):
        sid = "sec-hitl-autopilot"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid))
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={
                "opponent_raw_text": _AUTOPILOT_DEAL_TEXT,
                "opponent_offer_price": 12.00,
                "mode": "autopilot",
            },
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r.json()["detail"])
        stored = self.c.get(f"/api/v1/sessions/{sid}").json()
        self.assertNotEqual(stored["state"], "DEAL_CLOSED")
        self.assertEqual(stored["history"], [])

    def test_deal_closed_requires_approve_endpoint(self):
        sid = "sec-hitl-approve"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid))
        posted = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={"opponent_raw_text": "¥14.00 最低", "opponent_offer_price": 14.0},
        )
        self.assertEqual(posted.status_code, 422, posted.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", posted.json()["detail"])
        r = self.c.post(f"/api/v1/sessions/{sid}/approve", json={"action": "approve"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("not waiting for a decision", r.json()["detail"])
        self.assertEqual(self.c.get(f"/api/v1/sessions/{sid}").json()["history"], [])


# ---------------------------------------------------------------------------
# 5.3 — /command walk_away
# ---------------------------------------------------------------------------

class TestWalkAwayCommand(_IsolatedCase):
    """[5.3] command="3" must actually terminate, not report a phantom success."""

    def test_walk_away_transitions_from_autopilot_cruising(self):
        sid = "sec-walkaway-01"
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        h = {"X-API-Key": "a-real-configured-key-1234"}
        self.c.post("/api/v1/sessions", json=_valid_spec(sid, moq=5000), headers=h)

        r = self.c.post(f"/api/v1/sessions/{sid}/command", json={"command": "3"}, headers=h)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["action"], "walk_away")

        after = self.c.get(f"/api/v1/sessions/{sid}", headers=h).json()
        self.assertEqual(
            after["state"], "WALK_AWAY",
            "walk_away must move the session out of its cruising state",
        )

    def test_walk_away_from_idle_transitions(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        h = {"X-API-Key": "a-real-configured-key-1234"}
        sid = "sec-walkaway-idle"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid), headers=h)
        r = self.c.post(f"/api/v1/sessions/{sid}/command", json={"command": "3"}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["state_transition_applied"])
        self.assertEqual(self.c.get(f"/api/v1/sessions/{sid}", headers=h).json()["state"], "WALK_AWAY")

    def test_walk_away_on_absent_session_is_not_reported_as_success(self):
        r = self.c.post("/webhook/mobile-command",
                        json={"session_id": "sec-no-such-session", "command": "3"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "error")


# ---------------------------------------------------------------------------
# 5.4 — low-price override
# ---------------------------------------------------------------------------

class TestSupervisorPriceFloor(_IsolatedCase):
    """[5.4] A supervisor command must not silently authorize an arbitrarily low price."""

    def test_absurdly_low_override_is_refused(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        h = {"X-API-Key": "a-real-configured-key-1234"}
        sid = "sec-lowprice-01"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid, target_price=12.0, ceiling_price=16.2), headers=h)
        # 0.50 is 4% of the target price -> must be refused, not authorized.
        r = self.c.post(f"/api/v1/sessions/{sid}/command", json={"command": "0.50"}, headers=h)
        self.assertEqual(
            r.status_code, 422,
            "an arbitrarily low supervisor override must be rejected, not silently applied",
        )
        self.assertIn("lower bound", r.json()["detail"])

    def test_reasonable_override_within_bounds_is_accepted(self):
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"
        h = {"X-API-Key": "a-real-configured-key-1234"}
        sid = "sec-lowprice-ok"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid, target_price=12.0, ceiling_price=16.2), headers=h)
        r = self.c.post(f"/api/v1/sessions/{sid}/command", json={"command": "11.00"}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["custom_price"], 11.0)


# ---------------------------------------------------------------------------
# 9.A — Infinity poisoning
# ---------------------------------------------------------------------------

class TestInfinityPoisoning(_IsolatedCase):
    """[9.A] A non-finite numeric must not persist and brick the sessions API."""

    def _raw_create(self, raw_json: str):
        return self.c.post(
            "/api/v1/sessions",
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )

    def test_infinity_target_price_is_rejected(self):
        # stdlib json.dumps refuses to emit Infinity, which is exactly why the
        # existing suite never caught this. Emit a RAW body instead.
        raw = (
            '{"session_id": "sec-inf-01", "product_name": "x", '
            '"target_price": Infinity, "ceiling_price": 16.2, "moq": 5000}'
        )
        r = self._raw_create(raw)
        self.assertEqual(r.status_code, 422, "Infinity must be rejected at the boundary")

    def test_nan_ceiling_price_is_rejected(self):
        raw = (
            '{"session_id": "sec-nan-01", "product_name": "x", '
            '"target_price": 12.0, "ceiling_price": NaN, "moq": 5000}'
        )
        self.assertEqual(self._raw_create(raw).status_code, 422)

    def test_sessions_list_still_reads_after_a_poison_attempt(self):
        """The persistent-brick regression: LIST/GET must never 500."""
        self._raw_create(
            '{"session_id": "sec-inf-02", "product_name": "x", '
            '"target_price": Infinity, "ceiling_price": 16.2, "moq": 5000}'
        )
        self.c.post("/api/v1/sessions", json=_valid_spec("sec-inf-03"))
        self.assertEqual(self.c.get("/api/v1/sessions").status_code, 200)
        self.assertEqual(self.c.get("/api/v1/sessions/sec-inf-03").status_code, 200)

    def test_get_survives_a_manually_poisoned_row(self):
        """Belt and braces: even if a bad row somehow exists, reads must not 500."""
        import sqlite3
        self.c.post("/api/v1/sessions", json=_valid_spec("sec-inf-manual"))
        con = sqlite3.connect(api_main.session_mgr.db_path)
        con.execute(
            "UPDATE sessions SET spec_json = ? WHERE session_id = ?",
            ('{"session_id":"sec-inf-manual","product_name":"x","target_price":null,'
             '"ceiling_price":16.2,"moq":5000}', "sec-inf-manual"),
        )
        con.commit()
        con.close()
        r = self.c.get("/api/v1/sessions")
        self.assertEqual(r.status_code, 200, "a single poisoned row must not break LIST")


# ---------------------------------------------------------------------------
# 9.B — path traversal
# ---------------------------------------------------------------------------

class TestSessionIdPathTraversal(_IsolatedCase):
    """[9.B] session_id must never escape store_dir or overwrite arbitrary files."""

    def test_traversal_session_id_is_rejected_at_creation(self):
        r = self.c.post("/api/v1/sessions", json=_valid_spec("../../models/vocab"))
        self.assertEqual(r.status_code, 422)

    def test_drive_prefixed_session_id_is_rejected(self):
        r = self.c.post("/api/v1/sessions", json=_valid_spec("C:/Users/nn201/pwn"))
        self.assertEqual(r.status_code, 422)

    def test_traversal_in_url_path_is_rejected(self):
        # Backslash traversal: Starlette round-trips %5C and the whitelist rejects it.
        r = self.c.get("/api/v1/sessions/..%5C..%5Cmodels%5Cvocab")
        self.assertEqual(r.status_code, 422)

    def test_manager_refuses_to_write_outside_store_dir(self):
        from src.session.manager import SessionManager
        mgr = api_main.session_mgr
        spec = SessionSpec(
            session_id="legit_id_ok", product_name="x", target_price=12.0, ceiling_price=16.0,
            expected_markup=0.0,
        )
        self.assertRaises(ValueError, mgr._snapshot_path, "../../escape")
        # And a legit id resolves inside store_dir.
        p = mgr._snapshot_path(spec.session_id)
        self.assertTrue(os.path.abspath(p).startswith(os.path.abspath(mgr.store_dir)))

    def test_nonfinite_spec_values_are_rejected_by_the_schema(self):
        with self.assertRaises(Exception):
            SessionSpec(
                session_id="sec-spec-inf", product_name="x",
                target_price=float("inf"), ceiling_price=16.0,
            expected_markup=0.0,
        )


# ---------------------------------------------------------------------------
# 9.C — CAS bypass when expected_version is omitted
# ---------------------------------------------------------------------------

class TestCasIsAlwaysEnforced(_IsolatedCase):
    """[9.C] Omitting expected_version must not bypass the optimistic lock."""

    def test_stale_write_without_expected_version_conflicts(self):
        from src.session.schema import NegotiationTurn
        mgr = api_main.session_mgr
        spec = SessionSpec(
            session_id="sec-cas-01", product_name="x", target_price=20.0, ceiling_price=25.0,
            expected_markup=0.0,
        )
        created = mgr.create_session(spec)

        # Writer A advances the record twice.
        mgr.append_turn("sec-cas-01", NegotiationTurn(round_index=1, sender="opponent", raw_text="a"))
        sess = mgr.append_turn("sec-cas-01", NegotiationTurn(round_index=2, sender="opponent", raw_text="b"))
        self.assertGreater(sess.current_round, 1)

        # A stale turn (round 1) written with no expected_version must NOT silently
        # downgrade current_round. It may either be rejected (CAS conflict) or be
        # clamped monotonically — never regress.
        try:
            after = mgr.append_turn(
                "sec-cas-01",
                NegotiationTurn(round_index=1, sender="buyer_agent", raw_text="stale"),
            )
            self.assertGreaterEqual(
                after.current_round, 2,
                "a stale writer must not downgrade current_round",
            )
        except Exception as exc:
            self.assertIn("CAS", str(exc))

    def test_two_real_writers_on_same_session_exactly_one_wins(self):
        """The existing stress test gives every thread a distinct id, so it cannot
        detect this. Here both writers genuinely contend on ONE session id."""
        import concurrent.futures
        from src.session.schema import NegotiationTurn
        from src.session.manager import OptimisticLockConflictError

        mgr = api_main.session_mgr
        mgr.create_session(SessionSpec(
            session_id="sec-cas-race", product_name="race", target_price=20.0, ceiling_price=25.0,
            expected_markup=0.0,
        ))
        base = mgr.get_session("sec-cas-race").version

        def writer(i):
            try:
                mgr.append_turn(
                    "sec-cas-race",
                    NegotiationTurn(round_index=1, sender="opponent", raw_text=f"w{i}"),
                    expected_version=base,
                )
                return "ok"
            except OptimisticLockConflictError:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            outcomes = list(ex.map(writer, [1, 2]))

        self.assertEqual(
            outcomes.count("ok"), 1,
            f"exactly one writer must win the CAS race, got {outcomes}",
        )
        self.assertEqual(outcomes.count("conflict"), 1)

    def test_true_optimistic_lock_semantics_on_transition(self):
        """A correctly-stale caller must get a conflict, and a correct caller must
        not be falsely rejected."""
        from src.session.schema import AutopilotState
        from src.session.manager import OptimisticLockConflictError
        mgr = api_main.session_mgr
        mgr.create_session(SessionSpec(
            session_id="sec-cas-02", product_name="x", target_price=20.0, ceiling_price=25.0,
            expected_markup=0.0,
        ))
        s = mgr.transition_state(
            "sec-cas-02", AutopilotState.COPILOT_MONITORING, expected_version=1
        )
        self.assertEqual(s.version, 2)
        # Stale caller (version 1) must be rejected.
        with self.assertRaises(OptimisticLockConflictError):
            mgr.transition_state(
                "sec-cas-02", AutopilotState.AWAITING_HUMAN_DECISION, expected_version=1
            )
        # Correct caller (version 2) must succeed.
        s2 = mgr.transition_state(
            "sec-cas-02", AutopilotState.AWAITING_HUMAN_DECISION, expected_version=2
        )
        self.assertEqual(s2.version, 3)


class TestRequestCasPlumbing(_IsolatedCase):
    """[9.C] /round must pass the caller's expected_version through to the store."""

    def test_stale_expected_version_on_round_is_409(self):
        sid = "sec-cas-round"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid))
        for _ in range(2):
            r = self.c.post(f"/api/v1/sessions/{sid}/round",
                            json={"supplier_offer": 14.0, "mode": "copilot"})
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("unconfirmed numbers used in decisions: 1", r.json()["detail"])
        current = self.c.get(f"/api/v1/sessions/{sid}").json()["version"]
        matched = self.c.post(f"/api/v1/sessions/{sid}/round",
                              json={"supplier_offer": 13.9, "expected_version": current, "mode": "copilot"})
        self.assertEqual(matched.status_code, 422, matched.text)
        r = self.c.post(f"/api/v1/sessions/{sid}/round",
                        json={"supplier_offer": 13.9, "expected_version": current + 1, "mode": "copilot"})
        self.assertEqual(r.status_code, 409)


# ---------------------------------------------------------------------------
# 9.D — numeric 500s become 422
# ---------------------------------------------------------------------------

class TestNumericValidation(_IsolatedCase):
    """[9.D] Out-of-range numerics must be 422, never a bare undiagnosable 500."""

    def test_zero_prior_std_is_422(self):
        r = self.c.post("/api/v1/bayesian/update", json={"observed_offer": 1.25, "prior_std": 0.0})
        self.assertEqual(r.status_code, 422, "prior_std=0 previously caused a math-domain 500")

    def test_negative_prior_std_is_422(self):
        r = self.c.post("/api/v1/bayesian/update", json={"observed_offer": 1.25, "prior_std": -0.5})
        self.assertEqual(r.status_code, 422)

    def test_zero_raw_price_is_422(self):
        r = self.c.post("/api/v1/tco/evaluate", json={"raw_price": 0.0})
        self.assertEqual(r.status_code, 422)

    def test_infinity_observed_offer_is_422(self):
        r = self.c.post(
            "/api/v1/bayesian/update",
            content='{"observed_offer": Infinity}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 422)

    def test_valid_bayesian_update_still_works(self):
        r = self.c.post("/api/v1/bayesian/update", json={
            "prior_mean": 1.08, "prior_std": 0.10, "observed_offer": 1.25,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


# ---------------------------------------------------------------------------
# Guardrails must defer, not 500
# ---------------------------------------------------------------------------

class TestGuardrailNever500s(_IsolatedCase):
    """[A] A violated safety invariant must be a deterministic 422/block, never 500."""

    def test_round_with_out_of_range_deposit_does_not_500(self):
        sid = "sec-guard-deposit"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid, target_price=12.0, ceiling_price=16.2))
        r = self.c.post(
            f"/api/v1/sessions/{sid}/round",
            json={
                "opponent_raw_text": "就按你說的吧",
                "opponent_offer_price": 12.0,
                "deposit_pct": 0.85,
            },
        )
        self.assertNotEqual(r.status_code, 500, "guardrail violation must not crash the route")
        self.assertIn(r.status_code, (200, 422))
        if r.status_code == 200:
            # Deferred, not bypassed: the session must be tripped for a human.
            self.assertTrue(r.json()["circuit_tripped"])
            self.assertTrue(r.json()["pending_human_approval"])
            self.assertIsNone(r.json()["reply_text"])

    def test_mobile_command_with_out_of_range_deposit_does_not_500(self):
        sid = "sec-guard-cmd"
        self.c.post("/api/v1/sessions", json=_valid_spec(sid))
        r = self.c.post(
            "/webhook/mobile-command",
            json={"session_id": sid, "command": "deposit 95%"},
        )
        self.assertNotEqual(r.status_code, 500)


# ---------------------------------------------------------------------------
# 9.F — 500 bodies do not leak internals
# ---------------------------------------------------------------------------

class TestNoInternalLeakInErrors(_IsolatedCase):
    """[9.F] detail=str(e) leaked absolute paths and credential values."""

    def test_engine_failure_detail_is_generic(self):
        original = api_main.one_step_counter

        def boom(*a, **kw):
            raise RuntimeError(r"internal failure at C:\secret\path with api_key=SHOULDNOTLEAK")

        api_main.one_step_counter = boom
        try:
            r = self.c.post("/negotiate", json={
                "target_price": 12.0, "ceiling_price": 16.2, "observed_offer": 1.25,
                "n_samples": 100, "expected_markup": 0.0,
            })
        finally:
            api_main.one_step_counter = original
        self.assertEqual(r.status_code, 422)
        body = r.text
        self.assertNotIn("SHOULDNOTLEAK", body)
        self.assertNotIn(r"C:\secret\path", body)
        self.assertIn("unconfirmed numbers used in decisions: 1", body)


# ---------------------------------------------------------------------------
# SSE error surfacing
# ---------------------------------------------------------------------------

class TestSseErrorSurfacing(_IsolatedCase):
    """[SSE] A session-read failure must be visible, not swallowed.

    [P1 AUDIT FIX — 2026-10-17] Starlette 1.7 的 TestClient 會把整個 streaming
    response 緩衝到串流結束才交給呼叫端（testclient.handle_request 先把 app 跑到
    完成，再包成 ByteStream）。SSE 串流的生命週期上限原本硬寫 300 秒，因此本類別
    每個測試都會靜默等待 ~300 秒（單獨執行看起來像當機，整套測試因此多花約
    10 分鐘）。現在把上限改成可經 TRADEFLOW_SSE_MAX_SECONDS 設定，並在此類別
    縮到 1 秒：斷言完全不變（仍是串流的第一個 data 幀），但執行時間降到約 1 秒。
    """

    def setUp(self):
        super().setUp()
        self._orig_sse_cap = os.environ.get("TRADEFLOW_SSE_MAX_SECONDS")
        os.environ["TRADEFLOW_SSE_MAX_SECONDS"] = "1"

    def tearDown(self):
        if self._orig_sse_cap is None:
            os.environ.pop("TRADEFLOW_SSE_MAX_SECONDS", None)
        else:
            os.environ["TRADEFLOW_SSE_MAX_SECONDS"] = self._orig_sse_cap
        super().tearDown()

    def _first_sse_frame(self) -> dict:
        with self.c.stream("GET", "/api/v1/stream/session") as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    return json.loads(line[len("data: "):])
        self.fail("no SSE data frame received")

    def test_healthy_stream_has_no_error_field(self):
        frame = self._first_sse_frame()
        self.assertIn("sessions", frame)
        self.assertNotIn("error", frame)

    def test_read_failure_is_surfaced_in_the_payload(self):
        from src.session import manager as manager_mod

        def boom(self):
            raise RuntimeError("simulated store read failure")

        original = manager_mod.SessionManager.list_sessions
        manager_mod.SessionManager.list_sessions = boom
        try:
            frame = self._first_sse_frame()
        finally:
            manager_mod.SessionManager.list_sessions = original

        self.assertIn(
            "error", frame,
            "a session read failure must be surfaced in the SSE payload, not swallowed",
        )
        self.assertIn("simulated store read failure", frame["error"])
        self.assertEqual(frame["sessions"], [])


class TestTestIsolationEnvOverrides(unittest.TestCase):
    """[task-1 item 10] Concurrent test runs must be able to avoid collisions."""

    def test_env_overrides_change_the_defaults(self):
        from src.session.manager import SessionManager
        tmp = tempfile.mkdtemp(prefix="tf_env_iso_")
        db = os.path.join(tmp, "alt.db")
        store = os.path.join(tmp, "alt_json")
        old = {k: os.environ.get(k) for k in ("TRADEFLOW_DB_PATH", "TRADEFLOW_STORE_DIR")}
        os.environ["TRADEFLOW_DB_PATH"] = db
        os.environ["TRADEFLOW_STORE_DIR"] = store
        try:
            mgr = SessionManager()
            self.assertEqual(os.path.abspath(mgr.db_path), os.path.abspath(db))
            self.assertEqual(os.path.abspath(mgr.store_dir), os.path.abspath(store))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_defaults_are_unchanged_when_unset(self):
        from src.session import manager as manager_mod
        self.assertEqual(manager_mod.DEFAULT_DB_PATH, "data/sessions.db")
        self.assertEqual(manager_mod.DEFAULT_STORE_DIR, "data/sessions_json")


if __name__ == "__main__":
    unittest.main()
