"""Headless smoke of the desk a judge actually opens.

Covers the dashboard, the multi-auction headline, a session round, approve,
reject, the circuit-breaker badge, and the simulated supplier page. Layout is
checked for horizontal overflow at the widths named in the README.
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


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    tmp = tempfile.mkdtemp(prefix="tf_smoke_")
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


def _overflow(page) -> int:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def _post_json(page, path: str, body: dict) -> dict:
    return page.evaluate(
        """async ({path, body}) => {
            const res = await fetch(path, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            });
            const text = await res.text();
            let parsed = null;
            try { parsed = JSON.parse(text); } catch (e) { parsed = {"raw": text}; }
            return {status: res.status, body: parsed};
        }""",
        {"path": path, "body": body},
    )


def test_desk_auction_rounds_and_sandbox(live_server):
    port = live_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=60000)
            page.wait_for_selector("#runAuctionBtn")
            page.wait_for_selector("#cbPanel")
            assert page.locator("#cbPct").inner_text().strip() in {"—", "-"}
            assert "Traceback" not in page.content()

            for width in (1280, 768, 390, 360):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(150)
                assert _overflow(page) <= 1, f"horizontal overflow at {width}px before the auction"

            page.set_viewport_size({"width": 1280, "height": 900})
            page.click("#runAuctionBtn")
            page.wait_for_selector("#auctionSummary:not(.hidden)", timeout=120000)
            summary = page.locator("#auctionSummary").inner_text()
            assert "Headline: savings vs first-round best quote" in summary
            assert "Versus target" in summary
            assert "Secondary: versus walk-away ceiling" in summary
            headline_at = summary.index("Headline: savings vs first-round best quote")
            target_at = summary.index("Versus target")
            ceiling_at = summary.index("Secondary: versus walk-away ceiling")
            assert headline_at < target_at < ceiling_at
            # The committed archetype close is above the ¥12 target. The minus stays.
            assert "-" in page.locator("#auctionSummary .is-negative").inner_text()
            assert "Traceback" not in summary

            for width in (1280, 768, 390, 360):
                page.set_viewport_size({"width": width, "height": 1100})
                page.wait_for_timeout(150)
                assert _overflow(page) <= 1, f"horizontal overflow at {width}px after the auction"

            page.set_viewport_size({"width": 1280, "height": 900})
            page.select_option("#presetCategorySelect", "cnc_case")
            page.wait_for_function(
                "() => (document.getElementById('presetStatus') || {}).textContent.includes('DEMO_CNC_CASE_01')",
                timeout=30000,
            )
            opened = _post_json(
                page,
                "/api/v1/sessions/DEMO_CNC_CASE_01/round",
                {
                    "opponent_raw_text": "原料上涨，最低 14.80",
                    "opponent_offer_price": 14.80,
                    "mode": "copilot",
                },
            )
            assert opened["status"] == 200, opened
            assert "Traceback" not in json.dumps(opened["body"])
            page.click("text=Reload")
            page.wait_for_selector(".session-item")
            page.locator(".session-item", has_text="DEMO_CNC_CASE_01").click()
            page.wait_for_function(
                "() => { const t = document.getElementById('decisionCounter').textContent; return t && t !== '—' && t !== '-'; }",
                timeout=15000,
            )
            assert page.locator("#cbBadge").inner_text() == "AWAITING HUMAN DECISION"

            approved = _post_json(
                page,
                "/api/v1/sessions/DEMO_CNC_CASE_01/approve",
                {"action": "approve"},
            )
            assert approved["status"] == 200, approved
            page.click("text=Reload")
            page.locator(".session-item", has_text="DEMO_CNC_CASE_01").click()
            page.wait_for_function(
                "() => document.getElementById('decisionReply').textContent.trim() !== '—'",
                timeout=15000,
            )

            page.select_option("#presetCategorySelect", "battery")
            page.wait_for_function(
                "() => (document.getElementById('presetStatus') || {}).textContent.includes('DEMO_BATTERY_01')",
                timeout=30000,
            )
            priced = _post_json(
                page,
                "/api/v1/sessions/DEMO_BATTERY_01/round",
                {
                    "opponent_raw_text": "价格已是底线",
                    "opponent_offer_price": 80.0,
                    "mode": "copilot",
                },
            )
            assert priced["status"] == 200, priced
            rejected = _post_json(
                page,
                "/api/v1/sessions/DEMO_BATTERY_01/approve",
                {"action": "reject", "tactical_rationale": "smoke reject"},
            )
            assert rejected["status"] == 200, rejected
            assert rejected["body"]["status"] == "rejected"
            page.click("text=Reload")
            page.locator(".session-item", has_text="DEMO_BATTERY_01").click()
            page.wait_for_function(
                "() => document.getElementById('cbBadge').textContent === 'WALK AWAY'",
                timeout=15000,
            )

            page.select_option("#presetCategorySelect", "umbrella")
            page.wait_for_function(
                "() => (document.getElementById('presetStatus') || {}).textContent.includes('DEMO_UMBRELLA_01')",
                timeout=30000,
            )
            tripped = _post_json(
                page,
                "/api/v1/sessions/DEMO_UMBRELLA_01/round",
                {
                    "opponent_raw_text": "一口价 30，不议",
                    "opponent_offer_price": 30.0,
                    "mode": "copilot",
                },
            )
            assert tripped["status"] == 200, tripped
            page.click("text=Reload")
            page.locator(".session-item", has_text="DEMO_UMBRELLA_01").click()
            page.wait_for_function(
                "() => document.getElementById('cbBadge').textContent.includes('CIRCUIT TRIPPED')",
                timeout=15000,
            )
            assert "Traceback" not in page.locator("#cbPanel").inner_text()

            page.wait_for_function(
                "() => document.getElementById('feedVer').textContent === 'API 0.4.0'",
                timeout=15000,
            )
            assert page.locator("#amdLiveToggle").is_visible()
            assert page.locator("#amdLiveToggle").is_checked() is False
            page.click("#sampleQuote1")
            page.wait_for_function(
                "() => document.getElementById('quotePaste').value.includes('SIMULATION')",
                timeout=15000,
            )
            page.click("#readQuoteBtn")
            page.wait_for_function(
                "() => document.getElementById('amdCallout').textContent.includes('Offline fallback')",
                timeout=30000,
            )
            callout = page.locator("#amdCallout").inner_text()
            assert "AMD GPU Cloud" not in callout
            assert page.locator("#amdCallout").evaluate("el => el.classList.contains('is-ok')") is False
            assert "Traceback" not in callout
            assert page.locator("#quoteSampleImg").is_visible()
            assert not errors, errors

            sandbox = browser.new_page(viewport={"width": 390, "height": 844})
            sandbox.goto(
                "file://" + os.path.join(REPO, "extension", "mock_chat.html"),
                wait_until="domcontentloaded",
            )
            before = sandbox.locator("#messages-box .msg-bubble").count()
            sandbox.click("#btn-sim-quote")
            sandbox.wait_for_function(
                "(n) => document.querySelectorAll('#messages-box .msg-bubble').length > n",
                arg=before,
            )
            text = sandbox.locator("#messages-box").inner_text()
            assert "Traceback" not in text
            assert _overflow(sandbox) <= 1
        finally:
            browser.close()
