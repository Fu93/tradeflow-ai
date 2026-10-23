"""Desk route error paths, pinned over HTTP, not by calling the service layer.

The happy paths were covered by the service gold tests, but the route layer's
contract with the client — which HTTP status each failure becomes — was only
exercised for a few endpoints. A route that turns a KeyError into a 500, or a
refusal into a 404, is exactly the kind of regression that hides until a real
client hits it. These tests pin:

* every desk mutation route answers 404 for a well-formed but unknown id and
  422 when the service refuses the action;
* the send gate answers 409 when the draft changed under the approver;
* the poll surface keeps its channel validation and translates mailbox and
  TLS failures into 502, never a 500;
* the audit verify surface returns a summary by default and an explicit,
  paginated window when entries are requested.
"""
from __future__ import annotations

import ssl

import pytest
from fastapi.testclient import TestClient

KEY = "desk-routes-gap-key"

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]

MISSING = "f" * 32  # well-formed, never exists


@pytest.fixture()
def desk_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", KEY)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
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


def _make_deal(client):
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


def _reviewed_draft(client, snapshot):
    """Ingest one quote and accept it, returning the pending draft."""
    deal_id = snapshot["deal"]["id"]
    supplier_id = snapshot["suppliers"][0]["id"]
    ingested = client.post(
        f"/api/v1/desk/deals/{deal_id}/quotes",
        json={"source": "paste", "raw_text": "单价 14.80 元", "supplier_id": supplier_id},
    )
    assert ingested.status_code == 200, ingested.text
    event_id = ingested.json()["event"]["id"]
    review = client.post(
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        json={"supplier_id": supplier_id, "action": "accept", "chosen_token": "14.80"},
    )
    assert review.status_code == 200, review.text
    after = client.get(f"/api/v1/desk/deals/{deal_id}").json()
    return next(item for item in after["drafts"] if item["status"] == "draft")


class TestUnknownIdIs404Not500:
    """A well-formed id that does not exist must be 404 on every route."""

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("get", f"/api/v1/desk/deals/{MISSING}", None),
            ("post", f"/api/v1/desk/deals/{MISSING}/quotes", {"source": "paste", "raw_text": "单价 14.80 元"}),
            ("post", f"/api/v1/desk/deals/{MISSING}/review/{MISSING}", {"action": "accept"}),
            ("post", f"/api/v1/desk/deals/{MISSING}/suppliers/{MISSING}/outcome", {"outcome": "continue"}),
            ("post", f"/api/v1/desk/deals/{MISSING}/migrate", {"human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/policy", {
                "human_confirmed": True,
                "product": "umbrella",
                "quantity": 1000,
                "target_price": 12,
                "ceiling_price": 20,
                "risk_tolerance": 0.5,
            }),
            ("post", f"/api/v1/desk/deals/{MISSING}/reanchor", {"human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/anchor-history/recover", {"human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/suppliers/{MISSING}/address",
             {"address": "new@example.test", "human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/suppliers/{MISSING}/keep",
             {"address": "new@example.test", "human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/suppliers/{MISSING}/reopen", {"human_confirmed": True}),
            ("post", f"/api/v1/desk/deals/{MISSING}/assumptions", {"bayes_obs_noise": 0.5}),
            ("get", f"/api/v1/desk/deals/{MISSING}/audit/verify", None),
            ("get", f"/api/v1/desk/deals/{MISSING}/evidence.json", None),
        ],
    )
    def test_read_and_confirmation_routes(self, client, method, path, body):
        kwargs = {} if body is None else {"json": body}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404, response.text

    def test_send_answers_404_for_an_unknown_draft(self, client):
        response = client.post(
            f"/api/v1/desk/drafts/{MISSING}/send",
            json={"human_approved": True, "body_sha256": "a" * 64},
        )
        assert response.status_code == 404, response.text

    def test_poll_answers_404_for_an_unknown_deal(self, client):
        response = client.post("/api/v1/desk/poll", json={"channel": "imap", "deal_id": MISSING})
        assert response.status_code == 404, response.text


class TestRefusalIs422Not500:
    """A DeskRejected must reach the client as 422 with the reason."""

    def test_a_review_action_must_be_accept_or_reject(self, client):
        response = client.post(
            f"/api/v1/desk/deals/{MISSING}/review/{MISSING}", json={"action": "maybe"}
        )
        assert response.status_code == 422, response.text

    def test_an_outcome_must_be_one_of_the_registered_words(self, client):
        snapshot = _make_deal(client)
        deal_id = snapshot["deal"]["id"]
        supplier_id = snapshot["suppliers"][0]["id"]
        response = client.post(
            f"/api/v1/desk/deals/{deal_id}/suppliers/{supplier_id}/outcome",
            json={"outcome": "settlement"},
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("path,body", [
        ("/migrate", {"human_confirmed": False}),
        ("/policy", {
            "human_confirmed": False,
            "product": "umbrella",
            "quantity": 1000,
            "target_price": 12,
            "ceiling_price": 20,
            "risk_tolerance": 0.5,
        }),
        ("/reanchor", {"human_confirmed": False}),
        ("/anchor-history/recover", {"human_confirmed": False}),
    ])
    def test_confirmations_without_a_human_are_refused(self, client, path, body):
        snapshot = _make_deal(client)
        response = client.post(f"/api/v1/desk/deals/{snapshot['deal']['id']}{path}", json=body)
        assert response.status_code == 422, response.text
        assert "person" in response.json()["detail"].lower()

    def test_an_address_change_without_confirmation_is_refused(self, client):
        snapshot = _make_deal(client)
        deal_id = snapshot["deal"]["id"]
        supplier_id = snapshot["suppliers"][0]["id"]
        response = client.post(
            f"/api/v1/desk/deals/{deal_id}/suppliers/{supplier_id}/address",
            json={"address": "new@example.test", "human_confirmed": False},
        )
        assert response.status_code == 422, response.text

    def test_assumptions_convert_the_bool_for_the_store(self, client):
        snapshot = _make_deal(client)
        deal_id = snapshot["deal"]["id"]
        response = client.post(
            f"/api/v1/desk/deals/{deal_id}/assumptions",
            json={"include_unverified": True, "bayes_obs_noise": 0.5},
        )
        assert response.status_code == 200, response.text
        from src.desk.store import get_store

        assert get_store().get_deal(deal_id)["include_unverified"] == 1


class TestSendGateStatuses:
    def test_send_without_a_human_is_refused_422(self, client):
        snapshot = _make_deal(client)
        draft = _reviewed_draft(client, snapshot)
        response = client.post(
            f"/api/v1/desk/drafts/{draft['id']}/send",
            json={"human_approved": False, "body_sha256": "a" * 64},
        )
        assert response.status_code == 422, response.text
        assert "person" in response.json()["detail"].lower()

    def test_send_with_a_stale_digest_answers_409(self, client):
        snapshot = _make_deal(client)
        draft = _reviewed_draft(client, snapshot)
        response = client.post(
            f"/api/v1/desk/drafts/{draft['id']}/send",
            json={"human_approved": True, "body_sha256": "b" * 64},
        )
        assert response.status_code == 409, response.text
        assert "changed" in response.json()["detail"].lower()


class TestPollSurface:
    def test_an_unknown_channel_is_422(self, client):
        response = client.post("/api/v1/desk/poll", json={"channel": "smtp"})
        assert response.status_code == 422, response.text
        assert "imap or gmail" in response.json()["detail"]

    def test_imap_without_a_mailbox_is_a_clean_not_configured(self, client):
        response = client.post("/api/v1/desk/poll", json={"channel": "imap"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "not_configured"

    def test_gmail_without_a_mailbox_is_a_clean_not_configured(self, client):
        response = client.post("/api/v1/desk/poll", json={"channel": "gmail"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "not_configured"

    def test_a_mailbox_failure_is_502_not_500(self, client, monkeypatch):
        from src.desk.mailbox import MailboxError

        def boom(*args, **kwargs):
            raise MailboxError("the mailbox is unreachable")

        monkeypatch.setattr("src.desk.routes.poll_imap_once", boom)
        response = client.post("/api/v1/desk/poll", json={"channel": "imap"})
        assert response.status_code == 502, response.text
        assert "unreachable" in response.json()["detail"]

    def test_a_bad_mail_certificate_is_502_with_the_reason(self, client, monkeypatch):
        def boom(*args, **kwargs):
            raise ssl.SSLError("certificate verify failed")

        monkeypatch.setattr("src.desk.routes.poll_imap_once", boom)
        response = client.post("/api/v1/desk/poll", json={"channel": "imap"})
        assert response.status_code == 502, response.text
        assert "certificate" in response.json()["detail"]


class TestAuditVerifySurface:
    def test_the_default_answer_is_a_summary_without_entries(self, client):
        snapshot = _make_deal(client)
        response = client.get(f"/api/v1/desk/deals/{snapshot['deal']['id']}/audit/verify")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["count"] >= 1
        assert "entries" not in body

    def test_entries_are_opt_in_and_paginated(self, client):
        snapshot = _make_deal(client)
        deal_id = snapshot["deal"]["id"]
        first = client.get(
            f"/api/v1/desk/deals/{deal_id}/audit/verify",
            params={"include_entries": "true", "offset": 0, "limit": 1},
        ).json()
        assert len(first["entries"]) == 1
        assert first["offset"] == 0
        if first["count"] > 1:
            assert first["truncated"] is True
        tail = client.get(
            f"/api/v1/desk/deals/{deal_id}/audit/verify",
            params={"include_entries": "true", "offset": first["count"], "limit": 5},
        ).json()
        assert tail["entries"] == []
        assert tail["truncated"] is False


class TestChainKeyFailClosed:
    def test_deal_creation_without_a_chain_key_is_503(self, client, monkeypatch):
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY", raising=False)
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
        assert response.status_code == 503, response.text
        assert "chain key missing" in response.json()["detail"]
