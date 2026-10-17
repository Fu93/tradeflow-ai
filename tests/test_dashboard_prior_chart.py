"""Real browser regression test: the belief chart must plot THIS session's prior.

[為什麼需要這個測試]
儀表板曾把先驗點硬編成 `target_price * 1.08`。對雨傘的預設情境剛好正確，
所以任何只檢查「圖上有點」或「數字是 12.96」的測試都不會發現：換一個
`prior_reserve_mean`（例如 CNC/電池情境的 1.25）之後，圖上畫的仍然是 1.08。
本測試建立一個 1.25 先驗的會話，在真 Chromium 裡從 Chart.js 實例讀回
畫出的點，要求它等於該會話自己的先驗 —— 硬編值會直接失敗。

另外一併鎖住「沒有量測就不得填 0」：Breakdown Risk 在後端未提供
`p_break` 時必須顯示破折號，而不是 0.0%。這些都是瀏覽器渲染行為，
因此只能用量測，不能靠讀原始碼字串。

執行： python -m pytest tests/test_dashboard_prior_chart.py -q
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, seconds: float = 60.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def _post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


@pytest.fixture(scope="module")
def live_server():
    """Boot the real app on a private port with an isolated session store."""
    port = _free_port()
    tmp = tempfile.mkdtemp(prefix="tf_dashboard_")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": REPO,
            "TRADEFLOW_DB_PATH": os.path.join(tmp, "sessions.db"),
            "TRADEFLOW_STORE_DIR": os.path.join(tmp, "sessions_json"),
        }
    )
    env.pop("TRADEFLOW_API_KEY", None)
    env.pop("TRADEFLOW_WEBHOOK_TOKEN", None)

    log_path = os.path.join(tmp, "server.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import uvicorn; from src.app.main import app; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')",
        ],
        cwd=REPO,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health(port):
            log.flush()
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                pytest.fail("dashboard server did not start:\n" + fh.read()[-2000:])
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _read_chart(port: int, session_id: str) -> dict:
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 950})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            page.evaluate(
                """(deal) => {
                    const rows = [...document.querySelectorAll('.session-item')];
                    const row = rows.find(x => x.textContent.includes(deal));
                    if (row) row.click();
                }""",
                session_id,
            )
            page.wait_for_timeout(2500)
            out = page.evaluate(
                """() => {
                    const inst = (typeof Chart !== 'undefined' && Chart.getChart)
                        ? Chart.getChart('beliefChart') : null;
                    const risk = document.getElementById('cbPct');
                    const empty = document.getElementById('beliefEmpty');
                    const canvas = document.getElementById('beliefChart');
                    return {
                        labels: inst ? inst.data.labels : [],
                        series: inst ? inst.data.datasets.map(d => d.data) : [],
                        risk: risk ? risk.textContent.trim() : null,
                        empty_hidden: empty ? empty.hidden : null,
                        canvas_hidden: canvas ? canvas.style.visibility === 'hidden' : null,
                    };
                }"""
            )
            out["errors"] = errors
            return out
        finally:
            browser.close()


def test_chart_prior_comes_from_the_session_not_a_constant(live_server):
    port = live_server
    target, prior = 12.0, 1.25
    _post(
        port,
        "/api/v1/sessions",
        {
            "session_id": "DASH_PRIOR_125",
            "product_name": "CNC Bracket (non-umbrella prior)",
            "target_price": target,
            "ceiling_price": 16.2,
            "moq": 5000,
            "max_rounds": 5,
            "prior_reserve_mean": prior,
        },
    )

    out = _read_chart(port, "DASH_PRIOR_125")
    assert not out["errors"], f"page errors: {out['errors']}"
    assert out["series"], "belief chart was never instantiated"
    prior_point = out["series"][0][0]
    assert prior_point is not None, "chart has no prior point at all"
    assert abs(prior_point - prior * target) < 0.01, (
        "chart prior does not come from this session's prior_reserve_mean: "
        f"drew {prior_point!r}, expected {prior * target!r}"
    )
    assert abs(prior_point - 12.96) > 0.01, (
        "chart prior is the hardcoded umbrella constant 1.08 * 12.0 = 12.96"
    )
    # 沒有輪次就沒有量測：破局風險不得顯示成 0%，圖也必須明說「還沒有資料」。
    assert out["risk"] == "\u2014", f"risk shown without any measurement: {out['risk']!r}"
    assert out["empty_hidden"] is False, "empty-state hint hidden although there are no rounds"


def test_risk_and_chart_use_real_round_values(live_server):
    port = live_server
    target, prior = 12.0, 1.25
    _post(
        port,
        "/api/v1/sessions",
        {
            "session_id": "DASH_PRIOR_125_ROUND",
            "product_name": "CNC Bracket (non-umbrella prior)",
            "target_price": target,
            "ceiling_price": 16.2,
            "moq": 5000,
            "max_rounds": 5,
            "prior_reserve_mean": prior,
        },
    )
    _post(
        port,
        "/api/v1/sessions/DASH_PRIOR_125_ROUND/round",
        {"opponent_raw_text": "our best is 14.80", "opponent_offer_price": 14.80, "mode": "copilot"},
    )

    out = _read_chart(port, "DASH_PRIOR_125_ROUND")
    assert not out["errors"], f"page errors: {out['errors']}"
    assert len(out["labels"]) >= 2, f"round was not plotted: {out['labels']}"
    assert abs(out["series"][0][0] - prior * target) < 0.01

    risk_point = out["series"][1][-1]
    assert risk_point is not None, "no breakdown risk plotted for a completed round"
    assert 0.0 < risk_point < 100.0
    assert out["risk"] != "0.0%", "risk percentage was not derived from the measured p_break"
    assert out["risk"] is not None and out["risk"].endswith("%")


def test_unknown_session_does_not_render_a_fake_measurement(live_server):
    """未知/空的會話狀態不得留下上一次的數字或 0%。"""
    port = live_server
    out = _read_chart(port, "DOES_NOT_EXIST_SESSION")
    assert not out["errors"], f"page errors: {out['errors']}"
    assert out["risk"] in ("\u2014", ""), f"stale/fake risk after an unknown session: {out['risk']!r}"
