"""Browser check: a new IMAP message shows up on the open desk."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "desk_emails"
playwright = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(port: int) -> bool:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def test_desk_page_updates_when_mail_arrives():
    from tests.imap_fixture import ImapFixture

    fixture = ImapFixture()
    port = _free_port()
    tmp = tempfile.mkdtemp(prefix="tf_desk_ui_")
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PYTHONIOENCODING": "utf-8",
            "TRADEFLOW_ENV": "sandbox",
            "TRADEFLOW_DESK_DB": os.path.join(tmp, "desk.db"),
            "IMAP_HOST": "127.0.0.1",
            "IMAP_PORT": str(fixture.port),
            "IMAP_USERNAME": "desk",
            "IMAP_PASSWORD": "desk",
            "IMAP_SSL": "0",
            "IMAP_POLL_SECONDS": "1",
            "DESK_N_SAMPLES": "200",
            "DESK_N_GRID": "8",
            "TRADEFLOW_API_KEY": "desk-ui-key",
        }
    )
    env.pop("GMAIL_CLIENT_ID", None)
    env.pop("DESK_SMTP_SEND", None)
    log_path = os.path.join(tmp, "server.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import uvicorn; from src.app.main import app; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health(port):
            log.flush()
            pytest.fail(Path(log_path).read_text(encoding="utf-8", errors="replace")[-2000:])
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            assert page.locator('a[href="/desk"]').count() == 1
            page.goto(f"http://127.0.0.1:{port}/desk", wait_until="domcontentloaded")
            page.fill("#api-key", "desk-ui-key")
            page.fill("#product", "umbrella")
            page.fill("#quantity", "1000")
            page.fill("#target", "12")
            page.fill("#ceiling", "20")
            page.fill("#risk", "0.5")
            page.fill(
                "#suppliers",
                "\n".join(
                    [
                        "Ningbo Co., Ltd., ningbo@example.test",
                        "Wenzhou, wenzhou@example.test",
                        "Yiwu, yiwu@example.test",
                        "Foshan, foshan@example.test",
                    ]
                ),
            )
            page.click("button[type=submit]")
            page.wait_for_selector("#board:not([hidden])", timeout=15000)
            for name in ("ningbo.eml", "wenzhou.eml", "yiwu.eml", "foshan.eml"):
                fixture.append((FIXTURES / name).read_bytes())
            page.get_by_text("Ningbo Co., Ltd.").first.wait_for(timeout=15000)
            page.get_by_text("15.60 CNY").first.wait_for(timeout=20000)
            hazard = page.locator(".desk-card").first.inner_text()
            assert "%" in hazard
            assert "prior-driven" in hazard
            page.locator("#events").get_by_text("email").first.wait_for(timeout=5000)
            fixture.append((FIXTURES / "ningbo-followup.eml").read_bytes())
            page.get_by_text("13.40 CNY").first.wait_for(timeout=20000)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1
            page.fill("#paste-text", "Price around 33-35 CNY depending on finish.")
            page.locator("#paste-form button[type=submit]").click()
            page.locator("#review select").wait_for(timeout=10000)
            page.locator("#review select").select_option(index=1)
            chosen = page.locator("#review select").input_value()
            assert chosen
            page.wait_for_timeout(2500)
            assert page.locator("#review select").input_value() == chosen
            for width in (360, 320):
                page.set_viewport_size({"width": width, "height": 800})
                metrics = page.evaluate(
                    """() => {
                      const select = document.querySelector('#review select');
                      const area = document.querySelector('#drafts textarea');
                      const line = document.querySelector('#events .desk-event p');
                      return {
                        selectScroll: select ? select.scrollWidth : 9999,
                        selectClient: select ? select.clientWidth : 0,
                        areaClient: area ? area.clientWidth : 0,
                        areaRows: area ? Number(area.getAttribute('rows')) : 0,
                        vw: document.documentElement.clientWidth,
                        line: line ? line.textContent : '',
                      };
                    }"""
                )
                assert metrics["selectScroll"] <= metrics["vw"] + 1
                assert metrics["selectClient"] <= metrics["vw"]
                assert metrics["areaRows"] >= 8
                assert metrics["areaClient"] >= width - 80
                assert " UTC" in metrics["line"]
                stamp = metrics["line"].split("·")[0].replace("UTC", "")
                assert "T" not in stamp
            page.evaluate("sessionStorage.setItem('tfDeskDeal', '%s')" % ("ab" * 16))
            page.reload(wait_until="domcontentloaded")
            page.get_by_text("fresh disk").wait_for(timeout=10000)
            assert page.locator("#desk-form").is_visible()
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        fixture.close()
