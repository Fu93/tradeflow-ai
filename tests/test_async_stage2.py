"""Stage-2 非同步語意分類的效能與正確性回歸測試。

背景：Stage-2（AMD tokenfactory 開源模型）為同步網路呼叫，實測需 2–13 秒。
若它被無條件放進同步 HTTP 路徑，會阻塞主談判輪次。
本測試套件確保：
1. 主輪次端點預設**不**觸發 Stage-2（維持毫秒級）。
2. `deep_semantic=true` 才啟用 Stage-2。
3. 獨立的非同步語意端點可正確回傳 Stage-1 與 Stage-2 兩層結果。
4. Stage-2 缺席時系統正確降級，不拋錯。
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import src.app.main as app_module
from src.engine.bilingual import InboundIntentClassifier


@pytest.fixture(scope="module")
def client():
    return TestClient(app_module.app)


# 這句話的關鍵線索對 Stage-1 正則不明顯，是 Stage-2 存在的理由。
AMBIGUOUS_TEXT = "這價格嘛...你懂的，咱們私下再聊聊細節，平台這邊手續太麻煩了"


def test_stage1_only_never_makes_network_calls():
    """classify_stage1_only 必須在毫秒級完成，證明未發出網路請求。"""
    t0 = time.perf_counter()
    res = InboundIntentClassifier.classify_stage1_only(AMBIGUOUS_TEXT)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert res is not None and res.primary_intent is not None
    # 若誤觸發 Stage-2，這裡會是數千毫秒。
    assert elapsed_ms < 500, (
        f"classify_stage1_only took {elapsed_ms:.0f}ms; it appears to be making a "
        "network call. Stage-1 must stay deterministic and offline."
    )


def test_round_endpoint_default_does_not_invoke_stage2(client):
    """主輪次端點預設必須走快速路徑。"""
    created = client.post("/api/v1/sessions", json={
        "session_id": "async-regression-01",
        "product_name": "Sample",
        "target_price": 12.0,
        "ceiling_price": 16.2,
            "expected_markup": 0.0,
        })
    assert created.status_code == 200, created.text
    t0 = time.perf_counter()
    r = client.post(
        "/api/v1/sessions/async-regression-01/round",
        json={"supplier_text": AMBIGUOUS_TEXT, "supplier_offer": 15.0},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200
    # 引擎本身約需 1–3 秒；Stage-2 會額外增加 2–13 秒。
    # 門檻設在 8 秒以容納慢速 CI，同時仍能抓到「Stage-2 被誤觸發」的情況。
    assert elapsed_ms < 8000, (
        f"Round endpoint took {elapsed_ms:.0f}ms with deep_semantic unset. "
        "Stage-2 may have been invoked on the default path."
    )


def test_semantic_endpoint_without_stage2_is_fast(client):
    """enable_stage2=false 時，語意端點不得呼叫模型。"""
    t0 = time.perf_counter()
    r = client.post(
        "/api/v1/classify/semantic",
        json={"text": AMBIGUOUS_TEXT, "enable_stage2": False},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200
    body = r.json()
    assert body["stage2_available"] is False
    assert body["stage2"] is None
    assert body["stage1"]["primary_intent"] is not None
    assert elapsed_ms < 2000, f"Expected fast path, took {elapsed_ms:.0f}ms"


def test_semantic_endpoint_reports_stage2_availability(client):
    """啟用 Stage-2 時，端點必須誠實回報模型是否真的可用。

    在沒有配置 API 金鑰的環境中（例如 CI），stage2 應為 None 且
    stage2_available 為 False，但端點仍須正常回應，不得拋錯。
    """
    r = client.post(
        "/api/v1/classify/semantic",
        json={"text": AMBIGUOUS_TEXT, "enable_stage2": True},
    )
    assert r.status_code == 200
    body = r.json()

    if body["stage2_available"]:
        # 有模型可用：結果結構必須完整。
        assert set(body["stage2"]) >= {
            "primary_intent", "confidence", "tactical_rationale", "requires_escalation"
        }
        assert 0.0 <= body["stage2"]["confidence"] <= 1.0
    else:
        # 無模型可用（例如 CI 未配置金鑰）：必須優雅降級，而非拋出例外。
        assert body["stage1"]["primary_intent"] is not None
