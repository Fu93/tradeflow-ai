"""Measured extractor chain: Nebius Ultra, Nebius Super, then NVIDIA Super."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from email.message import Message

from src.config.heuristics import (
    EXTRACTOR_FALLBACK_MODEL,
    EXTRACTOR_MAX_OUTPUT_TOKENS,
    EXTRACTOR_MODEL,
    EXTRACTOR_NVIDIA_BASE_URL,
    EXTRACTOR_NVIDIA_MODEL,
    EXTRACTOR_REASONING_BUDGET,
    EXTRACTOR_TIMEOUT_BUDGET_SECONDS,
    EXTRACTOR_TIMEOUT_NVIDIA_SECONDS,
    EXTRACTOR_TIMEOUT_SUPER_SECONDS,
    EXTRACTOR_TIMEOUT_ULTRA_SECONDS,
)


def _mention(span: str = "18.40 USD per piece") -> dict:
    return {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "USD",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": span,
        "is_question": False,
    }


def _chat(payload: dict | list, *, finish: str = "stop") -> dict:
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(payload)}}]}


def _empty_chat() -> dict:
    return _chat({"mentions": []})


def _http(code: int, body: bytes = b"down") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://example.test/v1/chat/completions",
        code,
        "no",
        Message(),
        io.BytesIO(body),
    )


def _keys(monkeypatch, *, nvidia: str | None = None) -> None:
    monkeypatch.setenv("TRADEFLOW_LLM_API_KEY", "nebius-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if nvidia is None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("NVIDIA_API_KEY", nvidia)


def test_request_honours_the_model_override_and_grows_the_token_cap(monkeypatch):
    from src.config.heuristics import EXTRACTOR_CHARS_PER_TOKEN, EXTRACTOR_OUTPUT_TOKEN_CAP
    from src.desk.extract import extract_mentions, output_token_limit

    monkeypatch.setenv("TRADEFLOW_LLM_MODEL", "caller-override-model")
    seen: list[dict] = []

    def _transport(body):
        seen.append(body)
        return _empty_chat()

    message = "packing list " * 800
    result = extract_mentions(message, transport=_transport)
    assert len(seen) == 1
    body = seen[0]
    assert body["model"] == "caller-override-model"
    assert body["temperature"] == 0
    cap = output_token_limit(message)
    assert cap > EXTRACTOR_MAX_OUTPUT_TOKENS
    assert cap <= EXTRACTOR_OUTPUT_TOKEN_CAP
    assert cap == len(message) // int(EXTRACTOR_CHARS_PER_TOKEN) + EXTRACTOR_REASONING_BUDGET
    assert body["max_tokens"] == cap
    assert body["reasoning_budget"] == EXTRACTOR_REASONING_BUDGET
    assert body["stream"] is False
    assert message in body["messages"][1]["content"]
    assert result.get("error") is None
    assert result["mentions"] == []


def test_truncated_output_fails_closed_even_when_the_json_looks_valid():
    from src.desk.extract import _from_chat, extract_mentions
    from src.desk.shadow import shadow_from_message

    content = json.dumps({"mentions": [_mention()]})
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    for finish in ("length", "max_tokens"):
        parsed = _from_chat({"choices": [{"finish_reason": finish, "message": {"content": content}}]})
        assert parsed["mentions"] == []
        assert parsed["error"] == "extractor_truncated"
        assert parsed["response_sha256"] == digest

    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        return {"choices": [{"finish_reason": "length", "message": {"content": content}}]}

    direct = extract_mentions("unit price 18.40 USD per piece", transport=_transport)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
    assert direct["mentions"] == []
    assert direct["error"] == "extractor_truncated"
    assert [item["status"] for item in direct["attempts"]] == ["truncated", "truncated"]
    assert all(item["provider"] == "nebius" for item in direct["attempts"])
    shadow = shadow_from_message(
        "unit price 18.40 USD per piece",
        {"status": "accepted", "parsed": {"unit_price": 18.40}},
        low=1,
        high=100,
        transport=_transport,
    )
    assert shadow["would_accept"] is False
    assert shadow["auto_accept"] is False
    assert shadow["error"] == "extractor_truncated"
    assert shadow["attempts"]


def test_a_bad_body_stays_on_nebius_and_a_provider_failure_opens_nvidia(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    calls: list[str] = []

    def _bad(_body):
        calls.append(_body["model"])
        return ["nope"]

    malformed = extract_mentions("unit price 18.40 USD", transport=_bad)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
    assert malformed["mentions"] == []
    assert malformed["error"] == "extractor_not_object"
    assert [item["provider"] for item in malformed["attempts"]] == ["nebius", "nebius"]
    assert all("ultra" not in item["model"].lower() or item["provider"] == "nebius" for item in malformed["attempts"])

    calls.clear()

    def _provider(body):
        calls.append(body["model"])
        assert body["max_tokens"] == EXTRACTOR_MAX_OUTPUT_TOKENS
        assert body["stream"] is False
        if len(calls) == 1:
            return {"choices": [{"message": {"content": "nope"}}]}
        if len(calls) == 2:
            raise TimeoutError("slow")
        assert "ultra" not in body["model"].lower()
        return _empty_chat()

    opened = extract_mentions("unit price 18.40 USD", transport=_provider)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL, EXTRACTOR_NVIDIA_MODEL]
    assert opened.get("error") is None
    assert opened["model"] == EXTRACTOR_NVIDIA_MODEL
    assert [item["status"] for item in opened["attempts"]] == ["not_json", "timeout", "ok"]
    assert opened["attempts"][2]["provider"] == "nvidia"
    assert "ultra" not in opened["attempts"][2]["model"].lower()


def test_nvidia_hop_uses_its_own_host_and_key(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    monkeypatch.setenv("TRADEFLOW_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TRADEFLOW_LLM_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    clock = {"now": 0.0}
    monkeypatch.setattr("src.desk.extract.time.perf_counter", lambda: clock["now"])
    seen: list[tuple] = []

    def _urlopen(request, timeout=0):
        body = json.loads(request.data.decode("utf-8"))
        model = body["model"]
        seen.append((request.full_url, model, timeout, request.get_header("Authorization"), body))
        if request.full_url.startswith(EXTRACTOR_NVIDIA_BASE_URL):
            class _Ok:
                def read(self):
                    return json.dumps(_empty_chat()).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            return _Ok()
        if model == EXTRACTOR_MODEL:
            raise _http(503, b"ultra-down")
        clock["now"] = float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS) - float(EXTRACTOR_TIMEOUT_NVIDIA_SECONDS) + 1
        raise _http(429, b"slow-down")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    result = extract_mentions("unit price 18.40 USD")
    assert [item[1] for item in seen] == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL, EXTRACTOR_NVIDIA_MODEL]
    assert seen[0][0].startswith("http://127.0.0.1:9")
    assert seen[1][0].startswith("http://127.0.0.1:9")
    assert seen[2][0].startswith(EXTRACTOR_NVIDIA_BASE_URL)
    assert seen[0][2] == float(EXTRACTOR_TIMEOUT_ULTRA_SECONDS)
    assert seen[1][2] == float(EXTRACTOR_TIMEOUT_SUPER_SECONDS)
    assert seen[2][2] == float(EXTRACTOR_TIMEOUT_NVIDIA_SECONDS) - 1
    assert seen[0][3] == "Bearer nebius-key"
    assert seen[2][3] == "Bearer nvidia-key"
    assert seen[2][4]["stream"] is False
    assert seen[2][4]["max_tokens"] == EXTRACTOR_MAX_OUTPUT_TOKENS
    assert seen[2][4]["reasoning_budget"] == EXTRACTOR_REASONING_BUDGET
    assert result.get("error") is None
    assert result["model"] == EXTRACTOR_NVIDIA_MODEL
    assert [item["status"] for item in result["attempts"]] == ["http_5xx", "http_429", "ok"]
    assert result["attempts"][0]["response_sha256"] == hashlib.sha256(b"ultra-down").hexdigest()
    assert result["attempts"][1]["latency_s"] >= 0
    assert all(not (item["provider"] == "nvidia" and "ultra" in item["model"].lower()) for item in result["attempts"])


def test_budget_stops_before_later_hops(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("called")))
    monkeypatch.setattr("src.desk.extract.EXTRACTOR_TIMEOUT_BUDGET_SECONDS", 0)
    quiet = extract_mentions("unit price 18.40 USD")
    assert quiet["mentions"] == []
    assert quiet["error"] == "extractor_unreachable"
    assert [item["status"] for item in quiet["attempts"]] == ["budget"]
    assert quiet["attempts"][0]["model"] == EXTRACTOR_MODEL

    clock = {"now": 0.0}
    calls: list[str] = []
    monkeypatch.setattr("src.desk.extract.EXTRACTOR_TIMEOUT_BUDGET_SECONDS", EXTRACTOR_TIMEOUT_BUDGET_SECONDS)
    monkeypatch.setattr("src.desk.extract.time.perf_counter", lambda: clock["now"])

    def _urlopen(request, timeout=0):
        model = json.loads(request.data.decode("utf-8"))["model"]
        calls.append(model)
        if model == EXTRACTOR_FALLBACK_MODEL:
            clock["now"] = float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS) + 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    spent = extract_mentions("unit price 18.40 USD")
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
    assert [item["status"] for item in spent["attempts"]] == ["unreachable", "unreachable", "budget"]
    assert spent["attempts"][2]["provider"] == "nvidia"
    assert spent["error"] == "extractor_unreachable"
    assert spent["mentions"] == []


def test_connection_errors_are_provider_failures(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        if len(calls) == 1:
            raise TimeoutError("t")
        if len(calls) == 2:
            raise urllib.error.URLError(TimeoutError("timed out"))
        raise ConnectionResetError("reset")

    failed = extract_mentions("unit price 18.40 USD", transport=_transport)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL, EXTRACTOR_NVIDIA_MODEL]
    assert [item["status"] for item in failed["attempts"]] == ["timeout", "timeout", "unreachable"]
    assert failed["error"] == "extractor_unreachable"
    assert failed["mentions"] == []


def test_nvidia_key_alone_does_not_call(monkeypatch):
    import urllib.request

    from src.desk.extract import extract_mentions

    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("called")))
    quiet = extract_mentions("unit price 18.40 USD")
    assert quiet["configured"] is False
    assert quiet["mentions"] == []


def test_client_error_does_not_open_nvidia(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        raise _http(400, b"no")

    result = extract_mentions("unit price 18.40 USD", transport=_transport)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL]
    assert [item["status"] for item in result["attempts"]] == ["http_400", "http_400"]
    assert all(item["provider"] != "nvidia" for item in result["attempts"])


def test_malformed_body_opens_nvidia(monkeypatch):
    from src.desk.extract import _UnreadableBody, extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    calls: list[str] = []

    def _transport(body):
        calls.append(body["model"])
        if len(calls) == 3:
            return _empty_chat()
        raise _UnreadableBody("abc")

    result = extract_mentions("unit price 18.40 USD", transport=_transport)
    assert calls == [EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL, EXTRACTOR_NVIDIA_MODEL]
    assert [item["status"] for item in result["attempts"]] == ["malformed", "malformed", "ok"]
    assert result.get("error") is None


def test_timeout_env_overrides_the_registry(monkeypatch):
    from src.desk.extract import extract_mentions

    _keys(monkeypatch, nvidia="nvidia-key")
    monkeypatch.setenv("TRADEFLOW_EXTRACTOR_TIMEOUT_ULTRA", "3")
    seen: list[float] = []

    def _urlopen(request, timeout=0):
        seen.append(timeout)
        class _Ok:
            def read(self):
                return json.dumps(_empty_chat()).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return _Ok()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    extract_mentions("unit price 18.40 USD")
    assert seen == [3.0]


def test_hop_timeout_is_a_total_deadline(monkeypatch):
    import time

    from src.desk.extract import extract_mentions

    _keys(monkeypatch)
    monkeypatch.setenv("TRADEFLOW_EXTRACTOR_TIMEOUT_ULTRA", "0.15")
    monkeypatch.setenv("TRADEFLOW_EXTRACTOR_TIMEOUT_SUPER", "0.15")
    monkeypatch.setenv("TRADEFLOW_EXTRACTOR_TIMEOUT_BUDGET", "0.4")

    def _urlopen(request, timeout=0):
        time.sleep(30)
        raise AssertionError("past the deadline")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    started = time.monotonic()
    result = extract_mentions("unit price 18.40 USD")
    elapsed = time.monotonic() - started
    assert elapsed < 2
    assert [item["status"] for item in result["attempts"]] == ["timeout", "timeout"]
    assert result["error"] == "extractor_unreachable"


def _deal(tmp_path):
    from src.desk.service import create_deal
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = create_deal(
        store,
        product="Bracket",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        currency="CNY",
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )
    return store, opened["deal"]["id"], opened["suppliers"][0]["id"]


def test_ingest_returns_before_the_extractor_finishes(tmp_path, monkeypatch):
    from src.desk.service import ingest_message, settle_extractor

    monkeypatch.setattr("src.desk.extract.configured", lambda: True)
    release = threading.Event()
    started = threading.Event()

    def _fake(_message, *, transport=None):
        started.set()
        assert release.wait(5)
        return {
            "mentions": [],
            "configured": True,
            "numeric_field_discarded": False,
            "rejected_rows": 0,
            "model": EXTRACTOR_MODEL,
            "prompt_sha256": "p",
            "response_sha256": "abc",
            "error": "extractor_unreachable",
            "attempts": [
                {
                    "provider": "nebius",
                    "model": EXTRACTOR_MODEL,
                    "status": "timeout",
                    "latency_s": 1.0,
                    "response_sha256": "",
                }
            ],
        }

    monkeypatch.setattr("src.desk.shadow.extract_mentions", _fake)
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    event = ingested["event"]
    assert event["status"] == "review"
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["auto_accept"] is False
    assert event["provenance"]["extractor_pending"] is True
    assert started.wait(5)
    release.set()
    settle_extractor(event["id"])
    stored = store.event(deal_id, event["id"])
    assert stored is not None
    assert stored["status"] == "review"
    assert stored["parsed"]["unit_price"] is None
    assert stored["provenance"]["auto_accept"] is False
    assert stored["provenance"]["extractor_pending"] is False
    assert stored["provenance"]["extractor_error"] == "extractor_unreachable"
    assert stored["provenance"]["extractor_attempts"][0]["status"] == "timeout"
    assert stored["provenance"]["extractor_attempts"][0]["provider"] == "nebius"
    store.close()


def test_a_failed_chain_stays_in_review_and_the_record_keeps_every_attempt(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message, settle_extractor

    _keys(monkeypatch, nvidia="nvidia-key")

    def _urlopen(request, timeout=0):
        raise _http(503, b"no")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    event_id = ingested["event"]["id"]
    settle_extractor(event_id)
    event = store.event(deal_id, event_id)
    assert event is not None
    assert event["status"] == "review"
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["auto_accept"] is False
    assert event["provenance"]["shadow_would_accept"] is False
    attempts = event["provenance"]["extractor_attempts"]
    assert [item["provider"] for item in attempts] == ["nebius", "nebius", "nvidia"]
    assert [item["model"] for item in attempts] == [
        EXTRACTOR_MODEL,
        EXTRACTOR_FALLBACK_MODEL,
        EXTRACTOR_NVIDIA_MODEL,
    ]
    assert [item["status"] for item in attempts] == ["http_5xx", "http_5xx", "http_5xx"]
    assert all(item["response_sha256"] == hashlib.sha256(b"no").hexdigest() for item in attempts)
    assert all("ultra" not in item["model"].lower() or item["provider"] == "nebius" for item in attempts)
    accepted = accept_review(store, deal_id, event_id, supplier, chosen_token="18.40")
    card = next(row for row in accepted["suppliers"] if row["id"] == supplier)
    stored = store.get_decision(card["decision_id"])
    assert stored is not None
    recorded = stored["record"]["extractor_attempts"]
    assert [item["provider"] for item in recorded] == ["nebius", "nebius", "nvidia"]
    assert [item["status"] for item in recorded] == ["http_5xx", "http_5xx", "http_5xx"]
    assert recorded[2]["model"] == EXTRACTOR_NVIDIA_MODEL
    assert "ultra" not in recorded[2]["model"].lower()
    fresh = store.event(deal_id, event_id)
    assert fresh is not None
    assert fresh["provenance"]["auto_accept"] is False
    store.close()


def test_an_extractor_exception_still_leaves_the_quote_for_review(tmp_path, monkeypatch):
    from src.desk.service import ingest_message, settle_extractor

    monkeypatch.setattr("src.desk.extract.configured", lambda: True)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr("src.desk.shadow.shadow_from_message", _boom)
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    settle_extractor(ingested["event"]["id"])
    event = store.event(deal_id, ingested["event"]["id"])
    assert event is not None
    assert event["status"] == "review"
    assert event["parsed"]["unit_price"] is None
    assert event["provenance"]["auto_accept"] is False
    assert event["provenance"]["extractor_error"] == "extractor_failed"
    assert event["provenance"]["extractor_pending"] is False
    store.close()


def test_without_a_key_the_extractor_stays_inline(tmp_path, monkeypatch):
    from src.desk.service import _EXTRACTOR_THREADS, ingest_message, settle_extractor

    monkeypatch.delenv("TRADEFLOW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    called = {"n": 0}

    def _fake(_message, *, transport=None):
        called["n"] += 1
        return {"mentions": [], "configured": False, "numeric_field_discarded": False}

    monkeypatch.setattr("src.desk.shadow.extract_mentions", _fake)
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    assert called["n"] == 1
    assert "extractor_pending" not in ingested["event"]["provenance"]
    settle_extractor(ingested["event"]["id"])
    assert ingested["event"]["id"] not in _EXTRACTOR_THREADS
    store.close()


def test_accept_returns_while_the_extractor_is_still_running(tmp_path, monkeypatch):
    from src.desk.service import accept_review, ingest_message, settle_extractor

    monkeypatch.setattr("src.desk.extract.configured", lambda: True)
    release = threading.Event()
    started = threading.Event()

    def _fake(_message, *, transport=None):
        started.set()
        release.wait(5)
        return {
            "mentions": [],
            "configured": True,
            "numeric_field_discarded": False,
            "rejected_rows": 0,
            "model": EXTRACTOR_MODEL,
            "prompt_sha256": "p",
            "response_sha256": "abc",
            "error": "extractor_unreachable",
            "attempts": [],
        }

    monkeypatch.setattr("src.desk.shadow.extract_mentions", _fake)
    store, deal_id, supplier = _deal(tmp_path)
    ingested = ingest_message(
        store,
        deal_id,
        source="paste",
        raw_text="单价 18.40 元/件",
        supplier_id=supplier,
    )
    assert started.wait(5)
    done = threading.Event()
    box: dict = {}

    def _accept() -> None:
        try:
            box["snapshot"] = accept_review(
                store, deal_id, ingested["event"]["id"], supplier, chosen_token="18.40",
            )
        except Exception as exc:
            box["error"] = exc
        done.set()

    threading.Thread(target=_accept).start()
    assert done.wait(1), "confirm waited on the extractor"
    assert "error" not in box
    card = next(row for row in box["snapshot"]["suppliers"] if row["id"] == supplier)
    assert abs(float(card["latest_quote"]) - 18.4) < 0.001
    release.set()
    settle_extractor(ingested["event"]["id"])
    fresh = store.event(deal_id, ingested["event"]["id"])
    assert fresh is not None
    assert fresh["status"] == "accepted"
    assert abs(float(fresh["parsed"]["unit_price"]) - 18.4) < 0.001
    store.close()
