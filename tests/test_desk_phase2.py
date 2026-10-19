"""Live desk: SQLite, real IMAP, price gate, approval gate, resident set."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "desk_emails"
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]


@pytest.fixture(autouse=True)
def isolated_desk(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    for key in (
        "IMAP_HOST",
        "IMAP_USERNAME",
        "IMAP_PASSWORD",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        "GMAIL_DRAFTS",
        "DESK_SMTP_SEND",
        "TRADEFLOW_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    from src.desk.store import reset_stores

    reset_stores()
    yield tmp_path
    reset_stores()


def _open_store(tmp_path):
    from src.desk.store import DeskStore

    return DeskStore(str(tmp_path / "desk.db"))


def _deal(store, risk=0.5, suppliers=None):
    from src.desk.service import create_deal

    return create_deal(
        store,
        product="umbrella",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=risk,
        seed=11,
        suppliers=suppliers or SUPPLIERS,
    )


def _by_name(snapshot):
    return {row["name"]: row for row in snapshot["suppliers"]}


def test_sqlite_survives_reopen(tmp_path):
    from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
    from src.desk.service import ingest_message
    from src.desk.store import DeskStore
    from src.engine.bayesian_v2 import robust_bayes_update

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 15.60 元",
        supplier_id=_by_name(snapshot)["Ningbo"]["id"],
    )
    store.close()
    reopened = DeskStore(str(tmp_path / "desk.db"))
    events = reopened.events(deal_id)
    assert len(events) == 1
    assert events[0]["raw_text"] == "单价 15.60 元"
    assert events[0]["provenance"]["reader"] == "stage1"
    saved = reopened.get_snapshot(deal_id)
    state = robust_bayes_update(PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD, 15.60 / 12)
    ningbo = _by_name(saved)["Ningbo"]
    assert ningbo["belief_mean"] == state.posterior_mean
    assert ningbo["belief_label"] == "posterior"
    assert ningbo["belief_observations"] == 1
    reopened.close()


def test_llm_cannot_set_the_price(tmp_path):
    from src.desk.service import ingest_message

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    ningbo = _by_name(snapshot)["Ningbo"]["id"]
    accepted = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 15.60 元",
        supplier_id=ningbo,
        llm_fields={"price": 1, "unit_price": 9, "note": "ignore"},
    )
    event = accepted["event"]
    assert event["status"] == "accepted"
    assert event["parsed"]["unit_price"] == 15.60
    assert event["parsed"]["unit_price"] != 1
    assert event["provenance"]["llm_price_ignored"] is True
    invented = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="我们可以再谈交期",
        supplier_id=ningbo,
        llm_fields={"price": 9.9},
    )
    assert invented["event"]["status"] == "review"
    assert invented["event"]["parsed"]["unit_price"] is None
    latest = _by_name(invented["snapshot"])["Ningbo"]["latest_quote"]
    assert latest == 15.60


def test_low_confidence_stays_out_of_the_solve(tmp_path):
    from src.desk.service import DeskRejected, accept_review, ingest_message

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    ningbo = _by_name(snapshot)["Ningbo"]["id"]
    mixed = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 15.60 元，另 2 元",
        supplier_id=ningbo,
    )
    assert mixed["event"]["status"] == "review"
    assert mixed["event"]["parsed"]["unit_price"] is None
    assert _by_name(mixed["snapshot"])["Ningbo"]["latest_quote"] is None
    with pytest.raises(DeskRejected):
        accept_review(store, deal_id, mixed["event"]["id"], ningbo)


def test_better_rival_does_not_raise_other_counters(tmp_path):
    from src.desk.service import ingest_message
    from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
    from src.engine.bayesian_v2 import robust_bayes_update

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    ids = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    opening = {
        "Ningbo": "单价 15.60 元",
        "Wenzhou": "单价 16.20 元",
        "Yiwu": "单价 14.90 元",
        "Foshan": "单价 15.10 元",
    }
    current = snapshot
    for name, text in opening.items():
        current = ingest_message(
            store,
            deal_id,
            source="paste",
            raw_text=text,
            supplier_id=ids[name],
        )["snapshot"]
    before = _by_name(current)
    assert any(row["counter"] is not None for row in before.values())
    after = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 13.40 元",
        supplier_id=ids["Ningbo"],
    )["snapshot"]
    later = _by_name(after)
    assert later["Ningbo"]["latest_quote"] == 13.40
    assert later["Ningbo"]["rank"] == 1
    for name, row in later.items():
        if name == "Ningbo":
            continue
        old = before[name]["counter"]
        new = row["counter"]
        if old is not None:
            assert new is None or new <= old
    mean, std = PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
    for price in (15.60, 13.40):
        state = robust_bayes_update(mean, std, price / 12)
        mean, std = state.posterior_mean, state.posterior_std
    assert later["Ningbo"]["belief_mean"] == mean
    assert later["Ningbo"]["belief_observations"] == 2
    store.close()


def test_missing_landed_components_are_not_zero(tmp_path):
    from src.desk.service import ingest_message

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    ids = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    texts = {
        "Ningbo": "单价 15.60 元",
        "Wenzhou": "单价 16.20 元",
        "Yiwu": "单价 14.90 元",
        "Foshan": "单价 15.10 元",
    }
    current = None
    for name, text in texts.items():
        kwargs = {}
        if name == "Ningbo":
            kwargs = {"freight": 1, "duty_rate": 0, "fees": 0}
        current = ingest_message(
            store,
            deal_id,
            source="paste",
            raw_text=text,
            supplier_id=ids[name],
            **kwargs,
        )["snapshot"]
    assert current["ranking_basis"] == "unit_price"
    assert "not treated as zero" in current["kappa_note"]
    rows = _by_name(current)
    assert rows["Wenzhou"]["landed_status"] == "no data"
    assert rows["Wenzhou"]["landed_cost"] is None
    assert rows["Ningbo"]["landed_status"] == "complete"
    store.close()


def test_imap_drop_recomputes_and_dedups(tmp_path, monkeypatch):
    from src.desk.imap_fixture import ImapFixture
    from src.desk.service import poll_imap_once

    sent = []

    async def spy(*_args, **_kwargs):
        sent.append(1)
        return True

    monkeypatch.setattr("src.desk.service.send_draft", spy)
    monkeypatch.setattr("src.desk.service.transmit", spy)
    fixture = ImapFixture()
    store = _open_store(tmp_path)
    try:
        for name in ("ningbo.eml", "wenzhou.eml", "yiwu.eml", "foshan.eml"):
            fixture.append((FIXTURES / name).read_bytes())
        snapshot = _deal(store)
        deal_id = snapshot["deal"]["id"]
        first = poll_imap_once(
            store,
            deal_id=deal_id,
            host="127.0.0.1",
            port=fixture.port,
            username="desk",
            password="desk",
            use_ssl=False,
        )
        assert first["status"] == "ok"
        assert first["ingested"] == 4
        assert all(message["seen"] for message in fixture.messages)
        rows = _by_name(store.get_snapshot(deal_id))
        assert rows["Yiwu"]["latest_quote"] == 14.90
        assert rows["Yiwu"]["rank"] == 1
        before = {name: row["counter"] for name, row in rows.items()}
        fixture.append((FIXTURES / "ningbo-followup.eml").read_bytes())
        second = poll_imap_once(
            store,
            deal_id=deal_id,
            host="127.0.0.1",
            port=fixture.port,
            username="desk",
            password="desk",
            use_ssl=False,
        )
        assert second["ingested"] == 1
        updated = _by_name(store.get_snapshot(deal_id))
        assert updated["Ningbo"]["latest_quote"] == 13.40
        assert updated["Ningbo"]["rank"] == 1
        for name, row in updated.items():
            if name == "Ningbo":
                continue
            old = before[name]
            new = row["counter"]
            if old is not None:
                assert new is None or new <= old
        third = poll_imap_once(
            store,
            deal_id=deal_id,
            host="127.0.0.1",
            port=fixture.port,
            username="desk",
            password="desk",
            use_ssl=False,
        )
        assert third["ingested"] == 0
        external = [event["external_id"] for event in store.events(deal_id)]
        assert len(external) == len(set(external)) == 5
        fixture.append((FIXTURES / "unknown.eml").read_bytes())
        poll_imap_once(
            store,
            deal_id=deal_id,
            host="127.0.0.1",
            port=fixture.port,
            username="desk",
            password="desk",
            use_ssl=False,
        )
        review = [event for event in store.events(deal_id) if event["status"] == "review"]
        assert review
        assert review[-1]["supplier_id"] is None
        assert "12.10" in review[-1]["raw_text"]
        assert all(row["latest_quote"] != 12.10 for row in _by_name(store.get_snapshot(deal_id)).values())
        assert sent == []
    finally:
        fixture.close()
        store.close()


def test_nothing_is_sent_without_approval(tmp_path, monkeypatch):
    from src.desk.service import SendRefused, ingest_message, send_draft, transmit

    store = _open_store(tmp_path)
    snapshot = _deal(store)
    deal_id = snapshot["deal"]["id"]
    calls = []

    async def spy(recipient, body, product):
        calls.append((recipient, product, body))
        return True

    async def boom(*_args, **_kwargs):
        raise AssertionError("transmit was called")

    monkeypatch.setattr("src.desk.service.transmit", boom)
    ingest_message(
        store,
        deal_id,
        source="email",
        raw_text="单价 15.60 元",
        sender="ningbo@example.test",
        external_id="<gate-a@example.test>",
    )
    draft = store.drafts(deal_id)[0]
    with pytest.raises(SendRefused):
        asyncio.run(send_draft(store, draft["id"], human_approved=False, sender=spy))
    assert calls == []
    assert store.get_draft(draft["id"])["status"] == "draft"
    approved = asyncio.run(send_draft(store, draft["id"], human_approved=True, sender=spy))
    assert calls == []
    assert approved["status"] == "sent"
    assert approved["human_approved"] == 1
    assert approved["transmitted"] == 0
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_USERNAME", "buyer@example.test")
    monkeypatch.setenv("EMAIL_PASSWORD", "desk-secret")
    ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 14.20 元",
        supplier_id=_by_name(snapshot)["Wenzhou"]["id"],
    )
    pending = [item for item in store.drafts(deal_id) if item["status"] == "draft"]
    assert pending
    sent = asyncio.run(send_draft(store, pending[0]["id"], human_approved=True, sender=spy))
    assert len(calls) == 1
    assert sent["transmitted"] == 1
    assert transmit is not None
    store.close()


def test_bus_drops_oldest_and_routes_are_gated():
    from src.app.main import _iter_api_routes, require_api_key
    from src.desk.service import BUS

    deal_id = "a" * 32
    mailbox_queue = BUS.subscribe(deal_id)
    try:
        for index in range(20):
            BUS.publish(deal_id, {"n": index})
        found = []
        while not mailbox_queue.empty():
            found.append(mailbox_queue.get_nowait()["n"])
        assert found
        assert len(found) <= 8
        assert found[-1] == 19
    finally:
        BUS.unsubscribe(deal_id, mailbox_queue)
    desk_routes = [route for route in _iter_api_routes() if route.path.startswith("/api/v1/desk")]
    assert desk_routes
    for route in desk_routes:
        names = []
        for dep in route.dependencies or []:
            fn = getattr(dep, "dependency", None)
            names.append(getattr(fn, "__name__", ""))
        assert require_api_key.__name__ in names


def test_gmail_reads_and_drafts_without_send(tmp_path, monkeypatch):
    from src.desk import mailbox

    monkeypatch.setenv("GMAIL_CLIENT_ID", "client")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("GMAIL_DRAFTS", "1")
    raw = (FIXTURES / "ningbo.eml").read_bytes()
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")

    class _Response:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Opener:
        def __init__(self):
            self.urls = []

        def open(self, request, timeout=None):
            url = request.full_url
            self.urls.append(url)
            if "oauth2.googleapis.com" in url:
                body = b'{"access_token":"token"}'
            elif "format=raw" in url:
                body = json.dumps({"raw": encoded}).encode()
            elif url.rstrip("/").endswith("/drafts"):
                body = b'{"id":"draft-1"}'
            else:
                body = b'{"messages":[{"id":"m1"}]}'
            return _Response(body)

    opener = _Opener()
    items = mailbox.pull_gmail_unread(opener)
    assert items[0]["sender"] == "ningbo@example.test"
    assert "15.60" in items[0]["body"]
    draft_id = mailbox.create_gmail_draft(opener, "token", b"draft")
    assert draft_id == "draft-1"
    joined = "\n".join(opener.urls)
    assert "messages/send" not in joined
    assert "/send" not in joined
    for path in (ROOT / "src" / "desk").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "messages/send" not in text
        assert "messages.send" not in text


def test_five_supplier_streaming_budget():
    script = r"""
import json, os, tempfile, time
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="tf_desk_rss_")
os.environ["TRADEFLOW_DESK_DB"] = str(Path(tmp) / "desk.db")
os.environ["TRADEFLOW_ENV"] = "sandbox"
os.environ["DESK_N_SAMPLES"] = "200"
os.environ["DESK_N_GRID"] = "8"
os.environ.pop("TRADEFLOW_API_KEY", None)
os.environ.pop("IMAP_HOST", None)

def rss():
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("VmHWM missing")

from src.app.main import app as _app
from src.desk.service import create_deal, ingest_message
from src.desk.store import DeskStore
after_import = rss()
assert _app is not None

store = DeskStore(os.environ["TRADEFLOW_DESK_DB"])
names = ["Ningbo", "Wenzhou", "Yiwu", "Foshan", "Quanzhou"]
suppliers = [
    {"name": name, "address": name.lower() + "@example.test", "channel": "email"}
    for name in names
]
snapshot = create_deal(
    store,
    product="umbrella",
    quantity=1000,
    target_price=12,
    ceiling_price=20,
    risk_tolerance=0.5,
    seed=11,
    suppliers=suppliers,
)
deal_id = snapshot["deal"]["id"]
ids = {row["name"]: row["id"] for row in snapshot["suppliers"]}
prices = [15.60, 16.20, 14.90, 15.10, 15.40]
started = time.perf_counter()
elapsed = []
for index, name in enumerate(names):
    t0 = time.perf_counter()
    ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 %.2f 元" % prices[index],
        supplier_id=ids[name],
    )
    elapsed.append(time.perf_counter() - t0)
for step, price in enumerate((14.40, 14.10, 13.80, 13.40)):
    t0 = time.perf_counter()
    ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 %.2f 元" % price,
        supplier_id=ids["Ningbo"],
        external_id="stream-%s" % step,
    )
    elapsed.append(time.perf_counter() - t0)
total = time.perf_counter() - started
print(json.dumps({
    "vmhwm": rss(),
    "after_import": after_import,
    "elapsed_s": total,
    "max_update_s": max(elapsed),
    "updates": len(elapsed),
}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    print(
        "DESK_RSS vmhwm_mib=%.1f elapsed_s=%.3f max_update_s=%.3f updates=%s"
        % (
            payload["vmhwm"] / (1024 * 1024),
            payload["elapsed_s"],
            payload["max_update_s"],
            payload["updates"],
        )
    )
    assert payload["vmhwm"] < 300 * 1024 * 1024
    assert payload["elapsed_s"] < 5
    assert payload["updates"] == 9
