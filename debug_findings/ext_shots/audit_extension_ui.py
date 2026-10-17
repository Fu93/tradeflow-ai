"""Check the restyled HUD + side panel in a real Chrome extension context.

Run:  python debug_findings/ext_shots/audit_extension_ui.py
Exit: 0 = all checks passed.
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
PORT = 8099

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    log = open(os.path.join(HERE, "_sandbox.log"), "w", encoding="utf-8")
    srv = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
        cwd=EXT, stdout=log, stderr=subprocess.STDOUT,
    )
    time.sleep(1.5)
    profile = tempfile.mkdtemp(prefix="tf_ui_ext_")
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
                print("FATAL: no service worker")
                return 1
            ext_id = sw.url.split("/")[2]

            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{PORT}/mock_chat.html", wait_until="domcontentloaded")
            for _ in range(60):
                if page.locator('[data-tradeflow="overlay"]').count() > 0:
                    break
                time.sleep(0.25)
            page.click("#btn-sim-quote")
            for _ in range(90):
                n = page.evaluate(
                    """() => { const h = document.querySelector('[data-tradeflow="overlay"]');
                        return h && h.shadowRoot ? h.shadowRoot.querySelectorAll('.tf-copy-btn').length : 0; }"""
                )
                if n >= 3:
                    break
                time.sleep(0.5)
            page.wait_for_timeout(800)

            deck = page.evaluate(
                """() => {
                  const h = document.querySelector('[data-tradeflow="overlay"]');
                  const root = h.shadowRoot;
                  const pill = root.querySelector('#tf-hud');
                  const deckEl = root.querySelector('#tf-deck');
                  const card = root.querySelector('.tf-card-body');
                  const btn = root.querySelector('.tf-copy-btn');
                  const cs = (el, props) => { const c = getComputedStyle(el); const o = {};
                    props.forEach(p => o[p] = c[p]); return o; };
                  const r = deckEl.getBoundingClientRect();
                  return {
                    pillBg: cs(pill, ['backgroundColor', 'borderLeftWidth', 'borderLeftColor']),
                    deckBox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                    deckBg: getComputedStyle(deckEl).backgroundColor,
                    deckTop: getComputedStyle(deckEl).borderTopColor,
                    cardFont: cs(card, ['fontSize', 'lineHeight', 'color']),
                    btnFont: cs(btn, ['fontSize', 'backgroundColor']),
                    cards: root.querySelectorAll('.tf-card').length,
                    vw: innerWidth, vh: innerHeight,
                    scrollW: deckEl.scrollWidth, clientW: deckEl.clientWidth,
                  };
                }"""
            )
            print(f"    pill={deck['pillBg']} deck={deck['deckBox']} bg={deck['deckBg']}")
            check("HUD pill uses the paper surface", deck["pillBg"]["backgroundColor"] == "rgb(242, 242, 236)", deck["pillBg"]["backgroundColor"])
            check("HUD keeps a thick status edge", deck["pillBg"]["borderLeftWidth"] == "4px", deck["pillBg"]["borderLeftWidth"])
            check("deck uses paper surface with seal top rule",
                  deck["deckBg"] == "rgb(242, 242, 236)" and deck["deckTop"] == "rgb(163, 58, 42)",
                  f"{deck['deckBg']} / {deck['deckTop']}")
            check("deck fits the viewport",
                  deck["deckBox"]["x"] >= 0 and deck["deckBox"]["y"] >= 0
                  and deck["deckBox"]["x"] + deck["deckBox"]["w"] <= deck["vw"]
                  and deck["deckBox"]["y"] + deck["deckBox"]["h"] <= deck["vh"],
                  str(deck["deckBox"]))
            check("deck has no horizontal scroll", deck["scrollW"] <= deck["clientW"] + 1, f"{deck['scrollW']}>{deck['clientW']}")
            check("three cards rendered", deck["cards"] == 3, str(deck["cards"]))
            check("card body is readable at 11px+", float(deck["cardFont"]["fontSize"].rstrip("px")) >= 11, deck["cardFont"]["fontSize"])

            page.screenshot(
                path=os.path.join(HERE, "01_page_with_hud.png"),
                clip={"x": max(0, deck["vw"] - 480), "y": max(0, deck["vh"] - 720), "width": 480, "height": 720},
            )

            sp = ctx.new_page()
            sp.set_viewport_size({"width": 380, "height": 900})
            sp.on("pageerror", lambda e: errors.append("sidepanel: " + str(e)))
            sp.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="domcontentloaded")
            sp.wait_for_timeout(1500)
            sp.evaluate(
                """async () => {
                  const tabs = await chrome.tabs.query({});
                  const sb = tabs.find(t => (t.url||'').includes('mock_chat'));
                  if (!sb) return null;
                  currentTabId = sb.id;
                  sidepanelPort.postMessage({ type: 'SYNC_ACTIVE_TAB', tabId: sb.id });
                  return sb.id;
                }"""
            )
            sp.wait_for_timeout(3500)
            panel = sp.evaluate(
                """() => {
                  const cs = (sel, props) => { const el = document.querySelector(sel);
                    if (!el) return null; const c = getComputedStyle(el); const o = {};
                    props.forEach(p => o[p] = c[p]); return o; };
                  const doc = document.documentElement;
                  return {
                    body: cs('body', ['backgroundColor', 'fontFamily', 'fontSize']),
                    pill: cs('#status-badge', ['color', 'borderTopColor', 'backgroundImage']),
                    cell: cs('.terms td', ['fontFamily', 'fontSize']),
                    overflow: doc.scrollWidth - doc.clientWidth,
                    status: document.getElementById('status-text').textContent,
                    target: document.getElementById('val-target-price').textContent,
                    risk: document.getElementById('val-breakdown-risk').textContent,
                    rows: document.querySelectorAll('.timeline-turn').length,
                    bars: document.querySelectorAll('.bar-fill').length,
                  };
                }"""
            )
            print(f"    panel={panel}")
            check("side panel surface is paper", panel["body"]["backgroundColor"] == "rgb(223, 226, 219)", panel["body"]["backgroundColor"])
            check("side panel has no horizontal overflow", panel["overflow"] <= 1, str(panel["overflow"]))
            check("panel reports live values", panel["target"] not in ("", "—"), panel["target"])
            check("panel shows a risk reading", panel["risk"] not in ("", "—"), panel["risk"])
            check("timeline row rendered", panel["rows"] >= 1, str(panel["rows"]))
            sp.screenshot(path=os.path.join(HERE, "03_sidepanel.png"), full_page=True)

            check("no uncaught page errors", not errors, "; ".join(errors)[:200])
            ctx.close()
    finally:
        srv.terminate()
        log.close()

    print(f"\nRESULT: {'ALL PASS' if not FAILS else str(len(FAILS)) + ' failure(s)'}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
