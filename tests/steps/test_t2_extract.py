"""T2 extractor: off-schema rows are dropped, and a dead endpoint fails closed."""
from __future__ import annotations

import json


def test_hostile_payloads_do_not_become_prices(monkeypatch):
    from src.desk.extract import extract_mentions, mentions_from_payload

    hostile = {
        "mentions": [
            {
                "speaker": "supplier",
                "polarity": "affirmative",
                "currency": "CNY",
                "unit": "per_piece",
                "class": "own_unit_offer",
                "evidence_span": "15.60元",
                "is_question": False,
                "price": 1,
                "amount": {"value": 9},
            },
            {"speaker": "supplier", "class": "not-a-class", "evidence_span": "1"},
            "not-a-row",
        ]
    }
    parsed = mentions_from_payload(hostile)
    assert parsed["rejected_rows"] >= 1
    assert all("price" not in row for row in parsed["mentions"])

    def boom(_body):
        raise RuntimeError("down")

    failed = extract_mentions("15.60元", transport=boom)
    assert failed["mentions"] == []
    assert failed["error"] == "extractor_failed"

    malformed = extract_mentions("15.60元", transport=lambda _body: ["nope"])
    assert malformed["mentions"] == []
    assert malformed["rejected_rows"] >= 1

    chat = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "mentions": [
                                {
                                    "speaker": "supplier",
                                    "polarity": "affirmative",
                                    "currency": "CNY",
                                    "unit": "per_piece",
                                    "class": "own_unit_offer",
                                    "evidence_span": "15.60元",
                                    "is_question": False,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }
    accepted = extract_mentions("15.60元", transport=lambda _body: chat)
    assert accepted["mentions"]
    assert accepted["response_sha256"]

    not_json = extract_mentions("15.60元", transport=lambda _body: {"choices": [{"message": {"content": "nope"}}]})
    assert not_json["error"] == "extractor_not_json"
    empty = extract_mentions("15.60元", transport=lambda _body: {"choices": []})
    assert empty["error"] == "extractor_malformed"


def test_live_call_fails_closed_when_the_endpoint_errors(monkeypatch):
    import urllib.request

    from src.desk.extract import extract_mentions

    monkeypatch.setenv("TRADEFLOW_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TRADEFLOW_LLM_MODEL", "test-model")
    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def explode(*_args, **_kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    quiet = extract_mentions("单价 15.60 元")
    assert quiet["configured"] is False
    assert quiet["mentions"] == []
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "test-key")
    result = extract_mentions("单价 15.60 元")
    assert result["mentions"] == []
    assert result["error"] == "extractor_unreachable"

    class _Response:
        def read(self):
            return b"not-json"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Response())
    again = extract_mentions("单价 15.60 元")
    assert again["error"] == "extractor_unreachable"

    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "test-key")

    class _Ok:
        def read(self):
            body = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"mentions": []}),
                        }
                    }
                ]
            }
            return json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Ok())
    reached = extract_mentions("单价 15.60 元")
    assert reached["configured"] is True
    assert reached["mentions"] == []
