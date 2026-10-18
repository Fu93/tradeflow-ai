"""Capture what the Chrome extension actually looks like at runtime.

Launches real Chromium with the unpacked extension (same recipe as
tests/test_extension_e2e.py), drives a supplier quote, and saves screenshots of
the injected HUD and the side panel to this folder.

    python debug_findings/ext_shots/capture_shots.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EXT = os.path.join(REPO, "extension")
SANDBOX_PORT = 8099


def main() -> int:
    log = open(os.path.join(HERE, "_sandbox.log"), "w", encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SANDBOX_PORT), "--bind", "127.0.0.1"],
        cwd=EXT, stdout=log, stderr=subprocess.STDOUT,
    )
    time.sleep(1.5)
    profile = tempfile.mkdtemp(prefix="tf_shots_")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                profile,
                headless=False,
                args=[
                    f"--disable-extensions-except={EXT}",
                    f"--load-extension={EXT}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport={"width": 1500, "height": 950},
            )
            sw = None
            for _ in range(80):
                if ctx.service_workers:
                    sw = ctx.service_workers[0]
                    break
                time.sleep(0.25)
            if sw is None:
                print("FAIL: no service worker")
                return 1
            ext_id = sw.url.split("/")[2]
            print("extension id:", ext_id)

            page = ctx.new_page()
            page.goto(
                f"http://127.0.0.1:{SANDBOX_PORT}/mock_chat.html",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            # Trigger the first simulated counter-offer so the HUD has cards.
            for _ in range(60):
                if page.locator('[data-tradeflow="overlay"]').count() > 0:
                    break
                time.sleep(0.25)
            page.wait_for_timeout(1500)
            page.click("#btn-sim-quote")
            for _ in range(80):
                n = page.evaluate(
                    """() => {
                        const h = document.querySelector('[data-tradeflow="overlay"]');
                        return h && h.shadowRoot ? h.shadowRoot.querySelectorAll('.tf-copy-btn').length : 0;
                    }"""
                )
                if n >= 3:
                    break
                time.sleep(0.5)
            page.wait_for_timeout(1200)
            page.screenshot(path=os.path.join(HERE, "01_page_with_hud.png"))

            hud = page.locator('[data-tradeflow="overlay"]').first
            try:
                hud.screenshot(path=os.path.join(HERE, "02_hud_only.png"))
            except Exception as exc:  # shadow host box may be zero-sized
                print("hud-only screenshot skipped:", exc)

            sp = ctx.new_page()
            sp.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="domcontentloaded")
            sp.wait_for_timeout(2000)
            sp.evaluate(
                """
                async () => {
                  const tabs = await chrome.tabs.query({});
                  const sb = tabs.find(t => (t.url||'').includes('mock_chat'));
                  if (!sb) return null;
                  currentTabId = sb.id;
                  sidepanelPort.postMessage({ type: 'SYNC_ACTIVE_TAB', tabId: sb.id });
                  return sb.id;
                }
                """
            )
            sp.wait_for_timeout(4000)
            sp.screenshot(path=os.path.join(HERE, "03_sidepanel.png"), full_page=True)
            print("saved shots to", HERE)
            ctx.close()
    finally:
        srv.terminate()
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
