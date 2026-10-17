"""Screenshot the dossier at three widths and assert layout invariants.

Run:  python debug_findings/ui_shots/audit_layout.py
Exit: 0 = every check passed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://127.0.0.1:8000/"
DEAL = "UI_AUDIT_01"
WIDTHS = [(1440, 950, "desktop"), (1024, 900, "tablet"), (430, 900, "phone")]


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        URL.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read())


def seed_deal() -> None:
    """Create a deal with two real rounds so the ladder has something to plot."""
    _post(
        "/api/v1/sessions",
        {
            "session_id": DEAL,
            "product_name": "自動反開摺疊傘",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "moq": 5000,
            "max_rounds": 5,
        },
    )
    _post(
        f"/api/v1/sessions/{DEAL}/round",
        {"opponent_raw_text": "初次報價 ¥15.60，含 LOGO 印刷", "opponent_offer_price": 15.60, "mode": "copilot"},
    )
    _post(
        f"/api/v1/sessions/{DEAL}/round",
        {"opponent_raw_text": "原料漲了，最低 ¥14.80", "opponent_offer_price": 14.80, "mode": "copilot"},
    )

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILS.append(f"{name}: {detail}")


def main() -> int:
    seed_deal()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.evaluate(
            """(deal) => {
                const r = [...document.querySelectorAll('.session-item')]
                  .find(x => x.textContent.includes(deal));
                if (r) r.click();
            }""",
            DEAL,
        )
        page.wait_for_timeout(1500)

        for w, h, label in WIDTHS:
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(900)
            page.screenshot(path=os.path.join(HERE, f"dossier_{label}.png"), full_page=True)

            metrics = page.evaluate(
                """() => {
                  const doc = document.documentElement;
                  const overflow = doc.scrollWidth - doc.clientWidth;
                  const wide = [...document.querySelectorAll('body *')]
                    .filter(el => {
                      const r = el.getBoundingClientRect();
                      return r.width > 0 && r.right > doc.clientWidth + 1;
                    })
                    .slice(0, 6)
                    .map(el => `${el.tagName.toLowerCase()}.${el.className}`.slice(0, 70));
                  const track = document.querySelector('.ladder-track');
                  const markers = [...document.querySelectorAll('.track-marker:not([hidden])')]
                    .map(el => {
                      const b = el.getBoundingClientRect();
                      return { top: Math.round(b.top), bottom: Math.round(b.bottom) };
                    });
                  const tb = track ? track.getBoundingClientRect() : null;
                  const ticks = [...document.querySelectorAll('.track-tick span')]
                    .map(el => el.getBoundingClientRect());
                  const ladderH = document.querySelector('.ladder-track')
                    ? Math.round(document.querySelector('.ladder-track').getBoundingClientRect().height) : 0;
                  return {
                    overflow, wide, markers, ticks: ticks.length, ladderH,
                    trackTop: tb ? Math.round(tb.top) : null,
                    trackBottom: tb ? Math.round(tb.bottom) : null,
                    dealTitle: document.getElementById('dealTitle').textContent,
                    counter: document.getElementById('decisionCounter').textContent,
                    room: document.getElementById('ladderRoom').textContent,
                    ticksInTrack: ticks.every(t => !tb || (t.top >= tb.top - 2 && t.bottom <= tb.bottom + 2)),
                  };
                }"""
            )
            print(f"\n[{label} {w}x{h}] {json.dumps(metrics, ensure_ascii=False)}")
            check(f"{label}: no horizontal overflow", metrics["overflow"] <= 1, f"overflow={metrics['overflow']} {metrics['wide']}")
            check(f"{label}: ladder markers inside track",
                  all(metrics["trackTop"] - 12 <= m["top"] and m["bottom"] <= metrics["trackBottom"] + 12 for m in metrics["markers"]),
                  str(metrics["markers"]))
            check(f"{label}: tick labels stay within the track", metrics["ticksInTrack"])
            check(f"{label}: deal selected and live", metrics["dealTitle"] != "No deal selected", metrics["dealTitle"])
            check(f"{label}: ladder has a readable height", metrics["ladderH"] >= 120, f"{metrics['ladderH']}px")

        check("no uncaught page errors", not errors, "; ".join(errors)[:200])
        browser.close()

    print(f"\nRESULT: {'ALL PASS' if not FAILS else str(len(FAILS)) + ' failure(s)'}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
