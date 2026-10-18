"""Category preset 綁定回歸測試。

[為什麼需要這個測試]
Web Portal 的 Commodity Category 選擇器原本只呼叫 `applyCategoryPreset()`，
該函式僅改寫四個顯示標籤與圖表，**從未建立或修改任何 session**。因此操作者
切換到「Aerospace CNC Metal Case」時，面板顯示 target ¥28 / ceiling ¥36.5，
但引擎仍在使用雨傘的 ¥12 / ¥16.2 —— 顯示的授權範圍與實際強制的授權範圍不一致。

修復後 `applyCategoryPreset()` 會 POST /api/v1/sessions，讓顯示與強制一致。
本測試固定「每個 preset 都能建立 session，且引擎確實套用該 mandate」這個性質，
避免前端又退回成純裝飾。
"""
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient
    from src.app import main as api_main
except Exception as e:  # pragma: no cover
    print(f"SKIP test_category_preset: {e}")
    raise SystemExit(0)


def test_preset_rounds_never_touch_the_real_session_store():
    """[AUDIT FIX — 2026-10-17] 這個測試會 POST /api/v1/sessions 並跑一輪，而它
    過去直接寫進 repo 的 data/sessions.db：benchmark 執行後，使用者的本機示範
    資料庫就多出 TEST_PRESET_UMBRELLA / _CNC_CASE / _BATTERY 三筆真實會話。

    現在由 tests/conftest.py 的 autouse fixture 把整個模組導向臨時目錄。這裡
    釘住那個前提：一旦 fixture 被移除或寫壞，這個測試會先紅，而不是等到使用者
    發現儀表板多了一排殘骸。
    """
    db_path = api_main.session_mgr.db_path
    assert os.path.basename(os.path.dirname(db_path)).startswith("tf_test_"), db_path
    assert db_path != os.path.abspath(os.path.join("data", "sessions.db")), db_path

# 與 src/sim/scenarios.py 的模擬授權對應。前端從 /api/v1/simulation/mandates
# 讀同一份目錄，不再在 app.js 裡寫第二份價格。
EXPECTED_PRESETS = {
    "umbrella": dict(target=12.0, ceiling=16.2, moq=5000),
    "cnc_case": dict(target=28.0, ceiling=36.5, moq=2000),
    "battery": dict(target=185.0, ceiling=240.0, moq=500),
}


def _client() -> TestClient:
    return TestClient(api_main.app)


def test_every_preset_creates_a_session_the_engine_honours():
    c = _client()
    for cat, exp in EXPECTED_PRESETS.items():
        sid = f"TEST_PRESET_{cat.upper()}"
        body = {
            "session_id": sid,
            "product_name": f"Preset {cat}",
            "target_price": exp["target"],
            "ceiling_price": exp["ceiling"],
            "moq": exp["moq"],
        }
        r = c.post("/api/v1/sessions", json=body)
        assert r.status_code == 200, f"{cat}: create failed {r.status_code} {r.text[:200]}"
        spec = r.json()["spec"]
        assert spec["ceiling_price"] == exp["ceiling"]

        # 引擎必須以這個 ceiling 運作，而不是預設雨傘的 16.2。
        rr = c.post(
            f"/api/v1/sessions/{sid}/round",
            json={"opponent_raw_text": "老闆，原料漲價，最低報價", "opponent_offer_price": exp["ceiling"] * 0.95},
        )
        assert rr.status_code == 200, f"{cat}: round failed {rr.status_code}"
        ao = rr.json()["action_object"]
        assert ao["walk_away_ceiling"] == exp["ceiling"], (
            f"{cat}: engine used ceiling {ao['walk_away_ceiling']}, expected {exp['ceiling']}"
        )
        assert ao["authorized_price"] <= exp["ceiling"]


def test_preset_selector_posts_to_sessions_endpoint():
    """靜態檢查：preset 處理函式必須真的呼叫建立 session 的端點。

    這是最直接防止「退回純裝飾」的守衛 —— 舊版完全沒有這個 fetch。
    """
    js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "src", "web", "static", "js", "app.js",
    )
    src = open(js_path, encoding="utf-8").read()
    m = re.search(r"async function applyCategoryPreset.*?\n\}", src, re.S)
    assert m, "applyCategoryPreset not found in app.js"
    fn = m.group(0)
    assert "/api/v1/sessions" in fn, (
        "applyCategoryPreset no longer creates a session; the dashboard would "
        "display a mandate the engine does not enforce"
    )
    html = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(js_path))), "templates", "index.html"),
        encoding="utf-8",
    ).read()
    assert 'onchange="previewMandate(this.value)"' in html
    assert "openSandboxDeal" in src
    assert "/api/v1/simulation/mandates" in src
    assert "target: 12.0" not in src
