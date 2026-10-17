"""Real end-to-end test of the packed/unpacked MV3 extension with Playwright.

Loads the ACTUAL extension/ directory into real Chromium (persistent context, so
MV3 service workers + content scripts run for real), opens the mock sandbox over
http (so host_permissions / match patterns apply exactly as in the demo), and
drives the genuine flow:

    supplier quote -> content script detects price -> port -> service worker
    -> FastAPI /api/v1/.../round -> SESSION_UPDATED -> HUD + decision cards

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


def start_backend():
    """Start the FastAPI backend exactly as documented, isolated store."""
    tmp = tempfile.mkdtemp(prefix="tf_ext_backend_")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = REPO
    env["TRADEFLOW_DB_PATH"] = os.path.join(tmp, "sessions.db")
    env["TRADEFLOW_STORE_DIR"] = os.path.join(tmp, "sessions_json")
    env.pop("TRADEFLOW_API_KEY", None)
    env.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
    log = open(os.path.join(tmp, "backend.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.app.main"],
        cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    return proc, tmp, log


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
            backend, backend_tmp, backend_log = start_backend()
            if not wait_for_port(BACKEND_PORT):
                print("    FATAL: backend did not come up")
                return 1
            print(f"    backend started on :{BACKEND_PORT}")

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
                sw = None
                for _ in range(60):
                    if ctx.service_workers:
                        sw = ctx.service_workers[0]
                        break
                    time.sleep(0.25)
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
                import re as _re
                print(f"    registered session_id: {sid!r}")
                check(
                    "session_id registered by the SW matches backend whitelist",
                    bool(_re.fullmatch(r"[A-Za-z0-9._-]{1,128}", sid)),
                    f"session_id={sid!r} (CJK supplier name must NOT leak through)",
                )

                # ------------------------------------------------- supplier quote
                print("\n[3] Supplier quote -> engine -> decision cards")
                page.click("#btn-sim-quote")  # appends '...單價報價為 ¥15.60...'
                status_text = ""
                cards = 0
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        status_text = page.evaluate(
                            """() => {
                                const el = document.querySelector('[data-tradeflow="overlay"]');
                                if (!el || !el.shadowRoot) return '';
                                const t = el.shadowRoot.querySelector('.tf-title');
                                const s = el.shadowRoot.querySelector('.tf-subtext');
                                return (t ? t.innerText : '') + ' || ' + (s ? s.innerText : '');
                            }"""
                        )
                        cards = page.evaluate(
                            """() => {
                                const el = document.querySelector('[data-tradeflow="overlay"]');
                                if (!el || !el.shadowRoot) return 0;
                                return el.shadowRoot.querySelectorAll('.tf-copy-btn').length;
                            }"""
                        )
                    except Exception:
                        pass
                    if cards >= 3:
                        break
                    time.sleep(0.5)

                print(f"    HUD status: {status_text.strip()[:160]}")
                check("engine reached a settled HUD state (not stuck solving)",
                      "SOLVING" not in status_text.upper(), status_text.strip()[:80])
                check("3 decision cards rendered", cards == 3, f"copy buttons found = {cards}")

                if cards >= 3:
                    texts = hud.evaluate(
                        """el => Array.from(el.shadowRoot.querySelectorAll('.tf-copy-btn'))
                                   .map(b => decodeURIComponent(b.getAttribute('data-text') || ''))"""
                    )
                    joined = " ".join(texts)
                    print(f"    card[0]: {texts[0][:110]}")
                    check("cards contain real CNY figures (not placeholders)",
                          "¥" in joined and "--" not in joined.replace("-- ", ""), joined[:80])
                    check("cards carry an authorised price from the engine",
                          any(c.isdigit() for c in joined))

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
                    if "CAPTCHA" not in recovered.upper() and recovered_cards >= 3:
                        break
                    time.sleep(0.5)
                check("captcha recovery un-freezes the HUD on next quote (no permanent freeze)",
                      "CAPTCHA" not in recovered.upper(), f"status now: {recovered[:70]}")
                check("decision cards render again after captcha recovery",
                      recovered_cards >= 3, f"cards={recovered_cards}")

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
                    if (live.get("target") != "--" and live.get("risk") != "--"
                            and "—" not in live.get("round", "—")):
                        break
                    time.sleep(0.4)
                print("    live panel: " + json.dumps(live, ensure_ascii=False))
                check("panel shows the live target price", live.get("target") not in ("--", ""),
                      f"target={live.get('target')!r}")
                check("panel shows a live breakdown risk (copilot has turn=null)",
                      live.get("risk") not in ("--", ""),
                      f"risk={live.get('risk')!r} (read from action_object.breakdown_risk)")
                check("panel shows a round counter, not a placeholder",
                      "—" not in str(live.get("round", "—")), f"round={live.get('round')!r}")
                check("panel rendered the turn timeline", int(live.get("timeline") or 0) > 0,
                      f"rows={live.get('timeline')}")
                check("panel has no runtime errors while rendering live data",
                      not any("PAGEERROR" in c for c in sp_console),
                      "; ".join(c for c in sp_console if "PAGEERROR" in c)[:200])

                # ------------------------------------------------ mobile command
                print("\n[6] Tactical command path (2 = hold firm)")
                sp.click("#btn-hold")
                sp.wait_for_timeout(3000)
                fb = sp.locator("body").inner_text()
                print(f"    panel text after command: {fb[:120].replace(chr(10), ' ')}")
                check("side panel reports a command outcome (no silent no-op)",
                      ("Hold" in fb or "command" in fb.lower() or "dispatched" in fb.lower()
                       or "not applied" in fb.lower() or "OK" in fb))

                # ------------------------------------------------------- summary
                print("\n[7] Console health")
                errs = [c for c in console if c.startswith("error")]
                print(f"    page console errors: {len(errs)}")
                for e in errs[:6]:
                    print(f"      {e[:150]}")
                check("no uncaught page errors on the sandbox page",
                      not any("PAGEERROR" in c for c in console),
                      "; ".join(c for c in console if "PAGEERROR" in c)[:200])

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
