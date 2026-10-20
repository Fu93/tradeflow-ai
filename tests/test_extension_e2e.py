"""Real end-to-end test of the packed/unpacked MV3 extension with Playwright.

Loads the ACTUAL extension/ directory into real Chromium (persistent context, so
MV3 service workers + content scripts run for real), opens the mock sandbox over
http (so host_permissions / match patterns apply exactly as in the demo), and
drives the genuine flow:

    supplier quote -> content script sends the raw message -> service worker
    -> desk ingest -> confirm a token -> desk counter (or a simulated withhold)

This exists because the extension had never been executed. Every assertion below
is on REAL rendered DOM, not on source text.

Usage:  python tests/test_extension_e2e.py
Exit:   0 = all checks passed, 1 = at least one failure
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

# Playwright is only needed to RUN this script (`python tests/test_extension_e2e.py`,
# see the `extension-e2e` CI job). It must not be a hard import: pytest collects
# this module during `python -m pytest tests`, and a missing browser package made
# the entire suite die with a collection error (exit 2) before a single test ran.
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - plain install without browsers
    sync_playwright = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(REPO, "extension")
BACKEND_PORT = 8000
SANDBOX_PORT = 8099

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, seconds: float = 40.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if port_open(port):
            return True
        time.sleep(0.3)
    return False


def start_backend(api_key=None):
    """Start the FastAPI backend exactly as documented, isolated store."""
    tmp = tempfile.mkdtemp(prefix="tf_ext_backend_")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = REPO
    env.setdefault("TRADEFLOW_CHAIN_KEY", "test-chain-key")
    env["TRADEFLOW_DB_PATH"] = os.path.join(tmp, "sessions.db")
    env["TRADEFLOW_STORE_DIR"] = os.path.join(tmp, "sessions_json")
    env.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
    if api_key:
        env["TRADEFLOW_API_KEY"] = api_key
        env["TRADEFLOW_DESK_DB"] = os.path.join(tmp, "desk.db")
        env["DESK_N_SAMPLES"] = "40"
        env["DESK_N_GRID"] = "8"
    else:
        env.pop("TRADEFLOW_API_KEY", None)
    log = open(os.path.join(tmp, "backend.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.app.main"],
        cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    return proc, tmp, log


def create_copilot_deal(api_key: str) -> tuple[str, str]:
    """Four-supplier desk deal whose first name is the mock chat counterparty."""
    import urllib.request

    payload = json.dumps({
        "product": "umbrella",
        "quantity": 1000,
        "target_price": 12,
        "ceiling_price": 16.2,
        "risk_tolerance": 0.5,
        "suppliers": [
            {"name": "浙江頂尖雨具製造實業有限公司", "address": "ningbo@example.test"},
            {"name": "Wenzhou", "address": "wenzhou@example.test"},
            {"name": "Yiwu", "address": "yiwu@example.test"},
            {"name": "Foshan", "address": "foshan@example.test"},
        ],
    }).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/v1/desk/deals",
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        created = json.loads(response.read().decode())
    supplier = next(row for row in created["suppliers"] if "雨具" in row["name"])
    return created["deal"]["id"], supplier["id"]


def start_sandbox():
    """Serve extension/ over http so match patterns apply like the real demo."""
    log = open(os.path.join(EXT_DIR, "_sandbox_server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SANDBOX_PORT), "--bind", "127.0.0.1"],
        cwd=EXT_DIR, stdout=log, stderr=subprocess.STDOUT,
    )
    return proc, log


def main() -> int:
    print("=" * 78)
    print("TradeFlow extension -- REAL browser E2E (Playwright + Chromium)")
    print("=" * 78)

    if sync_playwright is None:
        print("\nFATAL: playwright is not installed, so this E2E cannot run.")
        print("       python -m pip install playwright")
        print("       python -m playwright install --with-deps chromium")
        return 1

    backend = sandbox = None
    user_data = tempfile.mkdtemp(prefix="tf_ext_profile_")
    backend_tmp = None
    backend_log = sandbox_log = None

    try:
        # ---------------------------------------------------------------- setup
        print("\n[0] Environment")
        if port_open(BACKEND_PORT):
            print(f"    backend already on :{BACKEND_PORT} -- reusing it")
        else:
            backend, backend_tmp, backend_log = start_backend(api_key="copilot-extension-e2e-key")
            if not wait_for_port(BACKEND_PORT):
                print("    FATAL: backend did not come up")
                return 1
            print(f"    backend started on :{BACKEND_PORT}")

        copilot_key = "copilot-extension-e2e-key"
        copilot_deal = ""
        copilot_supplier = ""
        if backend is not None:
            copilot_deal, copilot_supplier = create_copilot_deal(copilot_key)
            print(f"    desk deal {copilot_deal} supplier {copilot_supplier}")

        sandbox, sandbox_log = start_sandbox()
        if not wait_for_port(SANDBOX_PORT):
            print("    FATAL: sandbox server did not come up")
            return 1
        print(f"    sandbox served at http://127.0.0.1:{SANDBOX_PORT}/mock_chat.html")

        with sync_playwright() as p:
            # load-extension works in headless=False (headed) reliably for MV3.
            ctx = p.chromium.launch_persistent_context(
                user_data,
                headless=False,
                args=[
                    f"--disable-extensions-except={EXT_DIR}",
                    f"--load-extension={EXT_DIR}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                viewport={"width": 1400, "height": 900},
            )
            try:
                # ------------------------------------------------ extension load
                print("\n[1] Extension loads")
                # service_workers is a local list filled by the serviceworker
                # event. time.sleep does not pump that event, so a poll loop
                # can miss a worker that registered after launch returned.
                sw = ctx.service_workers[0] if ctx.service_workers else None
                if sw is None:
                    try:
                        sw = ctx.wait_for_event("serviceworker", timeout=15000)
                    except Exception:
                        sw = None
                if not check("MV3 service worker registered", sw is not None):
                    return 1
                ext_id = sw.url.split("/")[2]
                print(f"    extension id: {ext_id}")

                # Capture service-worker console for diagnostics
                sw_errors: list[str] = []
                sw.on("console", lambda m: sw_errors.append(f"{m.type}: {m.text}"))

                # ------------------------------------------------------ open page
                print("\n[2] Mock sandbox page + content script injection")
                page = ctx.new_page()
                console: list[str] = []
                page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
                page.on("pageerror", lambda e: console.append(f"PAGEERROR: {e}"))

                url = f"http://127.0.0.1:{SANDBOX_PORT}/mock_chat.html"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Content script injects the HUD host (a plain <div> + open shadow
                # root; it is NOT a custom element because customElements is null
                # in the MV3 isolated world -- see the P0 note in content.js).
                host_sel = '[data-tradeflow="overlay"]'
                hud = None
                for _ in range(60):
                    if page.locator(host_sel).count() > 0:
                        hud = page.locator(host_sel).first
                        break
                    time.sleep(0.25)
                if not check("HUD injected into page", hud is not None):
                    print("    page console:\n      " + "\n      ".join(console[-25:]))
                    return 1

                # NOTE: el._state is an expando set inside the content script's
                # ISOLATED world; Playwright's evaluate runs in the MAIN world and
                # cannot see isolated-world expando properties. So assert on
                # RENDERED DOM (what a judge actually sees) instead.
                badge0 = page.evaluate(
                    """() => {
                        const h = document.querySelector('[data-tradeflow="overlay"]');
                        if (!h || !h.shadowRoot) return '';
                        const b = h.shadowRoot.querySelector('#tf-badge');
                        return b ? b.innerText.trim() : '';
                    }"""
                )
                print(f"    HUD badge at rest: {badge0!r}")
                check("HUD renders and is active at rest", "RADAR ON" in badge0.upper(), f"badge={badge0!r}")

                toggle_label = page.evaluate(
                    """() => {
                        const h = document.querySelector('[data-tradeflow="overlay"]');
                        if (!h || !h.shadowRoot) return '';
                        const t = h.shadowRoot.querySelector('#tf-toggle-btn');
                        return t ? t.innerText.trim() : '';
                    }"""
                )
                check("overlay starts active (toggle offers Pause)", toggle_label.lower() == "pause",
                      f"toggle={toggle_label!r}")

                mode_label = page.evaluate(
                    """() => {
                        const h = document.querySelector('[data-tradeflow="overlay"]');
                        if (!h || !h.shadowRoot) return '';
                        const m = h.shadowRoot.querySelector('#tf-mode-btn');
                        return m ? m.innerText.trim() : '';
                    }"""
                )
                check("default mode is copilot (human-in-the-loop)", mode_label == "Copilot Mode",
                      f"mode button={mode_label!r}")

                # session_id whitelist: the tab session the SW registered must be
                # backend-legal even though the mock supplier name is CJK.
                # Registration is async after the HUD paints, so wait briefly.
                sid = ""
                session_deadline = time.time() + 8
                while time.time() < session_deadline:
                    sw_session = sw.evaluate(
                        """async () => {
                            try {
                              const r = await chrome.runtime.sendMessage({type:'GET_ALL_SESSIONS'});
                              if (r && r.sessions && r.sessions.length) return r.sessions[0];
                            } catch (e) {}
                            try {
                              const all = await chrome.storage.session.get(null);
                              const keys = Object.keys(all).filter(k => k.startsWith('tab_'));
                              if (keys.length) return all[keys[0]];
                            } catch (e) {}
                            return null;
                        }"""
                    )
                    sid = (sw_session or {}).get("sessionId") or ""
                    if sid:
                        break
                    page.wait_for_timeout(200)
                import re as _re
                print(f"    registered session_id: {sid!r}")
                check(
                    "session_id registered by the SW matches backend whitelist",
                    bool(_re.fullmatch(r"[A-Za-z0-9._-]{1,128}", sid)),
                    f"session_id={sid!r} (CJK supplier name must NOT leak through)",
                )

                # ------------------------------------------------- supplier quote
                print("\n[3] Supplier quote -> desk confirm, not a scraped counter")
                if copilot_deal and sw is not None:
                    sw.evaluate(
                        """async ({key, deal, supplier}) => {
                            await chrome.storage.local.set({
                              tfApiKey: key, tfDeskDeal: deal, tfDeskSupplier: supplier,
                            });
                        }""",
                        {"key": copilot_key, "deal": copilot_deal, "supplier": copilot_supplier},
                    )
                page.click("#btn-sim-quote")
                status_text = ""
                cards = 0
                confirms = 0
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        snap = page.evaluate(
                            """() => {
                                const el = document.querySelector('[data-tradeflow="overlay"]');
                                if (!el || !el.shadowRoot) return { status: '', cards: 0, confirms: 0 };
                                const t = el.shadowRoot.querySelector('.tf-title');
                                const s = el.shadowRoot.querySelector('.tf-subtext');
                                return {
                                    status: (t ? t.innerText : '') + ' || ' + (s ? s.innerText : ''),
                                    cards: el.shadowRoot.querySelectorAll('.tf-copy-btn').length,
                                    confirms: el.shadowRoot.querySelectorAll('.tf-confirm-btn').length,
                                };
                            }"""
                        )
                        status_text = snap.get("status") or ""
                        cards = int(snap.get("cards") or 0)
                        confirms = int(snap.get("confirms") or 0)
                    except Exception:
                        pass
                    if confirms >= 1 and "SOLVING" not in status_text.upper():
                        break
                    time.sleep(0.5)

                print(f"    HUD status: {status_text.strip()[:160]}")
                check("engine reached a confirm step (not stuck solving, not HTTP 404)",
                      "SOLVING" not in status_text.upper() and "404" not in status_text,
                      status_text.strip()[:120])
                check("scraped price did not render decision cards",
                      cards == 0, f"copy buttons found = {cards}")
                check("confirm buttons offer a token from the message",
                      confirms >= 1, f"confirm buttons = {confirms}")
                if confirms >= 1:
                    page.locator(".tf-confirm-btn").first.click()
                    confirmed = ""
                    deadline = time.time() + 60
                    while time.time() < deadline:
                        confirmed = page.evaluate(
                            """() => {
                                const el = document.querySelector('[data-tradeflow="overlay"]');
                                if (!el || !el.shadowRoot) return '';
                                const t = el.shadowRoot.querySelector('.tf-title');
                                const s = el.shadowRoot.querySelector('.tf-subtext');
                                return (t ? t.innerText : '') + ' || ' + (s ? s.innerText : '');
                            }"""
                        ) or ""
                        if "CONFIRM A PRICE" not in confirmed.upper() and "SOLVING" not in confirmed.upper():
                            break
                        time.sleep(0.4)
                    priced = page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-tradeflow="overlay"]');
                            if (!el || !el.shadowRoot) return 0;
                            return el.shadowRoot.querySelectorAll('.tf-copy-btn').length;
                        }"""
                    )
                    print(f"    after confirm: {confirmed.strip()[:160]}")
                    check("confirm does not invent a live counter on the simulated page",
                          "404" not in confirmed and int(priced or 0) == 0
                          and "SIMULATED" in confirmed.upper(),
                          confirmed.strip()[:120])

                # ------------------------------------------------------ captcha
                print("\n[4] Captcha sentinel (compliance gate)")
                page.click("#btn-sim-captcha")
                page.wait_for_timeout(300)
                # a quote during captcha must be frozen
                page.evaluate("""() => {
                    const box = document.getElementById('messages-box');
                    const b = document.createElement('div');
                    b.className = 'msg-bubble msg-received';
                    b.innerText = '這批貨單價報價為 ¥14.90 元';
                    box.appendChild(b);
                }""")
                captcha_status = ""
                for _ in range(40):
                    captcha_status = page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-tradeflow="overlay"]');
                            if (!el || !el.shadowRoot) return '';
                            const t = el.shadowRoot.querySelector('.tf-title');
                            return t ? t.innerText : '';
                        }"""
                    )
                    if "CAPTCHA" in captcha_status.upper():
                        break
                    time.sleep(0.25)
                check("HUD freezes to CAPTCHA DETECTED", "CAPTCHA" in captcha_status.upper(), captcha_status[:60])

                # Solve it, then send a NEW inbound quote. Recovery is checked on
                # the next inbound message (content.js handleInboundQuote clears
                # captchaPaused/circuitTripped in that path) -- the extension
                # deliberately does not poll the modal, so a new message is the
                # real-world trigger. This also covers the historical
                # permanent-freeze regression (HUD stuck on CAPTCHA forever).
                page.click("#nc_1_n1z")
                page.wait_for_timeout(1200)  # let the modal close
                modal_visible = page.evaluate(
                    "() => { const m = document.getElementById('baxia-dialog-content');"
                    " return !!m && getComputedStyle(m).display !== 'none'; }"
                )
                check("captcha modal closes after human slider interaction", not modal_visible,
                      f"modal visible={modal_visible}")

                page.evaluate("""() => {
                    const box = document.getElementById('messages-box');
                    const b = document.createElement('div');
                    b.className = 'msg-bubble msg-received';
                    b.innerText = '好商量，這批貨單價報價為 ¥14.30 元，可以再談。';
                    box.appendChild(b);
                }""")
                recovered = ""
                recovered_cards = 0
                for _ in range(120):
                    recovered = page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-tradeflow="overlay"]');
                            if (!el || !el.shadowRoot) return '';
                            const t = el.shadowRoot.querySelector('.tf-title');
                            return t ? t.innerText : '';
                        }"""
                    )
                    recovered_cards = page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-tradeflow="overlay"]');
                            if (!el || !el.shadowRoot) return 0;
                            return el.shadowRoot.querySelectorAll('.tf-copy-btn').length;
                        }"""
                    )
                    if "CAPTCHA" not in recovered.upper() and "CONFIRM" in recovered.upper():
                        break
                    time.sleep(0.5)
                check("captcha recovery un-freezes the HUD on next quote (no permanent freeze)",
                      "CAPTCHA" not in recovered.upper(), f"status now: {recovered[:70]}")
                check("recovery asks for a confirmation instead of priced cards",
                      "CONFIRM" in recovered.upper() and recovered_cards == 0,
                      f"status={recovered[:70]} cards={recovered_cards}")

                # ---------------------------------------------------- side panel
                print("\n[5] Side panel renders without fabricated numbers")
                sp = ctx.new_page()
                sp_console: list[str] = []
                sp.on("console", lambda m: sp_console.append(f"{m.type}: {m.text}"))
                sp.on("pageerror", lambda e: sp_console.append(f"PAGEERROR: {e}"))
                sp.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="domcontentloaded")
                sp.wait_for_timeout(2500)

                tgt = sp.locator("#val-target-price").inner_text()
                ceil = sp.locator("#val-reserve-ceiling").inner_text()
                mu = sp.locator("#val-posterior-mean").inner_text()
                risk = sp.locator("#val-breakdown-risk").inner_text()
                sid_txt = sp.locator("#session-id-display").inner_text()
                rnd = sp.locator("#round-counter").inner_text()
                print(f"    target={tgt!r} ceiling={ceil!r} mu={mu!r} risk={risk!r} sid={sid_txt!r} round={rnd!r}")

                fabricated = ["live-session-001", "¥12.00", "¥16.20", "1.080", "0.095", "4.8%", "Round 1 / 5"]
                leaked = [f for f in fabricated if f in {tgt, ceil, mu, risk, sid_txt, rnd}]
                check("no fabricated demo numbers on side panel",
                      not leaked, f"leaked={leaked}" if leaked else "all values are real or '--'")
                check("no uncaught page errors on side panel",
                      not any("PAGEERROR" in c for c in sp_console),
                      "; ".join(c for c in sp_console if "PAGEERROR" in c)[:160])

                # ------------------------------------------- live data binding
                # Bind the panel to the real sandbox tab the way a user's panel
                # does, and assert it renders LIVE engine values (this exercises
                # renderEngineData end to end).
                print("\n[5b] Side panel binds to the live tab session")
                bind = sp.evaluate(
                    """
                    async () => {
                      const tabs = await chrome.tabs.query({});
                      const sb = tabs.find(t => (t.url||'').includes('mock_chat'));
                      if (!sb) return null;
                      currentTabId = sb.id;
                      sidepanelPort.postMessage({ type:'SYNC_ACTIVE_TAB', tabId: sb.id });
                      return sb.id;
                    }
                    """
                )
                check("side panel bound to the sandbox tab", bind is not None, f"tabId={bind}")

                live = {}
                for _ in range(60):
                    live = sp.evaluate(
                        """() => ({
                            target: document.getElementById('val-target-price').innerText,
                            ceiling: document.getElementById('val-reserve-ceiling').innerText,
                            mu: document.getElementById('val-posterior-mean').innerText,
                            risk: document.getElementById('val-breakdown-risk').innerText,
                            round: document.getElementById('round-counter').innerText,
                            status: document.getElementById('status-text').innerText,
                            timeline: document.querySelectorAll('.timeline-turn').length,
                        })"""
                    )
                    if live.get("target") not in ("--", "", None):
                        break
                    time.sleep(0.4)
                print("    live panel: " + json.dumps(live, ensure_ascii=False))
                check("panel shows the desk target, not the scraped quote",
                      "12" in str(live.get("target") or "") and "14.80" not in str(live.get("target") or ""),
                      f"target={live.get('target')!r}")
                check("panel does not invent a round from a scraped price",
                      "404" not in str(live.get("status") or ""),
                      f"round={live.get('round')!r} status={live.get('status')!r}")
                check("panel does not invent a turn timeline from /round",
                      int(live.get("timeline") or 0) == 0,
                      f"rows={live.get('timeline')}")
                check("panel has no runtime errors while rendering live data",
                      not any("PAGEERROR" in c for c in sp_console),
                      "; ".join(c for c in sp_console if "PAGEERROR" in c)[:200])

                # ------------------------------------------------ source + gate
                print("\n[5c] Source badge, approval gate, savings")
                gate = sp.evaluate(
                    """() => ({
                        badge: document.getElementById('source-badge-text').innerText.trim(),
                        state: document.getElementById('approval-state').innerText.trim(),
                        action: document.getElementById('approval-action').innerText.trim(),
                        price: document.getElementById('approval-price').innerText.trim(),
                        evidence: document.getElementById('approval-evidence').innerText.trim(),
                        savings: document.getElementById('val-savings').innerText.trim(),
                        note: document.getElementById('gate-send-note').innerText,
                        url: (typeof tabMeta !== 'undefined' && tabMeta.url) || '',
                    })"""
                )
                print("    gate: " + json.dumps(gate, ensure_ascii=False)[:400])
                allowed_badges = {"AMD live", "Offline fallback", "Simulator", "Unreachable"}
                check("source badge is one of the four honesty labels",
                      gate.get("badge") in allowed_badges, f"badge={gate.get('badge')!r}")
                check("sandbox tab is labelled Simulator",
                      gate.get("badge") == "Simulator",
                      f"badge={gate.get('badge')!r} url={gate.get('url')!r}")
                check("approval gate stays empty until a desk counter exists",
                      gate.get("state") in ("--", "", None), f"state={gate.get('state')!r}")
                check("approval action is not invented from a scraped price",
                      gate.get("action") in ("--", "", None),
                      f"action={gate.get('action')!r}")
                check("approval price is not invented from a scraped price",
                      gate.get("price") in ("--", "", None),
                      f"price={gate.get('price')!r}")
                check("approval evidence is not invented from a scraped price",
                      gate.get("evidence") in ("--", "", None),
                      f"evidence={str(gate.get('evidence'))[:80]!r}")
                check("gate says the buyer pastes and sends",
                      "you paste and send; TradeFlow never sends" in str(gate.get("note")))
                check("savings stay '--' unless the engine sent a savings field",
                      gate.get("savings") == "--" or "¥" in str(gate.get("savings")) or "%" in str(gate.get("savings")),
                      f"savings={gate.get('savings')!r}")

                states = sp.evaluate(
                    """() => {
                        const live = lastRoundPayload;
                        const saved = live ? JSON.parse(JSON.stringify(live)) : null;
                        const fixture = {
                          pending_human_approval: true,
                          awaiting_human_approval: true,
                          autopilot_state: 'AWAITING_HUMAN_DECISION',
                          recommended_offer_price: 12.4,
                          session: { state: 'AWAITING_HUMAN_DECISION', history: [] },
                          action_object: { authorized_price: 12.4, source_round_id: 'round-a' },
                        };
                        const out = {};
                        const paint = (extra) => {
                          const copy = JSON.parse(JSON.stringify(fixture));
                          Object.assign(copy, extra);
                          if (!copy.session) copy.session = {};
                          if (extra.state) copy.session.state = extra.state;
                          if (extra.source_round_id) {
                            copy.action_object.source_round_id = extra.source_round_id;
                          }
                          renderEngineData(copy);
                          return document.getElementById('approval-state').innerText.trim();
                        };
                        paint({ source_round_id: 'round-a' });
                        out.price = document.getElementById('approval-price').innerText.trim();
                        out.approved = paint({
                          pending_human_approval: false,
                          awaiting_human_approval: false,
                          autopilot_state: 'HUMAN_APPROVED_APPLIED',
                          state: 'HUMAN_APPROVED_APPLIED',
                        });
                        out.approvedPrice = document.getElementById('approval-price').innerText.trim();
                        out.rejected = paint({
                          pending_human_approval: false,
                          awaiting_human_approval: false,
                          autopilot_state: 'WALK_AWAY',
                          state: 'WALK_AWAY',
                        });
                        paint({
                          pending_human_approval: true,
                          source_round_id: 'round-a',
                          autopilot_state: 'AWAITING_HUMAN_DECISION',
                          state: 'AWAITING_HUMAN_DECISION',
                        });
                        paint({
                          pending_human_approval: false,
                          awaiting_human_approval: false,
                          source_round_id: 'round-b',
                          autopilot_state: 'COPILOT_MONITORING',
                          state: 'COPILOT_MONITORING',
                        });
                        out.superseded = document.getElementById('approval-state').innerText.trim();
                        if (saved) renderEngineData(saved);
                        out.restored = document.getElementById('approval-state').innerText.trim();
                        out.restoredPrice = document.getElementById('approval-price').innerText.trim();
                        return out;
                    }"""
                )
                print("    states: " + json.dumps(states, ensure_ascii=False))
                check("approval card can show Approved",
                      states.get("approved") == "Approved", f"got={states.get('approved')!r}")
                check("approved price stays the engine figure",
                      states.get("approvedPrice") == states.get("price"),
                      f"{states.get('approvedPrice')!r} vs {states.get('price')!r}")
                check("approval card can show Rejected",
                      states.get("rejected") == "Rejected", f"got={states.get('rejected')!r}")
                check("approval card can show Superseded",
                      states.get("superseded") == "Superseded", f"got={states.get('superseded')!r}")
                check("live gate is restored after the state check",
                      states.get("restored") in ("--", "", None)
                      and states.get("restoredPrice") in ("--", "", None),
                      f"state={states.get('restored')!r} price={states.get('restoredPrice')!r}")

                badges = sp.evaluate(
                    """() => {
                        const out = {};
                        const savedUrl = tabMeta.url;
                        const savedHealth = tfHealth;
                        const savedErr = tfHealthError;
                        const savedForce = sourceForced;
                        const savedRound = lastRoundPayload;
                        tabMeta = Object.assign({}, tabMeta, { url: '' });
                        if (lastRoundPayload) {
                          lastRoundPayload = Object.assign({}, lastRoundPayload, { simulated_sample: false });
                          if (lastRoundPayload.amd_live) {
                            lastRoundPayload.amd_live = Object.assign({}, lastRoundPayload.amd_live, { requested: false });
                          }
                        }
                        sourceForced = null;
                        tfHealthError = null;
                        paintSourceBadge();
                        out.offline = document.getElementById('source-badge-text').innerText.trim();
                        tfHealth = { llm: { amd_live_available: true }, offline_demo: false };
                        if (!lastRoundPayload) lastRoundPayload = {};
                        lastRoundPayload.amd_live = { requested: true, decides_price: false };
                        lastRoundPayload.quote_status = 'ok';
                        paintSourceBadge();
                        out.amd = document.getElementById('source-badge-text').innerText.trim();
                        tfHealth = null;
                        tfHealthError = new Error('down');
                        sourceForced = 'unreachable';
                        paintSourceBadge();
                        out.down = document.getElementById('source-badge-text').innerText.trim();
                        tabMeta.url = savedUrl;
                        tfHealth = savedHealth;
                        tfHealthError = savedErr;
                        sourceForced = savedForce;
                        lastRoundPayload = savedRound;
                        paintSourceBadge();
                        out.restored = document.getElementById('source-badge-text').innerText.trim();
                        return out;
                    }"""
                )
                print("    badges: " + json.dumps(badges, ensure_ascii=False))
                check("offline fallback badge is derived when AMD was not requested",
                      badges.get("offline") == "Offline fallback", f"got={badges.get('offline')!r}")
                check("AMD live badge is derived only from live fields",
                      badges.get("amd") == "AMD live", f"got={badges.get('amd')!r}")
                check("unreachable badge is shown when the backend cannot be read",
                      badges.get("down") == "Unreachable", f"got={badges.get('down')!r}")
                check("source badge restores to Simulator",
                      badges.get("restored") == "Simulator", f"got={badges.get('restored')!r}")

                a11y = sp.evaluate(
                    """() => {
                        const body = getComputedStyle(document.body);
                        const btn = document.getElementById('btn-hold').getBoundingClientRect();
                        const refresh = document.getElementById('btn-refresh').getBoundingClientRect();
                        return {
                          font: parseFloat(body.fontSize),
                          btnH: btn.height,
                          refreshH: refresh.height,
                          statusLive: document.getElementById('status-badge').getAttribute('aria-live'),
                          signalLive: document.getElementById('val-last-signal').getAttribute('aria-live'),
                        };
                    }"""
                )
                check("body text is at least 12px", a11y.get("font", 0) >= 12, f"font={a11y.get('font')}")
                check("decision control is at least 32px", a11y.get("btnH", 0) >= 32, f"height={a11y.get('btnH')}")
                check("reload control is at least 32px", a11y.get("refreshH", 0) >= 32, f"height={a11y.get('refreshH')}")
                check("status text is a polite live region", a11y.get("statusLive") == "polite")
                check("signal text is a polite live region", a11y.get("signalLive") == "polite")

                sp.locator("#btn-hold").focus()
                focus_ring = sp.evaluate(
                    """() => {
                        const s = getComputedStyle(document.getElementById('btn-hold'));
                        return { style: s.outlineStyle, width: parseFloat(s.outlineWidth) || 0 };
                    }"""
                )
                check("decision control shows a visible focus ring",
                      focus_ring.get("style") not in ("none", "hidden", "") and focus_ring.get("width", 0) >= 2,
                      f"outline={focus_ring}")

                def contrast_sample(scheme):
                    sp.emulate_media(color_scheme=scheme)
                    sp.wait_for_timeout(50)
                    return sp.evaluate(
                        """() => {
                            const parse = (value) => {
                              const m = String(value).match(/[\\d.]+/g);
                              if (!m || m.length < 3) return null;
                              return m.slice(0, 3).map(Number);
                            };
                            const lin = (channel) => {
                              const s = channel / 255;
                              return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
                            };
                            const ratio = (fg, bg) => {
                              const L = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
                              const a = L(fg);
                              const b = L(bg);
                              const hi = Math.max(a, b);
                              const lo = Math.min(a, b);
                              return (hi + 0.05) / (lo + 0.05);
                            };
                            const opaque = (el) => {
                              let node = el;
                              while (node) {
                                const bg = parse(getComputedStyle(node).backgroundColor);
                                if (bg && (bg.length < 4 || Number(getComputedStyle(node).backgroundColor.split(',')[3]) !== 0)) {
                                  const parts = getComputedStyle(node).backgroundColor.replace(/[rgba()]/g, '').split(',');
                                  if (parts.length < 4 || parseFloat(parts[3]) > 0.2) return bg;
                                }
                                node = node.parentElement;
                              }
                              return null;
                            };
                            const fine = document.querySelector('.fineprint');
                            const fg = parse(getComputedStyle(fine).color);
                            const bg = opaque(fine);
                            const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
                            return {
                              ratio: fg && bg ? ratio(fg, bg) : 0,
                              bodyBg,
                            };
                        }"""
                    )

                for scheme in ("light", "dark"):
                    sample = contrast_sample(scheme)
                    print(f"    contrast {scheme}: {sample}")
                    check(f"{scheme} mode body text contrast is at least 4.5:1",
                          sample.get("ratio", 0) >= 4.5, f"ratio={sample.get('ratio')}")
                sp.emulate_media(color_scheme="light")

                # ------------------------------------------------ mobile command
                print("\n[6] Tactical command stays locked without a desk counter")
                reason = sp.locator("#gate-reason").inner_text()
                check("a scraped price does not enable hold",
                      sp.locator("#btn-hold").is_disabled()
                      and "not computed" in reason.lower(),
                      f"reason={reason!r}")

                # ------------------------------------------------- error + empty
                print("\n[6b] Error banner, teaching empty, overflow")
                sp.evaluate(
                    """async () => {
                        const orig = window.fetch;
                        window.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
                        try {
                          await fetchSessionFromBackend(currentSessionId || 'probe-session');
                        } finally {
                          window.fetch = orig;
                        }
                    }"""
                )
                sp.wait_for_timeout(300)
                banner_on = sp.locator("#error-banner").is_visible()
                retry_on = sp.locator("#btn-retry").is_visible()
                banner_text = sp.locator("#error-text").inner_text()
                badge_down = sp.locator("#source-badge-text").inner_text().strip()
                check("failed session load shows an error banner", banner_on, banner_text[:120])
                check("error banner offers Retry", retry_on)
                check("failed load labels the source Unreachable",
                      badge_down == "Unreachable", f"badge={badge_down!r}")
                sp.click("#btn-retry")
                recovered_price = "pending"
                for _ in range(40):
                    if not sp.locator("#error-banner").is_visible():
                        recovered_price = sp.locator("#approval-price").inner_text().strip()
                        break
                    time.sleep(0.25)
                check("Retry clears the error and does not invent a counter",
                      not sp.locator("#error-banner").is_visible()
                      and recovered_price in ("", "--"),
                      f"price={recovered_price!r}")

                def overflow_at(width):
                    sp.set_viewport_size({"width": width, "height": 800})
                    sp.wait_for_timeout(150)
                    return sp.evaluate(
                        """() => {
                            const vw = document.documentElement.clientWidth;
                            let bad = '';
                            for (const el of document.body.querySelectorAll('*')) {
                              const r = el.getBoundingClientRect();
                              if (r.width < 1 || r.height < 1) continue;
                              if (r.right > vw + 1) {
                                bad = (el.id || el.className || el.tagName).toString().slice(0, 80);
                                break;
                              }
                            }
                            return {
                              vw,
                              scroll: document.documentElement.scrollWidth,
                              bad,
                            };
                        }"""
                    )

                for width in (320, 360, 480):
                    metrics = overflow_at(width)
                    print(f"    overflow @{width}: {metrics}")
                    check(
                        f"no horizontal overflow at {width}px",
                        metrics.get("scroll", 9999) <= metrics.get("vw", 0) + 1 and not metrics.get("bad"),
                        f"scroll={metrics.get('scroll')} vw={metrics.get('vw')} offender={metrics.get('bad')!r}",
                    )

                tc = (
                    "請在一六八八、阿里巴巴國際站或 WhatsApp 的這一則很長的對話裡核對採購條件："
                    "引擎只呈現已經算出的授權價格與供應商原文證據，採購人員必須自己貼上並且自己送出，"
                    "TradeFlow 絕不會代填輸入框也絕不會代按送出按鈕，若後端暫時連不上就顯示無法連線。"
                )
                sp.evaluate(
                    """(text) => {
                        const evidence = document.getElementById('approval-evidence');
                        const action = document.getElementById('approval-action');
                        const status = document.getElementById('status-text');
                        if (evidence) evidence.textContent = text;
                        if (action) action.textContent = text;
                        if (status) status.textContent = text;
                    }""",
                    tc,
                )
                for width in (320, 480):
                    metrics = overflow_at(width)
                    print(f"    tc overflow @{width}: {metrics}")
                    check(
                        f"Traditional Chinese sample does not overflow at {width}px",
                        metrics.get("scroll", 9999) <= metrics.get("vw", 0) + 1 and not metrics.get("bad"),
                        f"scroll={metrics.get('scroll')} vw={metrics.get('vw')} offender={metrics.get('bad')!r}",
                    )

                sp.set_viewport_size({"width": 320, "height": 800})
                sp.evaluate("() => { hasRenderedLiveData = false; renderDormantView(); }")
                sp.wait_for_timeout(200)
                empty_text = sp.locator("#teaching-empty").inner_text()
                empty_visible = sp.locator("#teaching-empty").is_visible()
                empty_price = sp.locator("#approval-price").inner_text().strip()
                check("teaching empty state explains the gate",
                      empty_visible and "you paste and send; TradeFlow never sends" in empty_text
                      and "請開啟" in empty_text,
                      empty_text[:80])
                check("teaching empty does not invent a price", empty_price == "--", f"price={empty_price!r}")
                empty_metrics = overflow_at(320)
                check("teaching empty does not overflow at 320px",
                      empty_metrics.get("scroll", 9999) <= empty_metrics.get("vw", 0) + 1
                      and not empty_metrics.get("bad"),
                      f"offender={empty_metrics.get('bad')!r}")

                # ------------------------------------------------------- summary
                print("\n[7] Console health")
                errs = [c for c in console if c.startswith("error")]
                print(f"    page console errors: {len(errs)}")
                for e in errs[:6]:
                    print(f"      {e[:150]}")
                check("no uncaught page errors on the sandbox page",
                      not any("PAGEERROR" in c for c in console),
                      "; ".join(c for c in console if "PAGEERROR" in c)[:200])

                if backend is not None:
                    print("\n[8] Desk import uses the key typed into the panel")
                    import urllib.request
                    try:
                        backend.terminate()
                        backend.wait(timeout=10)
                    except Exception:
                        try:
                            backend.kill()
                        except Exception:
                            pass
                    if backend_log is not None:
                        try:
                            backend_log.close()
                        except Exception:
                            pass
                    deadline = time.time() + 15
                    while time.time() < deadline and port_open(BACKEND_PORT):
                        time.sleep(0.2)
                    previous_tmp = backend_tmp
                    desk_key = "desk-extension-e2e-key"
                    backend, backend_tmp, backend_log = start_backend(api_key=desk_key)
                    if previous_tmp:
                        shutil.rmtree(previous_tmp, ignore_errors=True)
                    came_up = wait_for_port(BACKEND_PORT)
                    check("keyed desk backend came up", came_up, "port 8000")
                    deal_id = ""
                    if came_up:
                        payload = json.dumps({
                            "product": "umbrella",
                            "quantity": 1000,
                            "target_price": 12,
                            "ceiling_price": 20,
                            "risk_tolerance": 0.5,
                            "suppliers": [
                                {"name": "浙江頂尖雨具製造實業有限公司", "address": "ningbo@example.test"},
                                {"name": "Wenzhou", "address": "wenzhou@example.test"},
                                {"name": "Yiwu", "address": "yiwu@example.test"},
                                {"name": "Foshan", "address": "foshan@example.test"},
                            ],
                        }).encode()
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{BACKEND_PORT}/api/v1/desk/deals",
                            data=payload,
                            headers={"Content-Type": "application/json", "X-API-Key": desk_key},
                        )
                        with urllib.request.urlopen(request, timeout=60) as response:
                            created = json.loads(response.read().decode())
                        deal_id = created["deal"]["id"]
                        sp.locator("#tab-desk").click()
                        sp.locator("#desk-api-key").fill(desk_key)
                        sp.locator("#desk-deal-id").fill(deal_id)
                        page.bring_to_front()
                        page.locator("#btn-sim-quote").click()
                        sp.evaluate(
                            "() => { document.getElementById('desk-import-status').textContent = ''; }"
                        )
                        sp.evaluate("() => document.getElementById('btn-desk-import').click()")
                        status = ""
                        for _wait in range(40):
                            status = sp.locator("#desk-import-status").inner_text()
                            if "simulated" in status.lower() or "review" in status.lower():
                                break
                            time.sleep(0.25)
                        check(
                            "typed API key imports the visible simulated message",
                            "simulated" in status.lower(),
                            status,
                        )
                        quoted = urllib.request.Request(
                            f"http://127.0.0.1:{BACKEND_PORT}/api/v1/desk/deals/{deal_id}",
                            headers={"X-API-Key": desk_key},
                        )
                        with urllib.request.urlopen(quoted, timeout=30) as response:
                            snapshot = json.loads(response.read().decode())
                        review = snapshot.get("review") or []
                        check(
                            "imported message stays in review",
                            any((item.get("provenance") or {}).get("simulated") for item in review),
                            f"review={len(review)}",
                        )

            finally:
                ctx.close()

    except Exception:
        print("\nEXCEPTION during run:")
        traceback.print_exc()
        RESULTS.append(("run completed without exception", False, "see traceback"))
    finally:
        for proc, log in ((backend, backend_log), (sandbox, sandbox_log)):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if log is not None:
                try:
                    log.close()
                except Exception:
                    pass
        shutil.rmtree(user_data, ignore_errors=True)
        if backend_tmp:
            shutil.rmtree(backend_tmp, ignore_errors=True)
        try:
            os.remove(os.path.join(EXT_DIR, "_sandbox_server.log"))
        except OSError:
            pass

    # ------------------------------------------------------------- summary
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 78)
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILURES:")
        for name, _, detail in failed:
            print(f"  - {name} :: {detail}")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
