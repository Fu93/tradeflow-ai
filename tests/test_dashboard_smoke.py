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
            assert "Savings versus the first-round best quote" in summary
            assert "Versus your target" in summary
            assert "Versus the walk-away ceiling" in summary
            headline_at = summary.index("Savings versus the first-round best quote")
            target_at = summary.index("Versus your target")
            ceiling_at = summary.index("Versus the walk-away ceiling")
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
            page.click("#openSandboxBtn")
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
            assert page.locator("#cbBadge").inner_text() == "WAITING FOR YOUR DECISION"
            assert page.locator("#approveBtn").is_visible()
            assert page.locator("#holdBtn").is_visible()
            assert page.locator("#rejectBtn").is_visible()
            assert page.locator("#beliefEmpty").is_hidden()
            assert int(page.locator("#roundCount").inner_text()) >= 1

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
            page.click("#openSandboxBtn")
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
            page.click("#openSandboxBtn")
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
            assert page.locator("#quoteSampleImg").is_hidden()
            page.click("#sampleQuote1")
            page.wait_for_function(
                "() => document.getElementById('quotePaste').value.includes('SIMULATION')",
                timeout=15000,
            )
            page.wait_for_function(
                "() => { const el = document.getElementById('quoteSampleImg');"
                " return !!el && !el.hidden && el.naturalWidth > 0; }",
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
            page.set_input_files(
                "#quoteUpload",
                os.path.join(REPO, "src", "web", "static", "samples", "quote_01.png"),
            )
            page.wait_for_function(
                "() => { const el = document.getElementById('quoteSampleImg');"
                " return !!el && !el.hidden && el.naturalWidth > 0 && el.src.indexOf('data:') === 0; }",
                timeout=15000,
            )
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


def test_pasted_quotes_compare_unit_and_landed(live_server):
    port = live_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=60000)
            page.fill("#auctionTarget", "12")
            page.fill("#auctionCeiling", "16.2")
            page.fill(
                "#liveQuotes",
                "\n".join([
                    "Alpha, 13.2, 0.4, 0, 0.1",
                    "Beta, 14.4, 0.4, 0, 0.1",
                    "Gamma, 15.1, 0.4, 0, 0.1",
                    "Delta, 16.0, 0.4, 0, 0.1",
                ]),
            )
            page.click("#runAuctionBtn")
            page.wait_for_selector("#auctionSummary:not(.hidden)", timeout=120000)
            summary = page.locator("#auctionSummary").inner_text()
            cards = page.locator("#supplierCards").inner_text()
            assert "Your quotes" in summary
            assert "Simulated suppliers" not in page.locator("#auctionKicker").inner_text()
            assert "Simulated suppliers" not in summary
            assert "simulated suppliers" not in summary
            assert "Savings versus the first-round best quote" in summary
            assert "Versus your target" in summary
            assert "Versus the walk-away ceiling" in summary
            assert "Landed cost" in cards
            assert "Unit price" in cards
            assert "no data" not in cards
            page.locator("#auctionSummary summary").click()
            notes = page.locator("#auctionSummary").inner_text()
            assert "prior-driven" in notes
            assert "P(deal)" in notes
            page.set_viewport_size({"width": 360, "height": 900})
            page.wait_for_timeout(150)
            assert _overflow(page) <= 1

            page.set_viewport_size({"width": 1280, "height": 900})
            page.fill("#liveQuotes", "Alpha, 13.2\nBeta, 14.4\nGamma, 15.1\nDelta, 16.0")
            page.click("#runAuctionBtn")
            page.wait_for_selector("#auctionSummary:not(.hidden)", timeout=120000)
            cards = page.locator("#supplierCards").inner_text()
            summary = page.locator("#auctionSummary").inner_text()
            assert "no data" in cards
            assert "not treated as a zero adder" in summary

            page.fill("#liveQuotes", "Alpha, 13.2, 0.4, 5, 0.1")
            page.click("#runAuctionBtn")
            page.wait_for_function(
                "() => document.getElementById('supplierCards').innerText.includes('fraction of one')",
                timeout=15000,
            )

            page.fill("#riskTolerance", "0.02")
            page.fill(
                "#liveQuotes",
                "\n".join([
                    "Alpha, 13.2, 0.4, 0, 0.1",
                    "Beta, 14.4, 0.4, 0, 0.1",
                ]),
            )
            page.click("#runAuctionBtn")
            page.wait_for_function(
                "() => document.getElementById('supplierCards').innerText.includes('not actionable')",
                timeout=120000,
            )
            blocked = page.locator("#supplierCards").inner_text()
            assert "not actionable" in blocked
            assert "risk-tolerance cap" in blocked
            assert "upper bound" in blocked
            assert "least aggressive" in blocked
            assert page.locator("#auctionSummary details").evaluate("el => el.open") is False
            visible = page.locator("#auctionSummary").inner_text()
            assert "tail saturated at the ceiling" in visible
            assert "CVaR cost" in visible
            assert "CNY" in visible
            assert "1.3035714285714284" not in visible

            page.fill("#riskTolerance", "0.08")
            page.fill(
                "#liveQuotes",
                "\n".join([
                    "Alpha, 13.26, 0, 0, 0",
                    "Beta, 15.60, 0, 0, 0",
                    "Gamma, 15.92, 0, 0, 0",
                    "Delta, 16.80, 0, 0, 0",
                ]),
            )
            page.click("#runAuctionBtn")
            page.wait_for_function(
                "() => document.getElementById('supplierCards').innerText.includes('upper bound')",
                timeout=120000,
            )
            shown = page.evaluate(
                """() => {
                    const card = document.querySelector("#supplierCards .supplier-card");
                    const text = card ? card.innerText : "";
                    const match = text.match(/Risk:\\s*([0-9.]+)% upper bound/);
                    return match ? Number(match[1]) : null;
                }"""
            )
            assert shown is not None and shown >= 8, shown

            page.fill("#riskTolerance", "")
            page.fill(
                "#liveQuotes",
                "\n".join([
                    "Alpha, 34, 1, 0, 0",
                    "Beta, 33, 1.2, 0, 0",
                ]),
            )
            page.click("#runAuctionBtn")
            page.wait_for_selector("#auctionSummary:not(.hidden)", timeout=120000)
            landed = page.locator("#supplierCards").inner_text()
            assert "35.00 CNY" in landed
            assert "34.20 CNY" in landed
            assert "35 CNY" not in landed.replace("35.00 CNY", "")
        finally:
            browser.close()


def test_preset_prefills_the_auction_mandate(live_server):
    port = live_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=60000)
            page.wait_for_function(
                "() => document.getElementById('auctionTarget').value === '12'"
                " && document.getElementById('auctionCeiling').value === '16.2'",
                timeout=15000,
            )
            umbrella = page.locator("#mandatePrice").inner_text()
            assert "12.00 CNY" in umbrella
            assert "16.20 CNY" in umbrella

            page.select_option("#presetCategorySelect", "cnc_case")
            page.wait_for_function(
                "() => document.getElementById('auctionTarget').value === '28'"
                " && document.getElementById('auctionCeiling').value === '36.5'",
                timeout=15000,
            )
            mandate = page.locator("#mandatePrice").inner_text()
            assert "28.00 CNY" in mandate
            assert "36.50 CNY" in mandate

            page.fill("#liveQuotes", "")
            page.click("#runAuctionBtn")
            page.wait_for_selector("#auctionSummary:not(.hidden)", timeout=120000)
            summary = page.locator("#auctionSummary").inner_text()
            assert "Versus your target" in summary
            assert "28.00 CNY" in summary
            assert "36.50 CNY" in summary
            assert "Simulated suppliers" in summary
        finally:
            browser.close()


def test_draft_language_on_the_desk(live_server):
    port = live_server
    english = (
        "Hi, the price is 3.20 USD/pc for 1000 pcs, MOQ 500. "
        "Lead time 30 days, 30% deposit."
    )
    chinese = "最低批發價 14.80 元/把，500個起訂"
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#decisionReply")

            def open_round(session_id, text):
                created = _post_json(
                    page,
                    "/api/v1/sessions",
                    {
                        "session_id": session_id,
                        "product_name": "Widget",
                        "target_price": 12,
                        "ceiling_price": 16.2,
                        "moq": 1000,
                        "max_rounds": 5,
                    },
                )
                assert created["status"] == 200, created
                rounded = _post_json(
                    page,
                    f"/api/v1/sessions/{session_id}/round",
                    {"opponent_raw_text": text, "mode": "copilot"},
                )
                assert rounded["status"] == 200, rounded
                return rounded["body"]

            def show(session_id, english):
                page.click("text=Reload")
                page.locator(".session-item", has_text=session_id).click()
                page.wait_for_function(
                    """({id, english}) => {
                        const label = (document.getElementById('sessionLabel') || {}).textContent || '';
                        const reply = (document.getElementById('decisionReply') || {}).textContent || '';
                        const cjk = /[\\u4e00-\\u9fff]/.test(reply);
                        const matches = english
                          ? reply.includes('Our offer is CNY')
                          : cjk && !reply.includes('Our offer is CNY');
                        return label.includes(id) && matches;
                    }""",
                    arg={"id": session_id, "english": english},
                    timeout=15000,
                )

            english_round = open_round("DRAFT_EN_01", english)
            price = english_round["recommended_offer_price"]
            label = f"CNY {price:.2f}"
            assert label in english_round["reply_text"]
            show("DRAFT_EN_01", True)
            reply = page.locator("#decisionReply")
            assert label in reply.inner_text()
            assert "Our offer is CNY" in reply.inner_text()
            page.click("#holdBtn")
            page.wait_for_function(
                "() => document.getElementById('decisionResult').textContent.includes('still waiting')",
                timeout=15000,
            )
            assert label in reply.inner_text()
            page.click("#approveBtn")
            page.wait_for_function(
                "() => document.getElementById('decisionResult').textContent.includes('Approved')",
                timeout=15000,
            )
            assert label in reply.inner_text()
            assert "Our offer is CNY" in reply.inner_text()
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#decisionReply")
            show("DRAFT_EN_01", True)
            assert label in page.locator("#decisionReply").inner_text()
            assert "Our offer is CNY" in page.locator("#decisionReply").inner_text()

            chinese_round = open_round("DRAFT_ZH_01", chinese)
            assert "Our offer is CNY" not in chinese_round["reply_text"]
            show("DRAFT_ZH_01", False)
            assert "Our offer is CNY" not in page.locator("#decisionReply").inner_text()
            page.click("#approveBtn")
            page.wait_for_function(
                """() => {
                    const result = document.getElementById('decisionResult').textContent || '';
                    const reply = document.getElementById('decisionReply').textContent || '';
                    return result.includes('Approved')
                      && /[\\u4e00-\\u9fff]/.test(reply)
                      && !reply.includes('Our offer is CNY');
                }""",
                timeout=15000,
            )
            assert "Our offer is CNY" not in page.locator("#decisionReply").inner_text()
            page.reload(wait_until="domcontentloaded")
            show("DRAFT_ZH_01", False)
            chinese_reply = page.locator("#decisionReply").inner_text()
            assert "Our offer is CNY" not in chinese_reply
            assert any("\u4e00" <= ch <= "\u9fff" for ch in chinese_reply)
        finally:
            browser.close()


def test_hs_suggestions_are_keyboard_selectable(live_server):
    port = live_server
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            def market(route):
                if "keyword=" in route.request.url:
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=(
                            '{"status":"needs_confirmation","candidates":['
                            '{"hs":"660199","text":"660199 Umbrellas"},'
                            '{"hs":"660110","text":"660110 Garden umbrellas"}]}'
                        ),
                    )
                    return
                route.continue_()

            page.route("**/api/v1/market**", market)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=60000)
            page.fill("#marketQuery", "umbrella")
            page.wait_for_selector("#marketSuggest button", timeout=10000)
            page.locator("#marketQuery").press("ArrowDown")
            page.wait_for_function(
                "() => document.querySelector('#marketSuggest button[aria-selected=\"true\"]')"
                " && document.querySelector('#marketSuggest button[aria-selected=\"true\"]').textContent.includes('660199')",
                timeout=5000,
            )
            page.locator("#marketQuery").press("Enter")
            page.wait_for_function(
                "() => document.getElementById('marketSuggest').hidden"
                " && document.getElementById('marketQuery').value === '660199'",
                timeout=10000,
            )
        finally:
            browser.close()
