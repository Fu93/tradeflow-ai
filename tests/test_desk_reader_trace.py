"""The desk shows what the model labelled: class and verbatim span, never an amount."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MESSAGE = "隔壁厂给你 24 元那是次料，我们正品单价 27.80 元/件。"


def _deal(tmp_path):
    from src.desk.service import create_deal
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = create_deal(
        store, product="Umbrella", quantity=1000, target_price=25, ceiling_price=32,
        risk_tolerance=0.5, seed=3, currency="CNY",
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )
    return store, opened["deal"]["id"], opened["suppliers"][0]["id"]


def _row(cls: str, span: str, **extra) -> dict:
    row = {
        "class": cls, "evidence_span": span, "speaker": "supplier", "polarity": "affirmative",
        "currency": "CNY", "unit": "per_piece", "is_question": False,
    }
    row.update(extra)
    return row


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
        ).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _ingest(tmp_path, monkeypatch, mentions: list[dict]):
    from src.desk.service import ingest_message, settle_extractor

    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Body({"mentions": mentions}))
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(store, deal_id, source="paste", raw_text=MESSAGE, supplier_id=supplier)
    settle_extractor(ingested["event"]["id"])
    event = store.event(deal_id, ingested["event"]["id"])
    store.close()
    return event


def test_labelled_spans_are_kept_for_display_without_amounts(tmp_path, monkeypatch):
    event = _ingest(tmp_path, monkeypatch, [
        _row("rival_price", "隔壁厂给你 24 元"),
        _row("own_unit_offer", "正品单价 27.80 元/件", price=27.8),
    ])
    trace = event["provenance"]["extractor_mentions"]
    assert [row["class"] for row in trace] == ["rival_price", "own_unit_offer"]
    assert [row["span"] for row in trace] == ["隔壁厂给你 24 元", "正品单价 27.80 元/件"]
    for row in trace:
        assert set(row) == {"class", "span", "speaker", "polarity", "is_question"}
    # The model's number never reaches the event; a person still confirms the amount.
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["auto_accept"] is False


def test_a_span_the_supplier_did_not_write_is_not_shown(tmp_path, monkeypatch):
    event = _ingest(tmp_path, monkeypatch, [
        _row("own_unit_offer", "单价 19.00 元"),  # not in the message
        _row("rival_price", "隔壁厂给你 24 元"),
    ])
    trace = event["provenance"]["extractor_mentions"]
    assert [row["span"] for row in trace] == ["隔壁厂给你 24 元"]


def test_the_trace_is_capped(tmp_path, monkeypatch):
    from src.config.heuristics import DESK_TRACE_MAX_MENTIONS

    event = _ingest(tmp_path, monkeypatch, [_row("other", "次料")] * (int(DESK_TRACE_MAX_MENTIONS) + 5))
    assert len(event["provenance"]["extractor_mentions"]) == int(DESK_TRACE_MAX_MENTIONS)


def test_desk_page_renders_the_trace_as_text_only():
    script = (ROOT / "src/web/static/js/desk.js").read_text(encoding="utf-8")
    start = script.index("function readerTrace(")
    body = script[start:script.index("function renderReview(", start)]
    assert "innerHTML" not in body
    assert "span.textContent = String(mention.span" in body
    assert script.count("readerTrace(item.provenance || {})") == 2
