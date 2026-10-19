"""Extra branches on the desk path so the coverage gate sees the failure cases."""
from __future__ import annotations

import email
import json
import os
from email.message import EmailMessage

import pytest

SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test"},
    {"name": "Wenzhou", "address": "wenzhou@example.test"},
    {"name": "Yiwu", "address": "yiwu@example.test"},
    {"name": "Foshan", "address": "foshan@example.test"},
]


def _deal_kwargs():
    return dict(
        product="valve",
        quantity=10,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=1,
        suppliers=SUPPLIERS,
    )


def test_create_deal_rejects_bad_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    from src.desk.service import create_deal
    from src.desk.store import DeskRejected, DeskStore

    store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
    base = _deal_kwargs()
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "suppliers": SUPPLIERS[:2]})
    blank = [dict(item) for item in SUPPLIERS]
    blank[0]["name"] = " "
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "suppliers": blank})
    no_address = [dict(item) for item in SUPPLIERS]
    no_address[0]["address"] = " "
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "suppliers": no_address})
    dup = [dict(item) for item in SUPPLIERS]
    dup[1]["name"] = "Ningbo"
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "suppliers": dup})
    dup_address = [dict(item) for item in SUPPLIERS]
    dup_address[1]["address"] = "ningbo@example.test"
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "suppliers": dup_address})
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "target_price": 30})
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "currency": "GBP"})
    with pytest.raises(DeskRejected):
        create_deal(store, **{**base, "risk_tolerance": 5})
    store.close()


def test_mailbox_window_and_gmail_use_the_injected_client(monkeypatch):
    from src.desk import mailbox

    raw = EmailMessage()
    raw["Subject"] = "quote"
    raw["Message-ID"] = "<q@example.test>"
    raw.set_content("单价 15.60 元")
    blob = raw.as_bytes()

    class Fake:
        untagged_responses = {"OK": [b"UIDVALIDITY 7"]}

        def select(self, _box, readonly=True):
            assert readonly is True
            return "OK", [b""]

        def response(self, _kind):
            return "OK", [b"UIDVALIDITY 7"]

        def uid(self, command, *args):
            if command == "SEARCH":
                return "OK", [b"1 2"]
            if "HEADER" in args[-1]:
                return "OK", [(b"1", b"Message-ID: <q@example.test>\r\n\r\n")]
            return "OK", [(b"1", blob)]

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(mailbox, "_open_imap", lambda *args, **kwargs: Fake())
    found, validity = mailbox.pull_window(
        host="127.0.0.1",
        port=993,
        username="desk",
        password="desk",
        use_ssl=True,
        skip_ids=set(),
    )
    assert validity
    assert found
    skipped, _validity = mailbox.pull_window(
        host="127.0.0.1",
        port=993,
        username="desk",
        password="desk",
        use_ssl=True,
        skip_ids={found[0]["external_id"]},
    )
    assert skipped == []

    monkeypatch.setenv("IMAP_LOOKBACK_DAYS", "nope")
    assert mailbox.lookback_days() == 14
    monkeypatch.setenv("IMAP_LOOKBACK_DAYS", "0")
    assert mailbox.lookback_days() == 1
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("IMAP_USERNAME", "desk")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")
    monkeypatch.delenv("IMAP_PORT", raising=False)
    settings = mailbox.imap_settings_from_env()
    assert settings["port"] == 993

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, timeout=30):
            url = request.full_url
            if "oauth2" in url:
                return Response({"access_token": "token"})
            if "messages?" in url:
                if "pageToken" in url:
                    return Response({"messages": [{"id": "m2"}]})
                return Response({"messages": [{"id": "m1"}], "nextPageToken": "next"})
            if "/drafts" in url:
                return Response({"id": "d1"})
            encoded = email.message.Message()
            return Response({"raw": __import__("base64").urlsafe_b64encode(blob).decode("ascii")})

    monkeypatch.setenv("GMAIL_CLIENT_ID", "id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("GMAIL_DRAFTS", "1")
    opener = Opener()
    assert mailbox.refresh_access_token(opener) == "token"
    assert mailbox.gmail_list_recent(opener, "token") == ["m1", "m2"]
    assert mailbox.gmail_get_raw(opener, "token", "m1")
    assert mailbox.create_gmail_draft(opener, "token", blob, draft_id="d0") == "d1"
    pulled = mailbox.pull_gmail_window(opener, skip_ids=set())
    assert pulled


def test_desk_routes_cover_outcome_send_and_assumptions(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "desk-branch-fill-key1")
    from fastapi.testclient import TestClient
    from src.app.main import app
    from src.desk.store import reset_stores

    reset_stores()
    client = TestClient(app)
    headers = {"X-API-Key": "desk-branch-fill-key1"}
    created = client.post(
        "/api/v1/desk/deals",
        headers=headers,
        json={
            "product": "valve",
            "quantity": 10,
            "target_price": 12,
            "ceiling_price": 20,
            "suppliers": SUPPLIERS,
        },
    )
    assert created.status_code == 200, created.text
    deal_id = created.json()["deal"]["id"]
    supplier = created.json()["suppliers"][0]["id"]
    assumptions = client.post(
        f"/api/v1/desk/deals/{deal_id}/assumptions",
        headers=headers,
        json={"include_unverified": True, "reserve_prior_mean": 0.7, "reserve_prior_std": 0.1},
    )
    assert assumptions.status_code == 200, assumptions.text
    refused = client.post(
        f"/api/v1/desk/deals/{deal_id}/suppliers/{supplier}/outcome",
        headers=headers,
        json={"outcome": "continue"},
    )
    assert refused.status_code == 422
    outcome = client.post(
        f"/api/v1/desk/deals/{deal_id}/suppliers/{supplier}/outcome",
        headers=headers,
        json={"outcome": "continue", "quote": 15.6, "offer": 14.0},
    )
    assert outcome.status_code == 200, outcome.text
    missing_send = client.post(
        "/api/v1/desk/drafts/" + ("ab" * 16) + "/send",
        headers=headers,
        json={"human_approved": False, "body_sha256": "0" * 64},
    )
    assert missing_send.status_code in {404, 422}
    reset_stores()


def test_headline_and_budget_edges():
    from src.desk.headline import confirmed_prices, human_confirmed, saving_vs_best_first
    from src.desk.recompute import _bounded_int, _std_or_tiny

    assert human_confirmed({"status": "accepted", "provenance": {"confirmed": False}}) is False
    assert human_confirmed({"status": "accepted", "provenance": {"review_accepted_by": "bot"}}) is False
    kept, count = confirmed_prices([{"status": "review", "parsed": {}}])
    assert kept == [] and count == 0
    saved = saving_vs_best_first([], None, simulated=True, unconfirmed=1)
    assert saved["counted"] is False
    assert "unconfirmed numbers used in decisions: 1" in saved["audit"]
    assert _bounded_int("nope", default=3, low=1, high=5) == 3
    assert _bounded_int("0", default=3, low=1, high=5) == 1
    assert _bounded_int("9", default=3, low=1, high=5) == 5
    assert _std_or_tiny(0.0) > 0.0
