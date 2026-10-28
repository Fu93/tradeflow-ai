"""The held-out quote-reader evaluation scores what the desk would do, with no network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("eval_quote_reader", ROOT / "scripts" / "eval_quote_reader.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

SPANS = {
    # The span the fake model calls the supplier's own unit offer, per message id.
    "zh_rival_then_own": "我们正品单价 27.80 元",
    "zh_plain_umbrella": "单价 26.80 元/把",
    "zh_tiers": "28 元",
}


def _chat(content: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }


def _fake_transport_for(model, usage):
    def send(body):
        text = body["messages"][-1]["content"].split("DATA_START\n", 1)[1].rsplit("\nDATA_END", 1)[0]
        row_id = next(row["id"] for row in ev.load()[0] if row["text"] == text)
        usage.update({"prompt_tokens": 100, "completion_tokens": 40})
        span = SPANS.get(row_id)
        if span is None:
            return _chat({"mentions": []})
        mention = {
            "class": "own_unit_offer", "evidence_span": span, "speaker": "supplier",
            "polarity": "affirmative", "currency": "CNY", "unit": "per_piece", "is_question": False,
            # A number field from the model must be discarded, never used.
            "price": 1.0,
        }
        return _chat({"mentions": [mention]})

    return send


def test_held_out_set_is_well_formed():
    rows, sha = ev.load()
    assert len(rows) == 40
    assert len({row["id"] for row in rows}) == 40
    assert len(sha) == 64
    for row in rows:
        assert set(row) == {"id", "lang", "text", "own_offer"}
        if row["own_offer"] is not None:
            assert row["own_offer"] > 0


def test_outcome_names():
    assert ev.outcome(27.8, 27.8) == "correct"
    assert ev.outcome(24.0, 27.8) == "wrong"
    assert ev.outcome(1.0, None) == "wrong"
    assert ev.outcome(None, 27.8) == "missed"
    assert ev.outcome(None, None) == "held"


def test_model_alone_reads_the_amount_from_its_span_and_the_gate_needs_the_rules():
    rows, _sha = ev.load()
    report = ev.evaluate(rows, ["fake/model"], base="http://fake.invalid", target=30, ceiling=40,
                         transport_for=_fake_transport_for)
    scored = {item["id"]: item for item in report["per_message"]["fake/model"]}
    rival = scored["zh_rival_then_own"]
    assert rival["status"] == "ok"
    assert rival["alone"] == 27.8 and rival["alone_outcome"] == "correct"
    # The rules leave this message for a person, so the gate cannot accept it.
    assert rival["gate"] is None and rival["gate_outcome"] == "missed"
    plain = scored["zh_plain_umbrella"]
    assert plain["gate"] == 26.8 and plain["gate_outcome"] == "correct"
    # A model that picks one tier row is wrong, and the score says so.
    assert scored["zh_tiers"]["alone_outcome"] == "wrong"
    assert scored["zh_tiers"]["gate_outcome"] == "held"
    assert rival["numeric_field_discarded"] is True
    readers = report["readers"]
    assert set(readers) == {"rules", "fake/model", "gate:fake/model"}
    assert sum(readers["rules"]["tally"].values()) == 40
    assert readers["fake/model"]["answered"] == 40
    assert readers["fake/model"]["prompt_tokens_total"] == 4000


def test_offline_run_writes_no_endpoint_and_no_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "should-not-appear")
    out = tmp_path / "eval.json"
    assert ev.main(["--offline", "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["endpoint"] is None and report["models"] == []
    assert report["dataset_sha256"] == ev.load()[1]
    assert "should-not-appear" not in out.read_text(encoding="utf-8")


def test_live_run_without_a_key_refuses(monkeypatch, tmp_path):
    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ev.main(["--out", str(tmp_path / "x.json")]) == 2
