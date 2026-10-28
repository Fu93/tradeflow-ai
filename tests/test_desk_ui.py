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
            page.get_by_text("Record: fixture").first.wait_for(timeout=20000)
            page.get_by_text("Excluded from the decision").first.wait_for(timeout=5000)
            assert "15.60 CNY" not in page.locator(".desk-card").first.inner_text()
            sensitivity = page.locator("#hazard-sensitivity").inner_text()
            assert "%" in sensitivity
            assert "Transferred baseline" in sensitivity
            assert "reserve-gap term is not added" in sensitivity
            assert "this supplier's effect only" in sensitivity
            assert page.locator("#hazard-reserve-gap").count() == 1
            assert page.locator("#obs-noise").count() == 1
            assert page.locator("#sanity-multiple").count() == 1
            page.locator("#events").get_by_text("email").first.wait_for(timeout=5000)
            page.locator("#paste-supplier").select_option(index=1)
            page.fill("#paste-text", "单价 14.80 元")
            page.locator("#paste-form button[type=submit]").click()
            confirm = page.locator("[data-confirm-value='14.80']")
            confirm.wait_for(timeout=15000)
            assert "14.80" in (confirm.get_attribute("data-confirm-span") or "")
            page.locator(".desk-event", has=confirm).get_by_role("button", name="Accept").click()
            previous = page.locator("#board").get_attribute("data-desk-seq")
            page.wait_for_function(
                """(seq) => {
                  const board = document.getElementById('board');
                  return Boolean(board && board.dataset.deskSeq !== seq
                    && document.body.innerText.includes('14.80 CNY'));
                }""",
                arg=previous,
            )
            page.get_by_text("cheapest confirmed quote").first.wait_for(timeout=10000)
            # The model's working is folded away; a person opens it, and it stays
            # open across the redraw on every poll.
            page.locator(".desk-card", has_text="14.80 CNY").locator(
                "summary", has_text="How these figures were worked out"
            ).click()
            page.get_by_text("labelled policy").first.wait_for(timeout=10000)
            page.get_by_text("unconfirmed numbers used in decisions: 0").first.wait_for(timeout=10000)
            card = page.locator(".desk-card", has_text="14.80 CNY")
            lines = card.locator("p").all_inner_texts()
            assert any(item.startswith("Default counter:") for item in lines)
            assert any(item.startswith("Push lower:") for item in lines)
            assert any("D prior (user policy): mean 0.9" in item for item in lines)
            page.fill("#reserve-prior", "0.7")
            page.locator("#assumptions button[type=submit]").click()
            page.get_by_text("D prior (user policy): mean 0.7").first.wait_for(timeout=10000)
            page.locator(".desk-card", has_text="14.80 CNY").get_by_role("button", name="Record continue").click()
            page.get_by_text("Recorded outcomes: 1").wait_for(timeout=10000)
            fixture.append((FIXTURES / "ningbo-followup.eml").read_bytes())
            page.locator("#events").get_by_text("13.40").first.wait_for(timeout=20000)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 1
            page.fill(
                "#paste-text",
                "We can do 14.80 CNY per piece.\nFinal offer 12.50 CNY per piece.",
            )
            page.locator("#paste-form button[type=submit]").click()
            block = page.locator("#review .desk-event", has_text="Final offer")
            block.wait_for(timeout=10000)
            block.get_by_role("button", name="12.50", exact=True).click()
            span = block.locator("[data-confirm-span]").get_attribute("data-confirm-span")
            assert span and "Final offer" in span and "12.50" in span
            page.fill("#paste-text", "Price around 33-35 CNY depending on finish.")
            page.locator("#paste-form button[type=submit]").click()
            review = page.locator("#review select").last
            review.wait_for(timeout=10000)
            review.select_option(index=1)
            chosen = review.input_value()
            assert chosen
            previous = page.locator("#board").get_attribute("data-desk-seq")
            page.wait_for_function(
                "(seq) => document.getElementById('board').dataset.deskSeq !== seq",
                arg=previous,
            )
            assert page.locator("#review select").last.input_value() == chosen
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
