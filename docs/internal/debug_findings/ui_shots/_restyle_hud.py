"""One-off helper: restyle the injected HUD in extension/content.js.

Rewrites getStyles() and the decision-deck template, maps the legacy status
colours onto the dossier palette, and leaves all logic untouched.
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ROOT, "extension", "content.js")

CSS = """
        :host {
          all: initial; /* 徹底切斷宿主頁面 CSS 繼承與 !important 污染 */
          position: fixed;
          bottom: 24px;
          right: 24px;
          z-index: 2147483647; /* 最大 32-bit 安全層次 */
          font-family: "Segoe UI", system-ui, -apple-system, "Noto Sans TC", "Microsoft JhengHei", sans-serif;
          font-size: 12px;
          line-height: 1.5;
          color-scheme: light;
          pointer-events: auto;
          user-select: none;
        }

        *, *::before, *::after {
          box-sizing: border-box !important;
          margin: 0;
          padding: 0;
        }

        /* A paper file tab, not a neon widget: light surface, one rule of
           status colour on the left edge, no glow. */
        .tf-hud-pill {
          background: #f2f2ec;
          color: #1b2622;
          border: 1px solid #9ba79c;
          border-left: 4px solid var(--tf-status-color, #46664c);
          border-radius: 2px;
          padding: 9px 12px 9px 11px;
          box-shadow: 0 8px 20px rgba(27, 38, 34, 0.18);
          display: flex;
          align-items: center;
          gap: 10px;
        }

        .tf-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--tf-dot-color, #46664c);
          flex: 0 0 auto;
        }

        .tf-title {
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", sans-serif;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.01em;
          display: flex;
          align-items: center;
          gap: 7px;
        }

        .tf-badge {
          font-size: 9px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 1px 5px;
          border: 1px solid currentColor;
          border-radius: 0;
          font-weight: 600;
        }

        .tf-subtext {
          color: #55645c;
          font-size: 10px;
          margin-top: 1px;
        }

        .tf-btn {
          border-radius: 0;
          cursor: pointer;
          font-family: "Segoe UI", system-ui, sans-serif;
          font-size: 10px;
          font-weight: 600;
          padding: 5px 9px;
          border: 1px solid #9ba79c;
          background: transparent;
          color: #1b2622;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          white-space: nowrap;
        }

        .tf-btn:active {
          transform: translateY(1px);
        }

        .tf-btn-primary {
          background: #2e4f63;
          border-color: #2e4f63;
          color: #f2f2ec;
        }

        .tf-btn-secondary:hover,
        .tf-btn-primary:hover {
          filter: brightness(1.08);
        }

        /* Decision deck: the same sheet the dashboard uses. */
        .tf-deck-container {
          position: fixed;
          bottom: 74px;
          right: 24px;
          width: 392px;
          max-height: 72vh;
          overflow-y: auto;
          background: #f2f2ec;
          border: 1px solid #9ba79c;
          border-top: 3px solid #a33a2a;
          border-radius: 0;
          padding: 12px 14px 14px;
          box-shadow: 0 12px 28px rgba(27, 38, 34, 0.22);
          color: #1b2622;
          display: none;
        }

        .tf-deck-head {
          display: flex;
          align-items: baseline;
          justify-content: space-between;
          gap: 10px;
          padding-bottom: 8px;
          border-bottom: 2px solid #1b2622;
          margin-bottom: 10px;
        }

        .tf-deck-title {
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", sans-serif;
          font-size: 13px;
          font-weight: 600;
          display: inline-flex;
          align-items: center;
        }

        .tf-deck-meta {
          font-size: 9px;
          letter-spacing: 0.06em;
          color: #8b968d;
          border: 1px solid #c2c8bd;
          padding: 1px 5px;
          white-space: nowrap;
        }

        .tf-deal-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
          padding: 8px 0 10px;
          border-bottom: 1px dotted #9ba79c;
          margin-bottom: 10px;
        }

        .tf-deal-label {
          display: block;
          font-size: 10px;
          color: #8b968d;
        }

        .tf-deal-figure {
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", sans-serif;
          font-size: 18px;
          font-variant-numeric: tabular-nums;
        }

        .tf-deal-ask { color: #a33a2a; }
        .tf-deal-counter { color: #2e4f63; }

        .tf-deck-note {
          font-size: 10px;
          color: #55645c;
          margin-bottom: 8px;
          display: inline-flex;
          align-items: center;
        }

        .tf-card {
          background: transparent;
          border: 0;
          border-bottom: 1px dotted #c2c8bd;
          border-radius: 0;
          padding: 0 0 9px;
          margin-bottom: 9px;
        }

        .tf-card:last-child {
          margin-bottom: 0;
          border-bottom: 0;
          padding-bottom: 0;
        }

        .tf-card-title {
          font-size: 11px;
          font-weight: 600;
          margin-bottom: 3px;
          color: #2e4f63;
        }

        .tf-card-body {
          font-size: 11px;
          line-height: 1.65;
          color: #55645c;
          margin-bottom: 6px;
        }

        .tf-card-body + div .tf-btn {
          font-size: 10px;
        }
"""

DECK = """      deck.innerHTML = `
        <div class="tf-deck-head">
          <span class="tf-deck-title">${TF_SVG.radar}採購決策副駕駛</span>
          <span class="tf-deck-meta">WIN PROB ${winProb == null ? "—" : winProb + "%"}</span>
        </div>
        <div class="tf-deal-row">
          <div>
            <span class="tf-deal-label">供應商報價</span>
            <span class="tf-deal-figure tf-deal-ask">¥${tfEsc(tfNum(offer, 0).toFixed(2))}</span>
          </div>
          <div>
            <span class="tf-deal-label">引擎建議還盤</span>
            <span class="tf-deal-figure tf-deal-counter">¥${counterSafe}</span>
          </div>
        </div>
        <div class="tf-deck-note">${TF_SVG.cards}三條反擊卡：複製後由你自己貼上發送</div>

        <div class="tf-card">
          <div class="tf-card-title">1. 預算核定施壓（建議先用）</div>
          <div class="tf-card-body">${card1}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card1)}">${TF_SVG.copy}複製這條</button>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">2. 條款置換：定金換交期</div>
          <div class="tf-card-body">${card2}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card2)}">${TF_SVG.copy}複製這條</button>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">3. 備選工廠威懾</div>
          <div class="tf-card-body">${card3}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card3)}">${TF_SVG.copy}複製這條</button>
          </div>
        </div>
      `;
"""


def main() -> int:
    src = io.open(PATH, encoding="utf-8", newline="").read()

    # 1. Stylesheet: replace everything the function returns.
    start = src.index("    getStyles() {")
    body_start = src.index("      return `", start)
    body_end = src.index("`;", body_start) + 2
    src = src[:body_start] + "      return `" + CSS + "`;" + src[body_end:]

    # 2. Deck markup: replace the template between the assignment and the
    #    clipboard-wiring comment that follows it.
    d_start = src.index("      deck.innerHTML = `")
    d_end = src.index("`;", d_start) + 2
    src = src[:d_start] + DECK.rstrip() + src[d_end:]

    # 3. Status colours: keep the call sites, translate them at the boundary.
    src = src.replace(
        "    updateStatus(statusText, color, subtext) {",
        "    updateStatus(statusText, color, subtext) {\n"
        "      // Legacy state colours are mapped onto the dossier palette here, so\n"
        "      // every call site keeps its meaning (safe / caution / trip / solving).\n"
        "      color = TF_INK[color] || color;",
        1,
    )
    src = src.replace(
        "  const OVERLAY_TAG = \"tradeflow-overlay\";",
        "  const TF_INK = {\n"
        "    \"#10b981\": \"#46664c\", // safe / active\n"
        "    \"#f59e0b\": \"#8d641b\", // caution / paused\n"
        "    \"#f43f5e\": \"#a33a2a\", // tripped\n"
        "    \"#38bdf8\": \"#2e4f63\", // engine working\n"
        "    \"#06b6d4\": \"#2e4f63\",\n"
        "    \"#a855f7\": \"#7d2a1e\", // simulator mode\n"
        "    \"#94a3b8\": \"#8b968d\", // idle\n"
        "  };\n\n"
        "  const OVERLAY_TAG = \"tradeflow-overlay\";",
        1,
    )

    # 4. Copy-confirmation colours inside the shadow DOM.
    src = src.replace('btn.style.borderColor = "#10b981";', 'btn.style.borderColor = "#46664c";', 1)
    src = src.replace('btn.style.borderColor = "#f59e0b";', 'btn.style.borderColor = "#8d641b";', 1)

    # 5. SVG icon strokes.
    for old, new in (
        ('stroke="#38bdf8"', 'stroke="#2e4f63"'),
        ('stroke="#94a3b8"', 'stroke="#8b968d"'),
        ('stroke="#10b981"', 'stroke="#46664c"'),
        ('stroke="#f59e0b"', 'stroke="#8d641b"'),
    ):
        src = src.replace(old, new)

    io.open(PATH, "w", encoding="utf-8", newline="").write(src)
    print("content.js rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
