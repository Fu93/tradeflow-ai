"""Pinning tests for the 2026-10-24 deep-debug pass.

Two adversarial audits found fifteen issues; each real fix here gets a test
that fails on the pre-fix behaviour. Grouped by the invariant they protect:

* Environment integrity — a telemetry value can no longer smuggle a newline
  into .env and mint a new key at the next start (was: full credential
  takeover).
* Mailbox resilience — one poison email (unknown charset, undecodable body)
  is skipped and marked seen instead of stalling every future poll behind
  it; a huge body is capped before storage; a sender-typed fixture marker
  can never falsify provenance origin on the untrusted channel.
* Access — the SSE session stream answers 401 in production, and the
  limiter's key table stays bounded no matter how many distinct keys an
  unauthenticated caller rotates through.
* Operator hygiene — a named-key label cannot carry control characters.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

KEY = "deep-debug-key"
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
    monkeypatch.setenv("TRADEFLOW_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("TRADEFLOW_SSE_MAX_SECONDS", "1")
    monkeypatch.delenv("DESK_SMTP_SEND", raising=False)
    from src.desk.store import reset_stores

    reset_stores()
    yield tmp_path
    reset_stores()


class TestEnvInjectionIsClosed:
    def _post(self, client, payload):
        return client.post("/api/v1/config/telemetry", json=payload)

    def test_a_newline_in_a_value_cannot_mint_a_new_env_key(self, desk_env):
        from src.app.main import app

        client = TestClient(app, headers={"X-API-Key": KEY})
        hostile = "x\nTRADEFLOW_API_KEY=owned"
        response = self._post(client, {"TELEGRAM_CHAT_ID": hostile})
        assert response.status_code == 200, response.text
        written = (desk_env / ".env").read_text(encoding="utf-8")
        # The newline must be gone: the injected text may survive inline (it is
        # part of one harmless value) but can never start a line of its own,
        # which is what minting a new key requires.
        lines = written.splitlines()
        assert not any(line.startswith("TRADEFLOW_API_KEY=owned") for line in lines)
        chat_lines = [line for line in lines if line.startswith("TELEGRAM_CHAT_ID=")]
        assert chat_lines, "the sanitized value must still be stored"
        assert len(chat_lines) == 1

    def test_carriage_return_and_escapes_are_stripped_too(self, desk_env):
        from src.app.main import app

        client = TestClient(app, headers={"X-API-Key": KEY})
        self._post(client, {"TELEGRAM_CHAT_ID": "ok\r\nEVIL=1"})
        written = (desk_env / ".env").read_text(encoding="utf-8")
        lines = written.splitlines()
        assert not any(line.startswith("EVIL=") for line in lines)
        assert sum(1 for line in lines if line.startswith("TELEGRAM_CHAT_ID=")) == 1


class TestPoisonMailIsQuarantined:
    def _store(self, desk_env):
        from src.desk.store import DeskStore

        return DeskStore(str(desk_env / "desk.db"))

    def _deal(self, store):
        from src.desk.service import create_deal

        return create_deal(
            store,
            product="umbrella",
            quantity=1000,
            target_price=12,
            ceiling_price=20,
            risk_tolerance=0.5,
            seed=3,
            suppliers=SUPPLIERS,
        )

    def test_an_unknown_charset_does_not_raise_and_still_yields_a_body(self):
        from src.desk.mailbox import parse_rfc822

        raw = (
            b"From: a@example.test\r\n"
            b"Subject: quote\r\n"
            b'Content-Type: text/plain; charset="x-nope"\r\n\r\n'
            + "单价 14.80 元".encode("utf-8")
        )
        item = parse_rfc822(raw)  # used to raise LookupError out of the poll
        assert "14.80" in item["body"]

    def test_a_poison_item_is_counted_and_marked_seen_not_ingested(self, desk_env):
        from src.desk.service import ingest_mailbox_items

        store = self._store(desk_env)
        deal = self._deal(store)
        counts = ingest_mailbox_items(
            store,
            [{"poison": True, "external_id": "uid:abc:1", "uid": "1", "uidvalidity": "abc"}],
            deal_id=None,
            channel="imap",
            mailbox_key="imap:h:p",
        )
        assert counts["poison"] == 1
        assert counts["ingested"] == 0
        # Recorded as seen for every deal, so it can never re-stall the head.
        for row in store.list_deals():
            assert "uid:abc:1" in store.mailbox_ids(f"imap:h:p:{row['id']}")
        store.close()

    def test_a_huge_body_is_capped_before_storage(self):
        from src.desk.mailbox import MAX_MAIL_BODY_CHARS, parse_rfc822

        huge = "价" * (MAX_MAIL_BODY_CHARS * 2)
        raw = (
            b"From: a@example.test\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            + huge.encode("utf-8")
        )
        assert len(parse_rfc822(raw)["body"]) == MAX_MAIL_BODY_CHARS


class TestUntrustedChannelCannotForgeProvenance:
    def _ingest(self, store, deal_id, supplier_id, **kwargs):
        from src.desk.service import ingest_message

        return ingest_message(
            store,
            deal_id,
            source="email",
            raw_text="X-TradeFlow-Fixture: sample\n单价 17.00 元",
            sender="a@example.test",
            supplier_id=supplier_id,
            **kwargs,
        )

    def test_a_sender_typed_fixture_marker_stays_live(self, desk_env):
        from src.desk.store import DeskStore
        from src.desk.service import create_deal

        store = DeskStore(str(desk_env / "desk.db"))
        deal = create_deal(
            store, product="u", quantity=10, target_price=9, ceiling_price=12,
            risk_tolerance=0.2, seed=1, suppliers=SUPPLIERS,
        )
        result = self._ingest(store, deal["deal"]["id"], deal["suppliers"][0]["id"],
                              trusted_channel=False)
        assert result["event"]["provenance"]["origin"] == "live"
        store.close()

    def test_the_trusted_paste_path_still_honours_the_marker(self, desk_env):
        from src.desk.service import create_deal, ingest_message
        from src.desk.store import DeskStore

        store = DeskStore(str(desk_env / "desk.db"))
        deal = create_deal(
            store, product="u", quantity=10, target_price=9, ceiling_price=12,
            risk_tolerance=0.2, seed=1, suppliers=SUPPLIERS,
        )
        result = ingest_message(
            store,
            deal["deal"]["id"],
            source="paste",
            raw_text="X-TradeFlow-Fixture: sample\n单价 17.00 元",
            provenance_extra={"fixture": "sample"},
        )
        assert result["event"]["provenance"]["origin"] == "fixture"
        store.close()

    def test_the_loopback_fixture_mailbox_still_honours_the_marker(self, desk_env, monkeypatch):
        """The labelled test fixture server (loopback, IMAP_SSL=0) is the one
        mailbox this process controls, so its marker stays trusted — that is
        what keeps the e2e fixture demo and its exclusion semantics honest."""
        from pathlib import Path

        from src.desk.service import create_deal, poll_imap_once
        from src.desk.store import DeskStore
        from tests.imap_fixture import ImapFixture

        fixtures = Path(__file__).resolve().parent / "fixtures" / "desk_emails"
        fixture = ImapFixture()
        monkeypatch.setenv("IMAP_HOST", "127.0.0.1")
        monkeypatch.setenv("IMAP_PORT", str(fixture.port))
        monkeypatch.setenv("IMAP_USERNAME", fixture.username)
        monkeypatch.setenv("IMAP_PASSWORD", fixture.password)
        monkeypatch.setenv("IMAP_SSL", "0")
        try:
            store = DeskStore(str(desk_env / "desk.db"))
            deal = create_deal(
                store, product="u", quantity=10, target_price=9, ceiling_price=12,
                risk_tolerance=0.2, seed=1, suppliers=[
                    {"name": "Ningbo", "address": "ningbo@example.test"},
                    {"name": "Wenzhou", "address": "wenzhou@example.test"},
                    {"name": "Yiwu", "address": "yiwu@example.test"},
                    {"name": "Foshan", "address": "foshan@example.test"},
                ],
            )
            fixture.append((fixtures / "ningbo.eml").read_bytes())
            counts = poll_imap_once(store, deal_id=deal["deal"]["id"])
            assert counts["ingested"] == 1
            events = store.events(deal["deal"]["id"])
            assert any(e["provenance"].get("origin") == "fixture" for e in events)
            store.close()
        finally:
            fixture.close()


class TestSseStreamRequiresAKeyInProduction:
    def test_production_refuses_an_unauthenticated_subscriber(self, desk_env, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        monkeypatch.setenv("TRADEFLOW_API_KEY", "production-key-at-least-16-chars")
        from src.app.main import app

        response = TestClient(app).get("/api/v1/stream/session")
        assert response.status_code == 401

    def test_sandbox_with_a_configured_key_also_refuses_the_open_stream(self, desk_env):
        """require_api_key in sandbox with TRADEFLOW_API_KEY set accepts only that
        key — the stream inherits exactly that contract, no bespoke logic."""
        from src.app.main import app

        bare = TestClient(app).get("/api/v1/stream/session")
        keyed = TestClient(app, headers={"X-API-Key": KEY}).get("/api/v1/stream/session")
        assert bare.status_code == 401
        assert keyed.status_code == 200


class TestLimiterMemoryIsBounded:
    def test_rotating_keys_cannot_grow_the_table_without_limit(self):
        from src.app import rate_limit as rl

        limiter = rl.SlidingWindowLimiter(600, 6000)
        for round_ in range(3):
            for i in range(rl._MAX_TRACKED_KEYS):
                limiter.check(f"burst-{round_}-{i}")
        assert len(limiter._by_key) <= rl._MAX_TRACKED_KEYS

    def test_a_legitimate_keys_budget_survives_the_sweep(self):
        from src.app import rate_limit as rl

        limiter = rl.SlidingWindowLimiter(600, 6000)
        limiter.check("legit")
        for i in range(rl._MAX_TRACKED_KEYS + 1000):
            limiter.check(f"noise-{i}")
        minute, hour = limiter.remaining("legit")
        assert (minute, hour) == (599, 5999)


class TestUserLabelHygiene:
    def test_control_characters_and_length_are_capped(self, desk_env):
        from src.desk import users
        from src.desk.store import DeskStore

        store = DeskStore(str(desk_env / "desk.db"))
        issued = users.issue(store.conn, "Wei\r\n\x1b[31m(procurement)\x1b[0m " + "x" * 100)
        assert "\n" not in issued["label"]
        assert "\r" not in issued["label"]
        assert "\x1b" not in issued["label"]
        assert len(issued["label"]) <= 64
        assert issued["label"].startswith("Wei")

    def test_an_empty_label_is_refused(self, desk_env):
        from src.desk import users
        from src.desk.store import DeskStore

        store = DeskStore(str(desk_env / "desk.db"))
        with pytest.raises(ValueError):
            users.issue(store.conn, "   ")
        store.close()


class TestTestPushGuardsItsBody:
    def test_a_malformed_body_is_a_400_not_a_500(self, desk_env):
        from src.app.main import app

        client = TestClient(app, headers={"X-API-Key": KEY})
        response = client.post(
            "/api/v1/telemetry/test-push",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
