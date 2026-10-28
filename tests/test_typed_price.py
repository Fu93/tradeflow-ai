"""A person can type a price the reader cannot read (28塊5), bound to the words it came from."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = [
    {"name": "Ningbo", "address": "ningbo@example.test", "channel": "email"},
    {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
    {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
    {"name": "Foshan", "address": "foshan@example.test", "channel": "email"},
]
MESSAGE = "老板，这款伞 28塊5 一把，起订 1000 把。"


@pytest.fixture
def deal(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
    for key in ("DESK_SMTP_SEND", "TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DESK_N_SAMPLES", "200")
    monkeypatch.setenv("DESK_N_GRID", "8")
    from src.desk.service import create_deal
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    snapshot = create_deal(
        store, product="umbrella", quantity=1000, target_price=24, ceiling_price=32,
        risk_tolerance=0.5, seed=11, suppliers=SUPPLIERS,
    )
    ids = {row["name"]: row["id"] for row in snapshot["suppliers"]}
    yield store, snapshot["deal"]["id"], ids
    store.close()


def _waiting(store, deal_id, supplier_id, text=MESSAGE):
    from src.desk.service import ingest_message

    return ingest_message(store, deal_id, source="paste", raw_text=text, supplier_id=supplier_id)["event"]


def test_the_reader_cannot_read_28_kuai_5(deal):
    store, deal_id, ids = deal
    event = _waiting(store, deal_id, ids["Ningbo"])
    assert event["status"] == "review"
    assert "28.5" not in [str(item) for item in event["provenance"].get("candidates") or []]


def test_a_typed_price_is_used_recorded_as_typed_and_passes_the_send_check(deal):
    from src.desk.service import accept_review, send_draft
    from src.desk.verify import _guard_send, draft_digest

    store, deal_id, ids = deal
    event = _waiting(store, deal_id, ids["Ningbo"])
    snapshot = accept_review(store, deal_id, event["id"], ids["Ningbo"], typed_amount=28.5, typed_words="28塊5")
    card = next(row for row in snapshot["suppliers"] if row["id"] == ids["Ningbo"])
    assert card["latest_quote"] == pytest.approx(28.5)
    assert card["quote_typed_words"] == "28塊5"
    stored = store.event(deal_id, event["id"])
    assert stored["provenance"]["typed_by_person"] is True
    assert stored["provenance"]["typed_words"] == "28塊5"
    chain = store.verify_chain(deal_id)
    assert chain["ok"]
    review = [e["payload"] for e in chain["entries"] if e.get("type") == "review"][-1]
    assert review["typed"] is True and review["token"] is None
    assert review["span"] == "28塊5" and review["value"] == pytest.approx(28.5)

    draft = next(item for item in store.drafts(deal_id) if item["supplier_id"] == ids["Ningbo"])
    assert _guard_send(store, draft, store.get_deal(deal_id))["counter"] is not None
    approved = asyncio.run(
        send_draft(store, draft["id"], human_approved=True, body_sha256=draft_digest(draft["body"]))
    )
    assert approved["status"] == "approved"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"typed_amount": 28.5, "typed_words": "28块5"}, "Name the words"),  # 块 is not 塊: not in the message
        ({"typed_amount": 28.5, "typed_words": ""}, "Name the words"),
        ({"typed_amount": 28.5, "typed_words": "28塊5", "chosen_token": "28"}, "not both"),
        ({"typed_amount": -1.0, "typed_words": "28塊5"}, "above zero"),
        ({"typed_amount": 285.0, "typed_words": "28塊5"}, "plausible unit-price band"),
    ],
)
def test_a_typed_price_is_refused_when_it_does_not_bind(deal, kwargs, message):
    from src.desk.service import accept_review
    from src.desk.store import DeskRejected

    store, deal_id, ids = deal
    event = _waiting(store, deal_id, ids["Ningbo"])
    with pytest.raises(DeskRejected, match=message):
        accept_review(store, deal_id, event["id"], ids["Ningbo"], **kwargs)
    assert store.event(deal_id, event["id"])["status"] == "review"


def _bind(raw_text, review, ask, ingested):
    from src.desk.verify import _review_binds_ok

    return _review_binds_ok(raw_text, review, ask, ingested)


def test_the_typed_bind_checks_the_message_the_words_and_the_amount():
    digest = hashlib.sha256(MESSAGE.encode("utf-8")).hexdigest()
    review = {"typed": True, "token": None, "value": 28.5, "span": "28塊5", "unit": "per_piece", "raw_sha256": digest}
    assert _bind(MESSAGE, review, 28.5, digest)
    # The ask being priced must be the typed amount.
    assert not _bind(MESSAGE, review, 27.0, digest)
    # A rewritten message: the digest no longer matches.
    other = MESSAGE.replace("28塊5", "26塊5")
    assert not _bind(other, review, 28.5, hashlib.sha256(other.encode("utf-8")).hexdigest())
    # The words must stand in the message.
    assert not _bind(MESSAGE, dict(review, span="30塊"), 28.5, digest)
    # Per piece only; a positive amount only.
    assert not _bind(MESSAGE, dict(review, unit="per_set"), 28.5, digest)
    assert not _bind(MESSAGE, dict(review, value=0.0), 0.0, digest)
    # A token review is never treated as typed, even with the flag.
    token_review = dict(review, token="28")
    assert not _bind(MESSAGE, token_review, 28.5, digest)


def test_desk_offers_a_typed_price_and_keeps_it_across_polls():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    assert "function typedRow(" in script
    assert "typed_amount: value, typed_words: words.value.trim()" in script
    assert "Recorded as typed by you." in script
    assert "typedDrafts.get(item.id)" in script
    assert "if (!(typing && reviewSig === lastReviewSig))" in script
