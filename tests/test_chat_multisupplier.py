"""Real MV3 capture with synthetic vendor pages and an isolated HTTP desk.

No supplier login, external platform request, SMTP or paid model call.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import time

import pytest

from tests.test_desk_user_journeys import KEY, desk_server as _desk_server

desk_server = _desk_server
playwright = pytest.importorskip("playwright.async_api")
ROOT = Path(__file__).resolve().parents[1]


def chat_html(name):
    return (
        "<!doctype html><html><head><title>" + name + "</title></head><body>"
        '<div class="sim-banner">Simulated fixture; no real supplier</div>'
        '<h1 class="supplier-name">' + name + '</h1><div class="chat-messages"></div></body></html>'
    )


async def launch_profile(pw, folder):
    extension = folder / "extension"
    return await pw.chromium.launch_persistent_context(
        str(folder / "profile"), channel="chromium", headless=True,
        args=["--disable-extensions-except=" + str(extension), "--load-extension=" + str(extension)],
    )


async def worker_for(context):
    return context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker")


async def capture_context(pw, folder, base):
    extension = folder / "extension"
    shutil.copytree(ROOT / "extension", extension)
    (extension / "capture_test.html").write_text("<!doctype html><title>Local extension test controller</title>")
    manifest = json.loads((extension / "manifest.json").read_text())
    manifest["host_permissions"].append(base + "/*")
    csp = manifest["content_security_policy"]["extension_pages"]
    manifest["content_security_policy"]["extension_pages"] = csp.replace("connect-src 'self'", "connect-src 'self' " + base)
    (extension / "manifest.json").write_text(json.dumps(manifest))
    context = await launch_profile(pw, folder)
    worker = await worker_for(context)
    response = await context.request.post(base + "/api/v1/desk/deals", headers={"X-API-Key": KEY}, data={
        "product": "umbrella", "quantity": 1000, "target_price": 24, "ceiling_price": 32,
        "risk_tolerance": .5,
        "suppliers": [{"name": name + " Factory", "address": name.lower() + "@example.test"}
                      for name in ("Alpha", "Beta", "Gamma", "Delta")],
    })
    assert response.ok, await response.text()
    deal = (await response.json())["deal"]["id"]
    await worker.evaluate("value => chrome.storage.local.set(value)", {
        "tfApiBase": base, "tfDeskDeal": deal, "tfApiKey": KEY, "tfAutoImport": True,
    })
    await asyncio.sleep(.1)
    return context, worker, deal


async def page_for(context, url, name, body=None):
    page = await context.new_page()
    origin = url.split("/", 3)[0] + "//" + url.split("/", 3)[2]
    await page.route(origin + "/**", lambda route: route.fulfill(content_type="text/html", body=body or chat_html(name)))
    await page.goto(url)
    await page.locator('[data-tradeflow="overlay"]').wait_for()
    return page


async def add_message(page, text, *, sent=False):
    await page.evaluate("""value => {
      const node = document.createElement('div');
      node.className = value.sent ? 'msg-bubble msg-sent' : 'msg-bubble msg-received';
      node.textContent = value.text;
      document.querySelector('.chat-messages').appendChild(node);
    }""", {"text": text, "sent": sent})


async def snapshot(context, base, deal):
    response = await context.request.get(base + "/api/v1/desk/deals/" + deal, headers={"X-API-Key": KEY})
    assert response.ok
    return await response.json()


async def events(context, base, deal, expected, timeout=6):
    deadline = time.monotonic() + timeout
    current = {}
    while time.monotonic() < deadline:
        current = await snapshot(context, base, deal)
        if len(current.get("events", [])) >= expected:
            break
        await asyncio.sleep(.1)
    names = {row["id"]: row["name"] for row in current["suppliers"]}
    return [(names[row["supplier_id"]], row["raw_text"]) for row in current.get("events", [])]


async def controller(context, worker):
    page = await context.new_page()
    await page.goto("chrome-extension://" + worker.url.split("/")[2] + "/capture_test.html")
    return page


async def wait_queue(worker, count):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        value = await worker.evaluate("async () => (await chrome.storage.local.get('tfAutoQueue')).tfAutoQueue || {}")
        if len(value) == count:
            return value
        await asyncio.sleep(.05)
    raise AssertionError(f"Expected {count} saved captures, got {len(value)}")


def run_case(base, folder, case):
    async def main():
        async with playwright.async_playwright() as pw:
            context, worker, deal = await capture_context(pw, folder, base)
            try:
                await case(context, worker, deal)
            finally:
                await context.close()
    asyncio.run(main())


def test_same_quote_in_two_chat_tabs_is_not_deduplicated_across_suppliers(desk_server, tmp_path):
    async def case(context, worker, deal):
        alpha = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        beta = await page_for(context, desk_server + "/chat?conversation=beta", "Beta Factory")
        await asyncio.gather(add_message(alpha, "单价 28.00 元。"), add_message(beta, "单价 28.00 元。"))
        assert sorted(await events(context, desk_server, deal, 2)) == [
            ("Alpha Factory", "单价 28.00 元。"), ("Beta Factory", "单价 28.00 元。"),
        ]
    run_case(desk_server, tmp_path, case)


def test_chat_switch_during_debounce_keeps_original_supplier(desk_server, tmp_path):
    async def case(context, worker, deal):
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        await add_message(page, "单价 28.50 元。")
        await page.evaluate("""() => {
          document.querySelector('.supplier-name').textContent = 'Beta Factory';
          history.pushState({}, '', '/chat?conversation=beta');
        }""")
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 28.50 元。")]
    run_case(desk_server, tmp_path, case)


PLATFORM_FIXTURES = [
    ("1688", "https://seller.1688.com/chat",
     '<h1 class="supplier-name">Alpha Factory</h1><div class="im-message-box"></div>',
     ".im-message-box", "message-in", "message-out"),
    ("alibaba", "https://seller.alibaba.com/chat",
     '<h1 class="supplier-name">Alpha Factory</h1><div class="im-message-list"></div>',
     ".im-message-list", "msg-received", "msg-sent"),
    ("whatsapp", "https://web.whatsapp.com/",
     '<main id="main"><header><span data-testid="conversation-info-header-chat-title">Alpha Factory</span>'
     '</header><div data-testid="conversation-panel-messages"></div></main>',
     '[data-testid="conversation-panel-messages"]', "message-in", "message-out"),
    ("wechat", "https://wx.qq.com/",
     '<div class="chat_hd"><span class="title_name">Alpha Factory</span></div><div class="chat_bd"></div>',
     ".chat_bd", "message", "message me"),
]


@pytest.mark.parametrize("platform,url,markup,container,incoming,outgoing", PLATFORM_FIXTURES)
def test_platform_fixture_reads_nested_incoming_text_and_ignores_buyer(
    desk_server, tmp_path, platform, url, markup, container, incoming, outgoing,
):
    async def case(context, worker, deal):
        body = '<!doctype html><title>Simulated platform fixture</title><div class="sim-banner">Fixture only</div>' + markup
        page = await page_for(context, url, "Alpha Factory", body=body)
        await page.evaluate("""value => {
          const node = document.createElement('div');
          node.className = value.incoming;
          node.dataset.messageId = 'inbound-1';
          node.innerHTML = '<span class="text"><span></span></span>';
          document.querySelector(value.container).appendChild(node);
        }""", {"incoming": incoming, "container": container})
        await page.evaluate("""value => {
          document.querySelector('[data-message-id="inbound-1"] span span').textContent = '单价 27.80 元。';
          const node = document.createElement('div');
          node.className = value.outgoing;
          node.textContent = '我方预算 20.00 元。';
          document.querySelector(value.container).appendChild(node);
        }""", {"outgoing": outgoing, "container": container})
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 27.80 元。")], platform
        await asyncio.sleep(.5)
        assert len(await events(context, desk_server, deal, 1)) == 1
    run_case(desk_server, tmp_path, case)


def test_replaced_message_container_rebinds_and_burst_preserves_every_message(desk_server, tmp_path):
    async def case(context, worker, deal):
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        await page.evaluate("""() => {
          const wrapper = document.createElement('section');
          wrapper.innerHTML = '<div class="chat-messages"></div>';
          document.querySelector('.chat-messages').replaceWith(wrapper);
        }""")
        await add_message(page, "单价 28.00 元。")
        await add_message(page, "最终单价 27.50 元。")
        assert sorted(await events(context, desk_server, deal, 2)) == [
            ("Alpha Factory", "单价 28.00 元。"), ("Alpha Factory", "最终单价 27.50 元。"),
        ]
    run_case(desk_server, tmp_path, case)


def test_unmapped_chat_bindings_stay_independent_across_tabs(desk_server, tmp_path):
    async def case(context, worker, deal):
        control = await controller(context, worker)
        alpha_url, beta_url = desk_server + "/chat?conversation=unknown-alpha", desk_server + "/chat?conversation=unknown-beta"
        alpha = await page_for(context, alpha_url, "Unknown Shop One")
        beta = await page_for(context, beta_url, "Unknown Shop Two")
        await add_message(alpha, "单价 28.00 元。")
        await add_message(beta, "单价 28.00 元。")
        await wait_queue(worker, 2)
        current = await snapshot(context, desk_server, deal)
        suppliers = {row["name"]: row["id"] for row in current["suppliers"]}
        for page, url, name in ((alpha, alpha_url, "Alpha Factory"), (beta, beta_url, "Beta Factory")):
            await worker.evaluate("value => chrome.storage.local.set({tfDeskSupplier:value})", suppliers[name])
            await page.bring_to_front()
            result = await control.evaluate("url => chrome.runtime.sendMessage({type:'IMPORT_DESK_QUOTE',confirmPage:url})", url)
            assert result["ok"], result
        await wait_queue(worker, 0)
        await asyncio.gather(add_message(alpha, "单价 27.50 元。"), add_message(beta, "单价 27.50 元。"))
        assert sorted(await events(context, desk_server, deal, 4)) == [
            ("Alpha Factory", "单价 27.50 元。"), ("Alpha Factory", "单价 28.00 元。"),
            ("Beta Factory", "单价 27.50 元。"), ("Beta Factory", "单价 28.00 元。"),
        ]
    run_case(desk_server, tmp_path, case)


def test_saved_captures_survive_browser_restart_and_recover_after_key_fix(desk_server, tmp_path):
    async def main():
        async with playwright.async_playwright() as pw:
            context, worker, deal = await capture_context(pw, tmp_path, desk_server)
            await worker.evaluate("() => chrome.storage.local.set({tfApiKey:'wrong-key'})")
            alpha = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
            beta = await page_for(context, desk_server + "/chat?conversation=beta", "Beta Factory")
            await asyncio.gather(add_message(alpha, "单价 28.00 元。"), add_message(beta, "单价 28.00 元。"))
            saved = await wait_queue(worker, 2)
            assert {entry["payload"]["shop"] for entry in saved.values()} == {"Alpha Factory", "Beta Factory"}
            assert "tfApiKey" not in json.dumps(saved)
            await context.close()
            context = await launch_profile(pw, tmp_path)
            try:
                worker = await worker_for(context)
                await wait_queue(worker, 2)
                await worker.evaluate("value => chrome.storage.local.set({tfApiKey:value})", KEY)
                assert sorted(await events(context, desk_server, deal, 2)) == [
                    ("Alpha Factory", "单价 28.00 元。"), ("Beta Factory", "单价 28.00 元。"),
                ]
                await wait_queue(worker, 0)
            finally:
                await context.close()
    asyncio.run(main())


def test_auto_retry_after_committed_response_is_lost_does_not_duplicate(desk_server, tmp_path):
    async def case(context, worker, deal):
        control = await controller(context, worker)
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        intercepted = asyncio.Event()

        async def lose(route):
            response = await route.fetch()
            assert response.ok
            intercepted.set()
            await route.abort("failed")

        pattern = desk_server + "/api/v1/desk/deals/" + deal + "/quotes"
        await context.route(pattern, lose)
        await add_message(page, "单价 28.00 元。")
        await asyncio.wait_for(intercepted.wait(), 8)
        await wait_queue(worker, 1)
        await context.unroute(pattern, lose)
        result = await control.evaluate("() => chrome.runtime.sendMessage({type:'RETRY_CHAT_IMPORTS'})")
        assert result["ok"]
        await wait_queue(worker, 0)
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 28.00 元。")]
    run_case(desk_server, tmp_path, case)


def test_two_browser_windows_and_late_supplier_reply_remain_independent(desk_server, tmp_path):
    async def case(context, worker, deal):
        alpha_url, beta_url = desk_server + "/chat?conversation=alpha", desk_server + "/chat?conversation=beta"
        alpha = await page_for(context, alpha_url, "Alpha Factory")
        beta = await page_for(context, beta_url, "Beta Factory")
        control = await controller(context, worker)
        windows = await control.evaluate("""async urls => {
          const tabs = await chrome.tabs.query({});
          const a = tabs.find(tab => tab.url === urls[0]);
          const b = tabs.find(tab => tab.url === urls[1]);
          const other = await chrome.windows.create({tabId:b.id});
          return [a.windowId, other.id];
        }""", [alpha_url, beta_url])
        assert windows[0] != windows[1]
        await add_message(alpha, "单价 28.00 元。")
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 28.00 元。")]
        current = await snapshot(context, desk_server, deal)
        beta_card = next(row for row in current["suppliers"] if row["name"] == "Beta Factory")
        assert beta_card["conversation"]["state"] == "no_quote"
        assert not beta_card["walked_away"]
        await add_message(beta, "单价 27.50 元。")
        assert sorted(await events(context, desk_server, deal, 2)) == [
            ("Alpha Factory", "单价 28.00 元。"), ("Beta Factory", "单价 27.50 元。"),
        ]
    run_case(desk_server, tmp_path, case)


def test_progressive_message_text_keeps_final_price_and_separate_bubbles(desk_server, tmp_path):
    async def case(context, worker, deal):
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        await page.evaluate("""() => {
          const node = document.createElement('div');
          node.className = 'msg-bubble msg-received';
          node.dataset.messageId = 'progressive-1';
          node.textContent = '单价 2 元。';
          document.querySelector('.chat-messages').appendChild(node);
        }""")
        await page.evaluate("""() => {
          document.querySelector('[data-message-id="progressive-1"]').textContent = '单价 28.00 元。';
        }""")
        await add_message(page, "最终单价 27.50 元。")
        assert sorted(await events(context, desk_server, deal, 2)) == [
            ("Alpha Factory", "单价 28.00 元。"), ("Alpha Factory", "最终单价 27.50 元。"),
        ]
        await asyncio.sleep(.5)
        assert len(await events(context, desk_server, deal, 2)) == 2
    run_case(desk_server, tmp_path, case)


def test_manual_reimport_and_auto_capture_share_platform_message_identity(desk_server, tmp_path):
    async def case(context, worker, deal):
        control = await controller(context, worker)
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        await page.evaluate("""() => {
          const node = document.createElement('div');
          node.className = 'msg-bubble msg-received';
          node.dataset.messageId = 'source-message-1';
          node.textContent = '单价 28.00 元。';
          document.querySelector('.chat-messages').appendChild(node);
        }""")
        await events(context, desk_server, deal, 1)
        await page.bring_to_front()
        result = await control.evaluate("() => chrome.runtime.sendMessage({type:'IMPORT_DESK_QUOTE'})")
        assert result["ok"] and result["duplicate"]
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 28.00 元。")]
    run_case(desk_server, tmp_path, case)


def test_hidden_previous_chat_cannot_be_routed_to_current_header(desk_server, tmp_path):
    async def case(context, worker, deal):
        page = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        await page.evaluate("""() => {
          const old = document.querySelector('.chat-messages');
          old.id = 'old-chat'; old.hidden = true;
          document.querySelector('.supplier-name').textContent = 'Beta Factory';
          history.pushState({}, '', '/chat?conversation=beta');
          const next = document.createElement('div');
          next.className = 'chat-messages'; next.id = 'new-chat';
          document.body.appendChild(next);
          const late = document.createElement('div');
          late.className = 'msg-bubble msg-received'; late.textContent = '旧供应商 29.00 元。';
          old.appendChild(late);
        }""")
        await page.evaluate("""() => {
          const node = document.createElement('div');
          node.className = 'msg-bubble msg-received'; node.textContent = '单价 27.50 元。';
          document.querySelector('#new-chat').appendChild(node);
        }""")
        assert await events(context, desk_server, deal, 1) == [("Beta Factory", "单价 27.50 元。")]
    run_case(desk_server, tmp_path, case)


def test_slow_network_does_not_delay_persisting_other_captures(desk_server, tmp_path):
    async def case(context, worker, deal):
        alpha = await page_for(context, desk_server + "/chat?conversation=alpha", "Alpha Factory")
        beta = await page_for(context, desk_server + "/chat?conversation=beta", "Beta Factory")
        release, intercepted = asyncio.Event(), asyncio.Event()

        async def hold(route):
            intercepted.set()
            await release.wait()
            await route.continue_()

        pattern = desk_server + "/api/v1/desk/deals/" + deal
        await context.route(pattern, hold)
        try:
            await add_message(alpha, "单价 28.00 元。")
            await asyncio.wait_for(intercepted.wait(), 4)
            await add_message(beta, "单价 27.50 元。")
            await asyncio.wait_for(wait_queue(worker, 2), 4)
        finally:
            release.set()
        assert sorted(await events(context, desk_server, deal, 2)) == [
            ("Alpha Factory", "单价 28.00 元。"), ("Beta Factory", "单价 27.50 元。"),
        ]
    run_case(desk_server, tmp_path, case)


def test_inactive_desk_entries_do_not_starve_current_desk_retries(desk_server, tmp_path):
    async def case(context, worker, deal):
        control = await controller(context, worker)
        await worker.evaluate("""value => {
          const queue = {};
          for (let i = 0; i < 12; i++) queue['old-' + i] = {
            base:value.base, deal:'old-deal', payload:{ok:true,text:'Old capture',url:value.base,shop:'Old Factory'}
          };
          queue.current = {base:value.base,deal:value.deal,payload:{
            ok:true,text:'单价 28.00 元。',url:value.base+'/chat?conversation=alpha',shop:'Alpha Factory',simulated:true
          }};
          return chrome.storage.local.set({tfAutoQueue:queue});
        }""", {"base": desk_server, "deal": deal})
        result = await control.evaluate("() => chrome.runtime.sendMessage({type:'RETRY_CHAT_IMPORTS'})")
        assert result["ok"]
        assert await events(context, desk_server, deal, 1) == [("Alpha Factory", "单价 28.00 元。")]
        saved = await wait_queue(worker, 12)
        assert all(entry["deal"] == "old-deal" for entry in saved.values())
    run_case(desk_server, tmp_path, case)
