"""Real browser regressions for interrupted and recoverable buyer workflows.

Only local, fictional suppliers; model calls, search and SMTP are disabled.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

playwright = pytest.importorskip("playwright.async_api")
ROOT = Path(__file__).resolve().parents[1]
KEY = "journey-test-key"


@pytest.fixture(scope="module")
def desk_server(tmp_path_factory, request):
    folder = tmp_path_factory.mktemp("buyer-journeys")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    for key in ("DESK_SMTP_SEND", "IMAP_HOST", "GMAIL_CLIENT_ID", "TRADEFLOW_APPROVAL_PUBKEY"):
        env.pop(key, None)
    env.update(TRADEFLOW_SKIP_DOTENV="1", TRADEFLOW_ENV="production",
               TRADEFLOW_API_KEY=KEY, TRADEFLOW_CHAIN_KEY="journey-chain-key",
               TRADEFLOW_REQUIRE_SIGNED_APPROVAL="0", TRADEFLOW_LLM_DAILY_CALLS="0",
               TRADEFLOW_WEB_EVIDENCE_OFFLINE="1", TRADEFLOW_MARKET_OFFLINE="1",
               TRADEFLOW_DESK_DB=str(folder / "desk.db"))
    # Opt-in strict approval fixture; existing journeys keep their demo mode.
    for key, value in getattr(request, "param", {}).items():
        if key not in {"TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "TRADEFLOW_APPROVAL_PUBKEY"}:
            raise ValueError("Unsupported fixture override")
        env[key] = value
    base = f"http://127.0.0.1:{port}"
    with (folder / "server.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.app.main:app",
                                 "--host", "127.0.0.1", "--port", str(port)],
                                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1):
                        break
                except (urllib.error.URLError, OSError):
                    if time.monotonic() > deadline:
                        pytest.fail((folder / "server.log").read_text())
                    time.sleep(.1)
            yield base
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


async def opened_page(browser, base, *, mobile=False):
    page = await browser.new_page(viewport={"width": 390 if mobile else 1280, "height": 844})
    await page.goto(base + "/desk")
    await page.click("#sample-deal")
    await page.fill("#api-key", KEY)
    await page.click('#desk-form button[type="submit"]')
    await page.locator("#board:not([hidden])").wait_for()
    return page


async def run_browser(scenario, base):
    async with playwright.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            await scenario(browser, base)
        finally:
            await browser.close()


def test_pending_quote_preserves_next_input_and_cannot_reopen_abandoned_deal(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        arrived, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def hold(route):
            nonlocal count
            count += 1
            response = await route.fetch()
            arrived.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route("**/quotes", hold)
        await page.fill("#paste-text", "单价 27.80 元。")
        await page.click('#paste-form button[type="submit"]')
        await asyncio.wait_for(arrived.wait(), 10)
        # Repeated submit events must not create another quote.
        await page.locator("#paste-form").dispatch_event("submit")
        await page.fill("#paste-text", "下一份报价 26.50 元。")
        release.set()
        await playwright.expect(page.locator('#paste-form button[type=submit]')).to_be_enabled()
        assert await page.input_value("#paste-text") == "下一份报价 26.50 元。"
        assert count == 1
        arrived.clear()
        release.clear()
        await page.click('#paste-form button[type="submit"]')
        await asyncio.wait_for(arrived.wait(), 10)
        await page.click("#new-deal")
        release.set()
        await page.wait_for_timeout(500)
        assert not await page.locator("#board").is_visible()
        assert await page.locator("#desk-form").is_visible()
        assert await page.evaluate("sessionStorage.getItem('tfDeskDeal')") is None
        assert await page.input_value("#paste-text") == ""
    asyncio.run(run_browser(scenario, desk_server))


def test_repeated_open_creates_one_deal(desk_server):
    async def scenario(browser, base):
        page = await browser.new_page()
        await page.goto(base + "/desk")
        await page.click("#sample-deal")
        await page.fill("#api-key", KEY)
        arrived, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def hold(route):
            nonlocal count
            count += 1
            response = await route.fetch()
            arrived.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route("**/api/v1/desk/deals", hold)
        await page.click('#desk-form button[type="submit"]')
        await asyncio.wait_for(arrived.wait(), 10)
        await page.locator("#desk-form").dispatch_event("submit")
        release.set()
        await page.locator("#board:not([hidden])").wait_for()
        assert count == 1
    asyncio.run(run_browser(scenario, desk_server))


def test_key_recovery_resumes_same_deal_and_missing_deal_clears_stale_state(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base, mobile=True)
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        await page.evaluate("sessionStorage.setItem('tfApiKey', 'wrong-key')")
        await page.reload()
        await playwright.expect(page.locator("#connection-status")).to_contain_text("Connection failed")
        assert not await page.locator("#board").is_visible()
        await page.fill("#api-key", KEY)
        await page.click("#retry-connection")
        await page.locator("#board:not([hidden])").wait_for()
        assert await page.evaluate("sessionStorage.getItem('tfDeskDeal')") == original
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        async def missing(route):
            await route.fulfill(status=404, json={"detail": "No such deal"})

        await page.route("**/api/v1/desk/deals/" + original, missing)
        await playwright.expect(page.locator("#form-error")).to_contain_text("not in the desk database", timeout=6000)
        assert not await page.locator("#board").is_visible()
        assert await page.evaluate("sessionStorage.getItem('tfDeskDeal')") is None
    asyncio.run(run_browser(scenario, desk_server))


def test_review_copy_approval_and_reopen_survive_polling(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        await page.fill("#paste-text", "单价 27.80 元。")
        await page.click('#paste-form button[type="submit"]')
        await page.locator('#review [data-confirm-value="27.80"]').wait_for()
        arrived, release = asyncio.Event(), asyncio.Event()
        reviews = 0

        async def hold(route):
            nonlocal reviews
            reviews += 1
            response = await route.fetch()
            arrived.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route("**/review/*", hold)
        await page.get_by_role("button", name="Accept", exact=True).click()
        await asyncio.wait_for(arrived.wait(), 10)
        # A poll must not recreate an enabled Accept button while acceptance waits.
        await page.wait_for_timeout(2300)
        assert await page.get_by_role("button", name="Accept", exact=True).is_disabled()
        release.set()
        area = page.locator("#drafts textarea").first
        await area.wait_for()
        assert reviews == 1
        handle = await area.element_handle()
        await area.evaluate("node => { node.focus(); node.select(); }")
        await page.wait_for_timeout(2400)
        assert await handle.evaluate("node => node.isConnected && node.selectionEnd === node.value.length")
        await page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {
            value: {writeText: async () => {throw Error('denied')}}
        })""")
        await page.get_by_role("button", name="Copy draft", exact=True).first.click()
        await playwright.expect(page.locator('#drafts [role="status"]').first).to_contain_text("press Ctrl+C")
        approve = page.get_by_role("button", name="Approve only (no email)", exact=True).first
        await approve.wait_for()
        dialogs = []

        async def confirm(dialog):
            dialogs.append(dialog.message)
            await dialog.accept()

        page.on("dialog", confirm)
        await approve.click()
        await playwright.expect(page.locator("#drafts")).to_contain_text("approved here, not transmitted")
        assert "Email sending is disabled" in dialogs[0]
        await page.get_by_role("button", name="Record walk-away", exact=True).first.click()
        reopen = page.get_by_role("button", name="Reopen supplier", exact=True)
        await reopen.wait_for()
        await reopen.click()
        await playwright.expect(reopen).to_have_count(0)
        assert await page.get_by_role("button", name="Record continue", exact=True).count() == 1
    asyncio.run(run_browser(scenario, desk_server))


def test_signed_approval_policy_explains_disabled_browser_action(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        await page.fill("#paste-text", "单价 27.80 元。")
        await page.click('#paste-form button[type="submit"]')
        await page.get_by_role("button", name="Accept", exact=True).click()
        await page.locator("#drafts textarea").first.wait_for()

        async def signed(route):
            response = await route.fetch()
            body = await response.json()
            body["send_policy"] = {"signed_approval_required": True, "email_enabled": False}
            await route.fulfill(response=response, json=body)

        await page.route("**/api/v1/desk/deals/" + original, signed)
        await playwright.expect(page.locator("#drafts")).to_contain_text("requires a signed approval", timeout=6000)
        assert await page.locator('[data-action="approve-draft"]').first.is_disabled()
    asyncio.run(run_browser(scenario, desk_server))


def test_delayed_poll_does_not_restore_quote_already_confirmed(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        await page.fill("#paste-text", "单价 27.80 元。")
        await page.click('#paste-form button[type="submit"]')
        await page.get_by_role("button", name="Accept", exact=True).wait_for()
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        arrived, release = asyncio.Event(), asyncio.Event()
        held = False

        async def delayed(route):
            nonlocal held
            if held:
                await route.continue_()
                return
            held = True
            response = await route.fetch()
            arrived.set()
            await release.wait()
            await route.fulfill(response=response)

        await page.route("**/api/v1/desk/deals/" + original, delayed)
        await asyncio.wait_for(arrived.wait(), 6)
        await page.get_by_role("button", name="Accept", exact=True).click()
        await page.locator("#drafts textarea").first.wait_for()
        release.set()
        await page.wait_for_timeout(500)
        assert await page.get_by_role("button", name="Accept", exact=True).count() == 0
        assert await page.locator("#drafts textarea").count() == 1
    asyncio.run(run_browser(scenario, desk_server))


def test_quote_error_retains_input_and_allows_retry_and_prior_reset(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)

        async def unavailable(route):
            await route.fulfill(status=503, json={"detail": "Temporarily unavailable"})

        await page.route("**/quotes", unavailable)
        text = "单价 27.80 元。"
        await page.fill("#paste-text", text)
        await page.click('#paste-form button[type="submit"]')
        await playwright.expect(page.locator("#paste-error")).to_contain_text("Temporarily unavailable")
        assert await page.input_value("#paste-text") == text
        await playwright.expect(page.locator('#paste-form button[type="submit"]')).to_be_enabled()
        await page.unroute("**/quotes", unavailable)
        await page.click('#paste-form button[type="submit"]')
        await page.get_by_role("button", name="Accept", exact=True).click()
        await page.locator("#drafts textarea").first.wait_for()
        await page.locator(".desk-advanced > summary").click()
        await page.fill("#reserve-prior", "0.6")
        await page.fill("#reserve-std", "0.1")
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        url = base + "/api/v1/desk/deals/" + original + "/assumptions"
        async with page.expect_response(url) as response:
            await page.get_by_role("button", name="Save assumptions", exact=True).click()
        saved = await (await response.value).json()
        assert saved["deal"]["reserve_prior_mean"] == .6
        await page.fill("#reserve-prior", "")
        await page.fill("#reserve-std", "")
        async with page.expect_response(url) as response:
            await page.get_by_role("button", name="Save assumptions", exact=True).click()
        reset = await (await response.value).json()
        assert reset["deal"]["reserve_prior_mean"] is None
        assert reset["deal"]["reserve_prior_std"] is None
        assert await page.locator("#board-error").inner_text() == ""
    asyncio.run(run_browser(scenario, desk_server))


def test_resuming_deal_preserves_saved_simulation_policy(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        await page.locator(".desk-advanced > summary").click()
        await page.locator("#include-unverified").check()
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        url = base + "/api/v1/desk/deals/" + original + "/assumptions"
        async with page.expect_response(url):
            await page.get_by_role("button", name="Save assumptions", exact=True).click()
        await page.reload()
        await page.locator("#board:not([hidden])").wait_for()
        assert await page.locator("#include-unverified").is_checked()
        await page.locator(".desk-advanced > summary").click()
        async with page.expect_response(url) as response:
            await page.get_by_role("button", name="Save assumptions", exact=True).click()
        saved = await (await response.value).json()
        assert saved["deal"]["include_unverified"] is True
    asyncio.run(run_browser(scenario, desk_server))


def test_typed_price_and_words_keep_focus_across_polls_then_confirm(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)
        await page.fill("#paste-text", "老板，这款伞 28塊5 一把，起订 1000 把。")
        await page.click('#paste-form button[type="submit"]')
        await page.locator("#review .desk-typed > summary").click()
        amount = page.get_by_role("spinbutton", name="Typed unit price", exact=True)
        words = page.get_by_role("textbox", name="Words in the message the price comes from", exact=True)
        await amount.fill("28.5")
        await words.fill("28塊5")
        handle = await words.element_handle()
        await page.wait_for_timeout(2400)
        assert await amount.input_value() == "28.5"
        assert await words.input_value() == "28塊5"
        assert await handle.evaluate("node => node.isConnected && document.activeElement === node")
        await page.get_by_role("button", name="Accept typed price", exact=True).click()
        await page.locator("#drafts textarea").first.wait_for()
        await playwright.expect(page.locator("#cards")).to_contain_text("28.50 CNY")
        assert await page.get_by_role("button", name="Accept typed price", exact=True).count() == 0
    asyncio.run(run_browser(scenario, desk_server))


def test_retry_after_committed_but_lost_response_stores_one_quote(desk_server):
    async def scenario(browser, base):
        page = await opened_page(browser, base)

        async def lost_response(route):
            # The server commits, but the client never receives the result.
            response = await route.fetch()
            assert response.ok
            await route.abort("failed")

        await page.route("**/quotes", lost_response)
        await page.fill("#paste-text", "单价 27.80 元。")
        await page.click('#paste-form button[type="submit"]')
        await playwright.expect(page.locator("#paste-error")).not_to_be_empty()
        assert await page.input_value("#paste-text") == "单价 27.80 元。"
        await page.unroute("**/quotes", lost_response)
        await page.click('#paste-form button[type="submit"]')
        await page.get_by_role("button", name="Accept", exact=True).wait_for()
        original = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
        response = await page.request.get(base + "/api/v1/desk/deals/" + original, headers={"X-API-Key": KEY})
        snapshot = await response.json()
        assert len(snapshot["events"]) == 1
        assert len(snapshot["review"]) == 1
        assert await page.locator("#events .desk-event").count() == 1
    asyncio.run(run_browser(scenario, desk_server))
