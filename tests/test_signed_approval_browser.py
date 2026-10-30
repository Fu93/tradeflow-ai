"""Real browser signed-approval workflow. Fictional suppliers; no mail sent."""

import asyncio
import hashlib
import json

import pytest

from src.desk.approval_signing import generate_keypair, sign_approval
from tests.test_desk_user_journeys import KEY, desk_server as _desk_server, opened_page, run_browser

desk_server = _desk_server
PRIVATE, PUBLIC = generate_keypair()
pytestmark = pytest.mark.parametrize(
    "desk_server",
    [
        {
            "TRADEFLOW_REQUIRE_SIGNED_APPROVAL": "1",
            "TRADEFLOW_APPROVAL_PUBKEY": PUBLIC,
        }
    ],
    indirect=True,
    scope="module",
)


async def quoted_page(browser, base):
    page = await opened_page(browser, base)
    await page.fill("#paste-text", "单价 28.00 元。")
    await page.click('#paste-form button[type="submit"]')
    await page.get_by_role("button", name="Accept", exact=True).click()
    await page.locator('[data-action="download-approval-payload"]').first.wait_for()
    page.on("dialog", lambda dialog: dialog.accept())
    return page


async def snapshot(page, base):
    deal = await page.evaluate("sessionStorage.getItem('tfDeskDeal')")
    response = await page.request.get(base + "/api/v1/desk/deals/" + deal, headers={"X-API-Key": KEY})
    assert response.ok
    return await response.json()


async def downloaded_payload(page, folder):
    async with page.expect_download() as pending:
        await page.locator('[data-action="download-approval-payload"]').first.click()
    download = await pending.value
    path = folder / download.suggested_filename
    await download.save_as(path)
    return json.loads(path.read_text())


async def upload(page, content):
    await page.locator('[data-role="approval-signature"]').first.set_input_files(
        {
            "name": "signature.json",
            "mimeType": "application/json",
            "buffer": content,
        }
    )


def test_download_sign_locally_and_import_approval(desk_server, tmp_path):
    async def scenario(browser, base):
        page = await quoted_page(browser, base)
        payload = await downloaded_payload(page, tmp_path)
        current = await snapshot(page, base)
        draft = next(row for row in current["drafts"] if row["id"] == payload["draft_id"])
        assert set(payload) == {"deal_id", "draft_id", "body_sha256"}
        assert payload["body_sha256"] == hashlib.sha256(draft["body"].encode()).hexdigest()
        block = sign_approval(PRIVATE, **payload)
        async with page.expect_response("**/drafts/*/send") as response:
            await upload(page, json.dumps(block).encode())
        receipt = await response.value
        assert receipt.status == 200
        assert (await receipt.json())["transmitted"] == 0
        assert PRIVATE not in receipt.request.post_data
        await page.get_by_text("approved here, not transmitted", exact=False).first.wait_for()

    asyncio.run(run_browser(scenario, desk_server))


@pytest.mark.parametrize("bad", ["private-key", "invalid-json", "oversized"])
def test_private_key_and_invalid_upload_never_reach_server(desk_server, bad):
    async def scenario(browser, base):
        page = await quoted_page(browser, base)
        sent = []
        page.on("request", lambda request: sent.append(request.url) if request.url.endswith("/send") else None)
        content = (
            json.dumps({"private_key": PRIVATE, "public_key": PUBLIC}).encode()
            if bad == "private-key"
            else (b"{" if bad == "invalid-json" else b" " * 4097)
        )
        await upload(page, content)
        await page.get_by_text("Choose the signature JSON", exact=False).wait_for()
        assert sent == []
        assert not any(row["status"] == "approved" for row in (await snapshot(page, base))["drafts"])

    asyncio.run(run_browser(scenario, desk_server))


def test_old_signature_cannot_approve_changed_draft(desk_server, tmp_path):
    async def scenario(browser, base):
        page = await quoted_page(browser, base)
        old = await downloaded_payload(page, tmp_path)
        signed = sign_approval(PRIVATE, **old)
        await page.fill("#paste-text", "单价 27.50 元。")
        await page.click('#paste-form button[type="submit"]')
        await page.get_by_role("button", name="Accept", exact=True).click()
        await page.wait_for_function("() => !document.querySelector('#review [data-confirm-value]')")
        async with page.expect_response("**/drafts/*/send") as response:
            await upload(page, json.dumps(signed).encode())
        assert (await response.value).status == 422
        await page.get_by_text("approval signature does not verify", exact=False).wait_for()
        assert not any(row["status"] == "approved" for row in (await snapshot(page, base))["drafts"])

    asyncio.run(run_browser(scenario, desk_server))


def test_leaving_deal_while_file_loads_cancels_approval(desk_server, tmp_path):
    async def scenario(browser, base):
        page = await quoted_page(browser, base)
        payload = await downloaded_payload(page, tmp_path)
        sent = []
        page.on("request", lambda request: sent.append(request.url) if request.url.endswith("/send") else None)
        await page.evaluate("""() => {
          const original = File.prototype.text;
          File.prototype.text = function() {
            return new Promise(resolve => { window.releaseSignature = () => original.call(this).then(resolve); });
          };
        }""")
        await upload(page, json.dumps(sign_approval(PRIVATE, **payload)).encode())
        await page.wait_for_function("typeof window.releaseSignature === 'function'")
        await page.click("#new-deal")
        await page.evaluate("window.releaseSignature()")
        await page.wait_for_timeout(250)
        assert not await page.locator("#board").is_visible()
        assert sent == []

    asyncio.run(run_browser(scenario, desk_server))
