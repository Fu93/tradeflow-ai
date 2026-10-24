"""Stage-2 LLM paths: response parsing, honest failure, and the fallback.

These paths only execute when a real chat endpoint is configured, which is
why they sat uncovered: a test cannot call the network. But the logic around
the call is production behaviour — a 200 with unparseable JSON, an empty
message from a reasoning model that exhausted its tokens, a timeout — each
must leave ``last_llm_error`` saying exactly what happened and return
``None`` so the caller falls back to Stage 1, never a crash, never a
fabricated classification. The adversarial nativeness audit must fall back
to its static result the same way. ``requests.post`` is monkeypatched; no
test talks to a network.
"""
from __future__ import annotations

import pytest

from src.engine.bilingual import CrossLingualSanityChecker, InboundIntentClassifier, scan_draft_prices


class _FakeResponse:
    def __init__(self, status_code=200, content=None, payload=None, text=""):
        self.status_code = status_code
        self._content = content
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is not None:
            return self._payload
        return {"choices": [{"message": {"content": self._content}}]}


def _endpoint():
    return {"base_url": "http://127.0.0.1:9", "api_key": "test-key", "model": "test-model"}


@pytest.fixture()
def with_endpoint(monkeypatch):
    monkeypatch.setattr("src.engine.llm_config.resolve_chat_endpoint", lambda *a, **k: _endpoint())
    monkeypatch.setattr("src.engine.bilingual.REGEX_CONF_SPEC", 0.55, raising=False)


class TestScanDraftPrices:
    def test_scans_marked_and_suffixed_prices(self):
        assert scan_draft_prices("單價 ¥12.30，另 $1.5/pcs") == [12.30, 1.5]

    def test_empty_text_scans_nothing(self):
        assert scan_draft_prices("") == []


class TestClassifyWithLlm:
    def test_a_valid_reply_parses_into_a_classification(self, with_endpoint, monkeypatch):
        content = '{"primary_intent": "CRY_POVERTY_COST_HIKE", "confidence": 0.9, "tactical_rationale": "r", "requires_escalation": false}'
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(content=f"noise {content} noise")
        )
        result = InboundIntentClassifier.classify_with_llm("原材料漲價了，真的做不了")
        assert result is not None
        assert result.primary_intent.value == "CRY_POVERTY_COST_HIKE"
        assert InboundIntentClassifier.last_llm_error is None

    def test_an_http_error_is_reported_not_swallowed(self, with_endpoint, monkeypatch):
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(status_code=500, text="boom")
        )
        assert InboundIntentClassifier.classify_with_llm("text") is None
        assert "HTTP 500" in (InboundIntentClassifier.last_llm_error or "")

    def test_an_empty_reply_is_reported(self, with_endpoint, monkeypatch):
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(content="   ")
        )
        assert InboundIntentClassifier.classify_with_llm("text") is None
        assert "empty message content" in (InboundIntentClassifier.last_llm_error or "")

    def test_unparseable_content_is_reported(self, with_endpoint, monkeypatch):
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(content="no json here at all")
        )
        assert InboundIntentClassifier.classify_with_llm("text") is None
        assert "no parsable JSON" in (InboundIntentClassifier.last_llm_error or "")

    def test_a_raised_exception_is_reported_with_its_type(self, with_endpoint, monkeypatch):
        def boom(*args, **kwargs):
            raise TimeoutError("the endpoint hung up")

        monkeypatch.setattr("requests.post", boom)
        assert InboundIntentClassifier.classify_with_llm("text") is None
        assert "TimeoutError" in (InboundIntentClassifier.last_llm_error or "")


class TestAdversarialNativenessAudit:
    def test_a_valid_llm_reply_is_labelled_llm_adversarial(self, with_endpoint, monkeypatch):
        content = '{"is_native": true, "confidence": 0.87, "critique": "authentic"}'
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(content=content)
        )
        result = CrossLingualSanityChecker.check_semantic_nativeness("這批貨我們自己做不了")
        assert result["audit_mode"] == "llm_adversarial"
        assert result["is_native"] is True
        assert result["critique"] == "authentic"

    def test_a_broken_llm_reply_falls_back_to_the_static_audit(self, with_endpoint, monkeypatch):
        monkeypatch.setattr(
            "requests.post", lambda *a, **k: _FakeResponse(content="{{not json")
        )
        result = CrossLingualSanityChecker.check_semantic_nativeness("這批貨我們自己做不了")
        assert result["audit_mode"] != "llm_adversarial"
        assert result["is_native"] is True

    def test_without_an_endpoint_the_static_audit_still_runs(self, monkeypatch):
        monkeypatch.setattr("src.engine.llm_config.resolve_chat_endpoint", lambda *a, **k: None)
        result = CrossLingualSanityChecker.check_semantic_nativeness("這批貨我們自己做不了")
        assert result["audit_mode"] != "llm_adversarial"
        assert result["is_native"] is True

    def test_stiff_translation_markers_are_flagged_without_any_llm(self, monkeypatch):
        monkeypatch.setattr("src.engine.llm_config.resolve_chat_endpoint", lambda *a, **k: None)
        result = CrossLingualSanityChecker.check_semantic_nativeness("尊敬的貴方，我們公司希望了解價格")
        assert result["is_native"] is False
