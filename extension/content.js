/**
 * TradeFlow Copilot — Modern Chrome Manifest V3 Content Script
 * 
 * Architecture:
 * 1. Web Component with Open Shadow DOM (<tradeflow-overlay>) for 100% CSS isolation.
 * 2. Event Barrier stopping keyboard/mouse leak to host e-commerce page.
 * 3. Multi-tier Dynamic Session Context Extractor (eliminating hardcoded session collisions).
 * 4. Bounded O(1) MessageLRUCache (FNV-1a hash, capacity=100) preventing memory leaks.
 * 5. Targeted Two-phase DOM MutationObserver with lifecycle teardown hooks.
 * 6. Resilient Port connection to Background Service Worker with exponential backoff.
 * 7. Alibaba Baxia & No-Captcha Safety Sentinel with instant freeze & remote alert.
 */

(() => {
  console.log("[TradeFlow Copilot] Modern MV3 Content Script initialized.");

  // ---------------------------------------------------------------------------
  // 1. Clean Inline SVG Icons (Zero Emoji Architecture)
  // ---------------------------------------------------------------------------
  const TF_SVG = {
    radar: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#2e4f63" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:5px; display:inline-block;"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/><path d="m4.93 4.93 4.24 4.24"/><circle cx="12" cy="12" r="2"/></svg>`,
    cards: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8b968d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:5px; display:inline-block;"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
    edit: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>`,
    copy: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>`,
    check: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#46664c" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><polyline points="20 6 9 17 4 12"/></svg>`,
    shield: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8d641b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:4px; display:inline-block;"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  };

  // ---------------------------------------------------------------------------
  // 2. High-Performance O(1) LRU Message Cache (FNV-1a Hash, Capacity=100)
  // ---------------------------------------------------------------------------
  class MessageLRUCache {
    constructor(capacity = 100) {
      this.capacity = capacity;
      this.cache = new Map();
    }

    _hash(str) {
      let hash = 0x811c9dc5;
      for (let i = 0; i < str.length; i++) {
        hash ^= str.charCodeAt(i);
        hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
      }
      return hash >>> 0;
    }

    has(text) {
      const key = this._hash(text);
      if (!this.cache.has(key)) return false;
      const val = this.cache.get(key);
      this.cache.delete(key);
      this.cache.set(key, val);
      return true;
    }

    add(text) {
      const key = this._hash(text);
      if (this.cache.has(key)) {
        this.cache.delete(key);
      } else if (this.cache.size >= this.capacity) {
        const oldestKey = this.cache.keys().next().value;
        this.cache.delete(oldestKey);
      }
      this.cache.set(key, Date.now());
    }

    clear() {
      this.cache.clear();
    }
  }

  // ---------------------------------------------------------------------------
  // 3. Multi-tier Dynamic Session Context Extractor
  // ---------------------------------------------------------------------------
  function detectPlatform() {
    const host = window.location.hostname;
    if (host.includes("1688.com")) return "1688";
    if (host.includes("alibaba.com")) return "alibaba";
    if (host.includes("whatsapp.com")) return "whatsapp";
    if (host.includes("globalsources.com")) return "globalsources";
    if (host.includes("made-in-china.com")) return "made_in_china";
    return "generic";
  }

  // [P0-1 修正] 後端 session_id 白名單：^[A-Za-z0-9._-]{1,128}$
  // （src/session/schema.py 的 SAFE_SESSION_ID_RE；URL 路徑層 src/app/main.py:246）。
  // 舊版 DOM 分支的清洗 regex 刻意保留 CJK（`[^a-zA-Z0-9_\u4e00-\u9fa5-]`），
  // 於是中文店名（含 mock_chat.html 的沙盒店名與真實 1688 店名）產出
  // `sess_浙江頂尖…`，後端一律回 HTTP 422 —— 「報價 → 決策卡」主線第一步就死。
  // 不要放寬後端白名單（它同時是路徑穿越防線），改在前端只輸出白名單字元。
  const SESSION_ID_MAX_LEN = 128;
  const SESSION_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;

  // FNV-1a 32-bit：免依賴、同步、對同一字串恆定，用來讓非 ASCII 的名稱
  // 仍能得到「穩定且不碰撞」的 session_id。
  function sessionIdHash32(text) {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i += 1) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, "0");
  }

  // 保證回傳值永久符合 SESSION_ID_RE（若清空或超長則以穩定雜湊截斷 fallback）。
  function sanitizeSessionId(rawValue, prefix) {
    const raw = String(rawValue === null || rawValue === undefined ? "" : rawValue).trim();
    // 已是合法 ASCII 值（URL 參數等）→ 原樣沿用，行為與舊版一致。
    let body = /^[A-Za-z0-9._-]{1,64}$/.test(raw) ? raw : "";
    if (!body) {
      // 非 ASCII（中文店名、含 CJK 的頁面標題、URL）：只保留可讀的 ASCII 片段
      // 當人眼提示，並用原始字串的雜湊綁定身分 —— 否則不同中文店名清洗後
      // 都會變成同一串底線，全部共用同一個 session。
      const slug = raw
        .replace(/[^A-Za-z0-9._-]+/g, "")
        .replace(/^[._-]+|[._-]+$/g, "")
        .slice(0, 24);
      body = `h${sessionIdHash32(raw)}${slug ? `_${slug}` : ""}`;
    }
    const candidate = `${prefix}${body}`.slice(0, SESSION_ID_MAX_LEN);
    // 最後一道防線：即使上面的清理出了意外，也不讓不合法 id 送出。
    return SESSION_ID_RE.test(candidate) ? candidate : `${prefix}h${sessionIdHash32(raw)}`;
  }

  function extractSessionContext() {
    const url = new URL(window.location.href);

    // 1. URL search params
    const candidateParams = ["sellerId", "memberId", "chat_id", "supplier_id", "conversation_id", "session_id", "id"];
    for (const p of candidateParams) {
      const val = url.searchParams.get(p);
      if (val && val.length > 2) {
        const sessionId = sanitizeSessionId(val, "sess_");
        return {
          sessionId,
          supplierId: sessionId.slice("sess_".length),
          supplierName: `Supplier (${val})`,
          platform: detectPlatform(),
          url: window.location.href,
        };
      }
    }

    // 2. DOM inspection for supplier title
    const domSelectors = [
      ".chat-header .supplier-name",
      ".im-chat-title",
      ".seller-info .name",
      "[data-role='seller-title']",
      "#chatHeaderTitle",
      "header [data-testid='conversation-header'] span",
    ];
    for (const sel of domSelectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText && el.innerText.trim()) {
        const rawName = el.innerText.trim().slice(0, 30);
        // [P0-1 修正] 舊版在此保留中文 → 後端 422。改走白名單清洗 + 雜湊 fallback。
        const sessionId = sanitizeSessionId(rawName, "sess_");
        return {
          sessionId,
          supplierId: sessionId.slice("sess_".length),
          supplierName: rawName,
          platform: detectPlatform(),
          url: window.location.href,
        };
      }
    }

    // 3. Document title or pathname fallback
    const pathPart = (url.hostname + url.pathname).replace(/[^a-zA-Z0-9_-]/g, "_").slice(-20);
    // [P0-1 修正] 一併走白名單清洗，確保 fallback 分支也不可能產出非法 id。
    const sessionId = sanitizeSessionId(pathPart || `${url.hostname}${url.pathname}`, "sess_tab_");
    return {
      sessionId,
      supplierId: sessionId.slice("sess_tab_".length),
      supplierName: document.title ? document.title.slice(0, 25) : "E-Commerce Supplier",
      platform: detectPlatform(),
      url: window.location.href,
    };
  }

  // ---------------------------------------------------------------------------
  // 4. Anti-Bot Risk & Captcha Selectors (Alibaba Baxia / No-Captcha Sentinel)
  // ---------------------------------------------------------------------------
  const CAPTCHA_SELECTORS = [
    "#nc_1_n1z", ".nc_iconfont", ".nc-lang-cnt",          // 阿里 No-Captcha 滑塊
    "#baxia-dialog-content", ".baxia-dialog",             // 阿里霸下安全風控彈窗
    ".sm-pop", ".sec-modal",                              // 阿里次世代滑塊
    "#challenge-running", ".cf-turnstile-wrapper",        // Cloudflare / Turnstile
    "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']" // 通用驗證碼
  ];

  function detectCaptchaChallenge() {
    for (const sel of CAPTCHA_SELECTORS) {
      const el = document.querySelector(sel);
      if (el && el.offsetParent !== null) {
        return true;
      }
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // 5. Multi-Platform Chat Bubble and Input Selectors
  // ---------------------------------------------------------------------------
  // [修正/誠實揭露] 舊版在此定義了一個 `PLATFORMS` 查詢表（c1688/alibaba/
  // globalsources/made_in_china/whatsapp/generic 的 DOM selector），但它**從未被
  // 任何程式碼引用** —— 是死碼。本擴充為 Copilot 模式，**刻意不提供**任何自動
  // 填入聊天框或自動點擊發送的能力；相關的 findChatInput() / findSendButton()
  // 早已移除。死碼已刪除，僅保留對選擇器的說明供偵測入口使用。

  // ---------------------------------------------------------------------------
  // 5b. Security helpers (H2/H5)
  // ---------------------------------------------------------------------------
  // H2 (F3): backend-sourced values (counterOffer) must be escaped before they
  // are interpolated into the decision-deck innerHTML template. The card text is
  // also injected into a data-* attribute, so a value containing a quote would
  // break out of the attribute even with encodeURIComponent applied downstream.
  function tfEsc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // H2 (F3): counterOffer is rendered with toFixed() and must therefore be a
  // real number. Previously it was interpolated raw, so a backend string (or an
  // object) either rendered as "[object Object]" or threw inside .toFixed().
  // [P0 REGRESSION — 2026-10-17 實機稽查] Number(null) === 0，舊版把缺值渲染成
  // ¥0.00 / 0.0%；null / undefined / 空字串一律視為缺值。
  function tfNum(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function customsReferenceLine(ctx) {
    if (!ctx || typeof ctx !== "object") {
      return "REFERENCE ONLY. Customs reference: not available. Implied customs unit value is not a price quote.";
    }
    const pick = (value) => (value === null || value === undefined || value === "" ? "not available" : String(value));
    const provenance = ctx.provenance || {};
    return (
      "REFERENCE ONLY. Implied customs unit value is not a price quote. HS " + pick(ctx.hs) +
      ". Year " + pick(ctx.year) +
      ". UK imports from China " + pick(ctx.value_usd) + " USD. Quantity " + pick(ctx.qty) +
      ". Implied customs unit value " + pick(ctx.unit_value_usd) + " USD. Source " + pick(provenance.source) +
      ". Fetched " + pick(provenance.fetched_at) +
      ". Status " + pick(ctx.status) +
      ". CIF customs valuation is not a supplier offer and does not set the counter."
    );
  }

  function extractPriceFromText(text) {
    if (!text) return null;
    const patterns = [
      /(?:報價|單價|價格|price|quote|offer|¥|￥|\$|RMB|USD)\s*[:：=]?\s*([0-9]+(?:\.[0-9]{1,2})?)/i,
      /([0-9]+(?:\.[0-9]{1,2})?)\s*(?:元|塊|bucks|usd|rmb)/i,
      /(?:給您|特價|最低|含稅)\s*([0-9]+(?:\.[0-9]{1,2})?)/i,
    ];
    for (const pat of patterns) {
      const m = text.match(pat);
      if (m && m[1]) {
        const val = parseFloat(m[1]);
        if (!isNaN(val) && val > 0 && val < 100000) return val;
      }
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // 6. Overlay Host: open Shadow DOM on a PLAIN element (see P0 note above)
  //
  // [P0 REGRESSION — 首次實際執行測試時發現] 舊版用
  // `customElements.define("tradeflow-overlay", TradeFlowOverlay)` 註冊自訂元素。
  // 在 **Manifest V3 的 isolated world** 中，`customElements` 實際是 **null**
  // （量測：`typeof customElements === "object"` 但值為 null —— 即 JS 惡名昭彰的
  // `typeof null === "object"`）。於是 `customElements.define(...)` 在 content
  // script 頂層立即拋
  //     TypeError: Cannot read properties of null (reading 'define')
  // 而整支 content script 就此中斷：HUD 不注入、決策卡不渲染、驗證碼哨兵不作用
  // —— 也就是「擴充完全沒有作用」。
  //
  // 這個 bug 為何能長期逃過審查：所有既有測試都只**閱讀 JavaScript 原始碼字串**
  // （grep assertIn），從來沒有在真實 Chromium 裡載入過擴充；而靜態閱讀時
  // `customElements.define(...)` 看起來完全正常。
  //
  // 修法：不再使用 customElements。改為建立一個普通 <div> 並 `attachShadow`
  // （open），把原本的 render/bind/updateStatus/renderDecisionDeck 邏輯掛到該
  // 實例上。Shadow DOM 隔離性、事件屏障、決策卡 DOM 全部保持不變，且完全不需要
  // 跨 world 注入 <script>（那會被頁面 CSP 阻擋）。
  // ---------------------------------------------------------------------------
  class TradeFlowOverlayHost {
    constructor() {
      // 普通 DOM 容器（不再靠 customElements 升級）。host 元素即頁面上的 HUD 根節點。
      this.el = document.createElement("div");
      this.el.className = "tradeflow-overlay";
      this.el.setAttribute("data-tradeflow", "overlay");
      // opened shadow root：隔離頁面 CSS（等同舊版 :host { all: initial } 的效果）
      this.shadowRoot = this.el.attachShadow({ mode: "open" });
      this._state = {
        active: true,
        mode: "copilot", // "copilot" (default safe assistant) | "autopilot_simulator"
        sessionId: "live-session-001",
        statusText: "COPILOT RADAR ON",
        statusColor: "#10b981",
        subText: "Should-Cost Radar Active • Human in the loop",
        // H6 (F13): the LRU cache used to be the only de-dup, and the observer had
        // no way to tell an operator-sent line from a supplier line. This flag is
        // set while the deck's own copy action runs so that a node synthesised by
        // us can never be re-ingested as an inbound quote.
        suppressObserverOnce: false,
      };
    }

    // 舊版是 custom element 的 connectedCallback()（由瀏覽器在元素插入 DOM 時自動
    // 呼叫）。改為普通元素後沒有這個生命週期，因此改成顯式 mount()，由
    // initOverlay() 在 appendChild 之後立即呼叫 —— 順序與舊版語義等價。
    mount() {
      this.render();
      this.bindEvents();
      this.setupEventBarrier();
    }

    getStyles() {
      return `
        :host {
          all: initial; /* 徹底切斷宿主頁面 CSS 繼承與 !important 污染 */
          position: fixed;
          bottom: 24px;
          right: 24px;
          z-index: 2147483647; /* 最大 32-bit 安全層次 */
          font-family: "Segoe UI", system-ui, -apple-system, "Noto Sans CJK TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", "WenQuanYi Micro Hei", "Droid Sans Fallback", sans-serif;
          font-size: 12px;
          line-height: 1.5;
          overflow-wrap: anywhere;
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
          border-left: 4px solid #46664c; /* updateStatus() sets borderColor inline */
          border-radius: 2px;
          padding: 9px 12px 9px 11px;
          box-shadow: 0 8px 20px rgba(27, 38, 34, 0.18);
          display: flex;
          align-items: center;
          gap: 10px;
          max-width: min(360px, calc(100vw - 16px));
        }

        .tf-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--tf-dot-color, #46664c);
          flex: 0 0 auto;
        }

        .tf-title {
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", "Noto Sans CJK TC", "Noto Sans TC", sans-serif;
          font-size: 13px;
          font-weight: 600;
          letter-spacing: 0.01em;
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 7px;
          min-width: 0;
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
          font-family: "Segoe UI", system-ui, "Noto Sans CJK TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif;
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
          width: min(392px, calc(100vw - 16px));
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
          flex-wrap: wrap;
          gap: 10px;
          padding-bottom: 8px;
          border-bottom: 2px solid #1b2622;
          margin-bottom: 10px;
        }

        .tf-deck-title {
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", "Noto Sans CJK TC", "Noto Sans TC", sans-serif;
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
          line-height: 1.35;
          overflow-wrap: anywhere;
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
          font-family: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", "Noto Sans CJK TC", "Noto Sans TC", sans-serif;
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

        @media (max-width: 420px) {
          :host { right: 8px; bottom: 8px; }
          .tf-deck-container { right: 8px; bottom: 64px; }
        }
`;
    }

    setupEventBarrier() {
      const root = this.shadowRoot.getElementById("tf-root-container");
      if (!root) return;

      const ISOLATED_EVENTS = [
        "click", "dblclick", "mousedown", "mouseup", "pointerdown", "pointerup",
        "keydown", "keyup", "keypress", "contextmenu", "wheel"
      ];

      ISOLATED_EVENTS.forEach((eventType) => {
        root.addEventListener(eventType, (e) => {
          e.stopPropagation();
          e.stopImmediatePropagation();
        }, { capture: true });
      });
    }

    render() {
      this.shadowRoot.innerHTML = `
        <style>${this.getStyles()}</style>
        <div id="tf-root-container">
          <!-- Floating HUD Pill -->
          <div class="tf-hud-pill" id="tf-hud" style="border-color: ${this._state.statusColor};">
            <div class="tf-dot" style="--tf-dot-color: ${this._state.statusColor};"></div>
            <div>
              <div class="tf-title">
                <span style="color: #38bdf8;">TradeFlow Copilot</span>
                <span class="tf-badge" id="tf-badge" style="color: ${this._state.statusColor}; background: ${this._state.statusColor}22;">
                  ${this._state.statusText}
                </span>
              </div>
              <div class="tf-subtext" id="tf-subtext">${this._state.subText}</div>
            </div>
            <button class="tf-btn tf-btn-primary" id="tf-mode-btn">
              ${this._state.mode === "copilot" ? "Copilot Mode" : "Simulator Mode"}
            </button>
            <button class="tf-btn tf-btn-secondary" id="tf-toggle-btn">
              ${this._state.active ? "Pause" : "Resume"}
            </button>
          </div>

          <!-- Decision Deck Popup -->
          <div class="tf-deck-container" id="tf-deck"></div>
        </div>
      `;
    }

    bindEvents() {
      const modeBtn = this.shadowRoot.getElementById("tf-mode-btn");
      const toggleBtn = this.shadowRoot.getElementById("tf-toggle-btn");

      modeBtn?.addEventListener("click", () => {
        this._state.mode = this._state.mode === "copilot" ? "autopilot_simulator" : "copilot";
        modeBtn.textContent = this._state.mode === "copilot" ? "Copilot Mode" : "Simulator Mode";
        modeBtn.style.background = this._state.mode === "copilot" ? "#0369a1" : "#7c3aed";
        this.updateStatus(
          this._state.mode === "copilot" ? "COPILOT RADAR ON" : "SIMULATOR ACTIVE",
          this._state.mode === "copilot" ? "#10b981" : "#a855f7",
          this._state.mode === "copilot" ? "Should-Cost Radar Active • Human in the loop" : "Sandbox Simulator Mode (Demo only)"
        );
      });

      toggleBtn?.addEventListener("click", () => {
        this._state.active = !this._state.active;
        toggleBtn.textContent = this._state.active ? "Pause" : "Resume";
        this.updateStatus(
          this._state.active ? "COPILOT RADAR ON" : "RADAR PAUSED",
          this._state.active ? "#10b981" : "#f59e0b",
          this._state.active ? "Listening for quotes..." : "Manual control engaged"
        );
      });
    }

    updateStatus(statusText, color, subtext) {
      // Legacy state colours are mapped onto the dossier palette here, so
      // every call site keeps its meaning (safe / caution / trip / solving).
      color = TF_INK[color] || color;
      this._state.statusText = statusText;
      this._state.statusColor = color;
      if (subtext) this._state.subText = subtext;

      const hud = this.shadowRoot.getElementById("tf-hud");
      const badge = this.shadowRoot.getElementById("tf-badge");
      const dot = this.shadowRoot.querySelector(".tf-dot");
      const sub = this.shadowRoot.getElementById("tf-subtext");

      if (hud) hud.style.borderColor = color;
      if (badge) {
        badge.textContent = statusText;
        badge.style.color = color;
        badge.style.background = `${color}22`;
      }
      if (dot) dot.style.setProperty("--tf-dot-color", color);
      if (sub && subtext) sub.textContent = subtext;
    }

    renderDecisionDeck(data, offer, counterOffer, onCopy) {
      const deck = this.shadowRoot.getElementById("tf-deck");
      if (!deck) return;

      // [修正/誠實揭露] 舊版 `data.bayesian_belief_state?.deal_probability || 0.68`
      // 在後端沒給成交機率時仍顯示「68%」，是捏造的數字。改為：只有後端真實提供
      // deal_probability 才顯示百分比，否則顯示「—」。
      const dealProb = data.bayesian_belief_state?.deal_probability;
      const winProb = dealProb != null ? Math.round(dealProb * 100) : null;

      // H2 (F3): coerce the backend counter-offer to a real number and escape it
      // before it is interpolated into the deck HTML. Card text is built with a
      // single escaped copy (card1Safe/card2Safe/card3Safe) so the same string is
      // safe in both the visible body and the data-text attribute.
      const counterSafe = tfEsc(tfNum(counterOffer, 0).toFixed(2));
      const marketCtx = (data.session && data.session.spec && data.session.spec.market_context) || null;
      const marketLine = customsReferenceLine(marketCtx);

      const card1 = `【採購預算核定施壓 (推薦)】感謝報價。我方本期專案採購核定單價為 ¥${counterSafe}，現款現結走官方系統，若可配合將可立即簽核首批 PO。`;
      // [修正] 舊版硬寫「保證 15 天交期」「定金提升至 35%」，這些 15/35 是
      // 未經驗證的編造條款，不該由前端虛構。改為不含捏造數字的通用表述。
      const card2 = `【商業條款置換】若能就交期與模具開機費作出改善承諾，我方可就首批定金比例作出相應提升，意向價維持 ¥${counterSafe}。`;
      // [修正] 舊版「* 0.98 給出備選廠出廠價」是捏造的競爭報價，前端沒有可靠來源。
      // 改為只表明存在備選供應商、不宣稱其具體價格。
      const card3 = `【備選供應商威懾】同規格的備選供應商已納入評估，若能就條件貼近我方要求，將有利於鎖定首批排產；具體價差請以多方比價結果為準。`;

      deck.style.display = "block";
      deck.innerHTML = `
        <div class="tf-deck-head">
          <span class="tf-deck-title">${TF_SVG.radar}Procurement Copilot</span>
          <span class="tf-deck-meta">WIN PROB ${winProb == null ? "—" : winProb + "%"}</span>
        </div>
        <div class="tf-deal-row">
          <div>
            <span class="tf-deal-label">Supplier quote</span>
            <span class="tf-deal-figure tf-deal-ask">¥${tfEsc(tfNum(offer, 0).toFixed(2))}</span>
          </div>
          <div>
            <span class="tf-deal-label">Engine counter</span>
            <span class="tf-deal-figure tf-deal-counter">¥${counterSafe}</span>
          </div>
        </div>
        <div class="tf-deck-note">${tfEsc(marketLine)}</div>
        <div class="tf-deck-note">${TF_SVG.cards}Three reply scripts, written in Chinese for the supplier — copy one and paste it into the chat yourself</div>

        <div class="tf-card">
          <div class="tf-card-title">1. Budget-authority pressure (recommended first)</div>
          <div class="tf-card-body">${card1}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card1)}">${TF_SVG.copy}Copy script</button>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">2. Terms swap: deposit for lead time</div>
          <div class="tf-card-body">${card2}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card2)}">${TF_SVG.copy}Copy script</button>
          </div>
        </div>

        <div class="tf-card">
          <div class="tf-card-title">3. Alternative-supplier leverage</div>
          <div class="tf-card-body">${card3}</div>
          <div>
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card3)}">${TF_SVG.copy}Copy script</button>
          </div>
        </div>
      `;

      // H5 (F12): the whole human-in-the-loop design depends on the operator
      // actually receiving the copied text, so a failed clipboard write must NOT
      // show a green "已複製！" tick. Await the promise and surface failure.
      deck.querySelectorAll(".tf-copy-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const text = decodeURIComponent(btn.getAttribute("data-text") || "");
          const restore = () => {
            btn.innerHTML = `${TF_SVG.copy}Copy script`;
            btn.style.borderColor = "";
          };
          try {
            await navigator.clipboard.writeText(text);
            if (onCopy) onCopy(text);
            btn.innerHTML = `${TF_SVG.check}Copied`;
            btn.style.borderColor = "#46664c";
            setTimeout(restore, 2000);
          } catch (err) {
            // Do NOT pretend the copy worked. Tell the operator to copy manually
            // and keep the failure visible (no auto-reset) so it cannot be missed.
            console.warn("[TradeFlow Copilot] Clipboard write failed:", err);
            btn.innerHTML = `${TF_SVG.copy}Copy failed — select manually`;
            btn.style.borderColor = "#8d641b";
            // Fallback: expose the raw card text so the operator can select it
            // manually (the whole design assumes a human sends the message).
            btn.title = text;
          }
        });
      });
    }
  }

  // 建立 HUD 物件：不用 customElements（isolated world 中為 null，見上方 P0 說明）。
  // TradeFlowOverlayHost 只是普通的 JS 類別（不再 extends HTMLElement），
  // 它在自己的 constructor 內建立一個帶 open shadow root 的容器 <div>。
  const TF_INK = {
    "#10b981": "#46664c", // safe / active
    "#f59e0b": "#8d641b", // caution / paused
    "#f43f5e": "#a33a2a", // tripped
    "#38bdf8": "#2e4f63", // engine working
    "#06b6d4": "#2e4f63",
    "#a855f7": "#7d2a1e", // simulator mode
    "#94a3b8": "#8b968d", // idle
  };

  const OVERLAY_TAG = "tradeflow-overlay";

  // ---------------------------------------------------------------------------
  // 7. Resilient Port Client (Exponential Backoff + Jitter)
  // ---------------------------------------------------------------------------
  class RobustPortClient {
    constructor() {
      this.port = null;
      this.retryCount = 0;
      this.maxRetryDelay = 15000;
      this.handlers = new Map();
      this.connect();
    }

    connect() {
      try {
        this.port = chrome.runtime.connect({ name: "tf-content" });
        this.retryCount = 0;
        console.log("[TradeFlow Port] Connected to background SW.");

        this.port.onMessage.addListener((msg) => {
          const handler = this.handlers.get(msg.type);
          if (handler) handler(msg);
        });

        this.port.onDisconnect.addListener(() => {
          console.warn("[TradeFlow Port] Port disconnected. Reconnecting with backoff...");
          this.scheduleReconnect();
        });
      } catch (err) {
        console.error("[TradeFlow Port] Connection error:", err);
        this.scheduleReconnect();
      }
    }

    scheduleReconnect() {
      this.port = null;
      this.retryCount++;
      const baseDelay = Math.min(this.maxRetryDelay, 1000 * Math.pow(1.5, this.retryCount));
      const jitter = Math.floor(Math.random() * 500);
      setTimeout(() => this.connect(), baseDelay + jitter);
    }

    on(type, callback) {
      this.handlers.set(type, callback);
    }

    send(type, payload) {
      if (this.port) {
        try {
          this.port.postMessage({ type, payload });
        } catch (e) {
          console.warn("[TradeFlow Port] Send failed:", e);
        }
      }
    }
  }

  // ---------------------------------------------------------------------------
  // 8. Targeted DOM Chat Observer Manager (Discovery -> Targeted Binding)
  // ---------------------------------------------------------------------------
  // H6 (F13): the observer used to treat EVERY newly added element containing a
  // price as an inbound SUPPLIER quote. When the operator pastes their own
  // counter-offer into the chat, that same node is added to the observed
  // container and was re-ingested as a supplier concession — corrupting the
  // Bayesian posterior and letting the buyer's own text trip the circuit
  // breaker against them.
  //
  // Direction filter: only nodes that look like a RECEIVED message are ingested.
  // We read the class list of the node and its nearest ancestors/descendants for
  // any "sent" marker (the platforms in manifest.json use msg-sent /
  // message-out / chat-msg--out / outgoing, matching extension/mock_chat.html's
  // msg-received vs msg-sent convention) and for an explicit "received" marker.
  // A node that is clearly "sent" is dropped; a node that is clearly "received"
  // is kept; an unmarked node keeps the legacy behaviour (ingest) so generic
  // sites keep working.
  //
  // Matching is per class TOKEN, not a substring of the joined class string:
  // substring matching would misclassify e.g. `some-thing` (contains "me-") or
  // `absent` (contains "sent") as a sent message.
  const SENT_CLASS_TOKENS = new Set([
    "msg-sent", "message-out", "chat-msg--out", "outgoing", "sent", "self", "own-message",
  ]);
  const SENT_CLASS_PREFIXES = ["msg-sent-", "message-out-", "chat-msg--out-", "outgoing-", "own-message-", "self-", "sent-"];

  function isSentClass(cls) {
    const c = String(cls).toLowerCase();
    if (SENT_CLASS_TOKENS.has(c)) return true;
    return SENT_CLASS_PREFIXES.some((p) => c.startsWith(p));
  }

  function messageDirection(node) {
    if (!node || !node.classList) return "unknown";
    const parts = [];
    const collect = (el) => {
      if (el && el.classList) parts.push(...Array.from(el.classList));
    };

    // Walk up a few levels: platforms often put the direction class on a
    // wrapper rather than the bubble itself.
    let el = node;
    for (let depth = 0; el && depth < 4; depth++) {
      collect(el);
      // Include directly nested bubbles too (the added node may be a wrapper).
      el.querySelectorAll?.("[class*='msg'], [class*='message'], [class*='chat-msg']").forEach(collect);
      el = el.parentElement;
    }

    const tokens = parts.map((p) => String(p).toLowerCase());
    if (tokens.some(isSentClass)) return "sent";
    const hasReceived = tokens.some(
      (t) =>
        t === "msg-received" || t === "message-in" || t === "incoming" || t === "received" ||
        t.startsWith("msg-received-") || t.startsWith("message-in-") || t.startsWith("incoming-")
    );
    if (hasReceived) return "received";
    return "unknown";
  }

  class DOMChatObserverManager {
    constructor(onNewMessage) {
      this.onNewMessage = onNewMessage;
      this.lruCache = new MessageLRUCache(100);
      this.discoveryObserver = null;
      this.chatBoxObserver = null;
      this.debounceTimer = null;
      this.isDestroyed = false;
    }

    start() {
      this.findAndBindChatBox();
      this.bindLifecycleHooks();
    }

    findAndBindChatBox() {
      const chatContainer = document.querySelector(
        ".chat-messages, .im-message-box, #messages-box, .chat-list-left, #chatMessages, .messages-container"
      );

      if (chatContainer) {
        this.bindTargetedChatObserver(chatContainer);
      } else {
        // Shallow discovery observer on document.body
        this.discoveryObserver = new MutationObserver(() => {
          const found = document.querySelector(
            ".chat-messages, .im-message-box, #messages-box, .chat-list-left, #chatMessages, .messages-container"
          );
          if (found) {
            this.discoveryObserver.disconnect();
            this.discoveryObserver = null;
            this.bindTargetedChatObserver(found);
          }
        });
        this.discoveryObserver.observe(document.body, { childList: true });
      }
    }

    bindTargetedChatObserver(container) {
      if (this.chatBoxObserver) {
        this.chatBoxObserver.disconnect();
      }

      this.chatBoxObserver = new MutationObserver((mutations) => {
        if (this.isDestroyed) return;

        for (const mutation of mutations) {
          if (mutation.type === "childList" && mutation.addedNodes.length > 0) {
            mutation.addedNodes.forEach((node) => {
              if (node.nodeType === Node.ELEMENT_NODE) {
                // H6 (F13): do not ingest the buyer's own (sent) message as an
                // inbound supplier quote.
                if (messageDirection(node) === "sent") return;

                const text = (node.innerText || node.textContent || "").trim();
                if (text && !this.lruCache.has(text)) {
                  this.lruCache.add(text);
                  this.debouncedTrigger(text);
                }
              }
            });
          }
        }
      });

      this.chatBoxObserver.observe(container, { childList: true });
      console.log("[TradeFlow] Targeted ChatObserver successfully attached to container.");
    }

    debouncedTrigger(text) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        if (!this.isDestroyed && this.onNewMessage) {
          this.onNewMessage(text);
        }
      }, 350);
    }

    bindLifecycleHooks() {
      const cleanup = () => this.destroy();
      window.addEventListener("pagehide", cleanup);
      window.addEventListener("beforeunload", cleanup);
    }

    destroy() {
      this.isDestroyed = true;
      if (this.discoveryObserver) {
        this.discoveryObserver.disconnect();
        this.discoveryObserver = null;
      }
      if (this.chatBoxObserver) {
        this.chatBoxObserver.disconnect();
        this.chatBoxObserver = null;
      }
      clearTimeout(this.debounceTimer);
      this.lruCache.clear();
      console.log("[TradeFlow] ChatObserver cleanly destroyed.");
    }
  }

  // ---------------------------------------------------------------------------
  // 9. Main Orchestration
  // ---------------------------------------------------------------------------
  let overlayEl = null;
  let portClient = null;
  let observerManager = null;
  const sessionContext = extractSessionContext();
  let circuitTripped = false;
  let captchaPaused = false;

  function initOverlay() {
    // 重複注入防護：HUD 以 data-tradeflow 屬性標記（不再依賴自訂標籤名）。
    if (document.querySelector('[data-tradeflow="overlay"]')) return;
    overlayEl = new TradeFlowOverlayHost();
    overlayEl._state.sessionId = sessionContext.sessionId;
    // 先 mount()（render + bind + event barrier）再插入文件，避免任何
    // 依賴 layout 的綁定在未插入狀態下執行。
    overlayEl.mount();
    document.body.appendChild(overlayEl.el);
  }

  async function handleInboundQuote(rawText) {
    if (!overlayEl || !overlayEl._state.active) return;

    // 1. Captcha Sentinel check
    // [P0-3 修正] 驗證碼判定與復原現在都在 early return 之前。舊版的最前面是
    // `if (!active || circuitTripped) return;`，而驗證碼觸發時會設
    // circuitTripped = true（:795）—— 負責復原的 captchaPaused 分支因此永遠
    // 到不了，頁面只要出現一次風控滑塊就永久凍結（HUD 釘在 CAPTCHA DETECTED），
    // 只能重新載入頁面。滑塊通過後現在會清掉旗標並恢復。
    if (detectCaptchaChallenge()) {
      if (!captchaPaused) {
        captchaPaused = true;
        circuitTripped = true;
        overlayEl.updateStatus(
          "CAPTCHA DETECTED",
          "#f59e0b",
          "Platform verification challenge active! Please slide manually."
        );
        portClient.send("TELEMETRY_ALERT", {
          sessionId: sessionContext.sessionId,
          message: "1688/Alibaba Captcha challenge detected! Autopilot paused.",
        });
      }
      return;
    }

    if (captchaPaused) {
      captchaPaused = false;
      circuitTripped = false;
      overlayEl.updateStatus("COPILOT RADAR ON", "#10b981", "Captcha solved! Copilot resumed.");
      // 復原時一併重新註冊本分頁的 session 綁定：暫停期間 service worker 可能
      // 已被 MV3 回收，或 registry 已被其他分頁的 REGISTER_SESSION 覆寫。
      portClient.send("REGISTER_SESSION", {
        ...sessionContext,
        mode: overlayEl._state.mode === "copilot" ? "copilot" : "autopilot",
      });
    }

    // [P0-3 修正/偏差說明] 熔斷旗標不再阻斷後續輪次。舊版的 `|| circuitTripped`
    // 是第二個不可復原的死結：熔斷後不再送出任何請求，後端就永遠沒有機會重算
    // 風險並解除熔斷，人類經 /approve 或手機指令解除後前端也不會回來
    // （DEMO_VIDEO_DIRECTOR_SCRIPT.md:52 的「系統瞬間解鎖、恢復正常」敘事即失效）。
    // 現在熔斷是「渲染層」狀態：SESSION_UPDATED 會據後端真實旗標抑制決策卡，
    // 但報價仍持續送引擎重評，解除後下一輪即恢復正常。

    // 2. Extract price
    const offer = extractPriceFromText(rawText);
    if (!offer) return;

    console.log(`[TradeFlow Copilot] Detected quote ¥${offer} in session ${sessionContext.sessionId}`);
    // [誠實揭露] 決策核心是向量化 Expectimax（+ Bayesian v2 信念更新），
    // 不是 MCTS。舊字串 "Bayesian MCTS" 是錯誤的演算法宣稱，demo 時會被看見。
    overlayEl.updateStatus("ENGINE SOLVING...", "#38bdf8", `Processing quote ¥${offer} via Expectimax + Bayesian v2`);

    // 3. Dispatch to background via Port
    // V1 (F6) CONFIRMED: neither content.js nor background.js ever sent a `mode`
    // field, so every round hit the backend's autonomous branch
    // (RoundActionRequest.mode defaults to "autopilot", main.py:367), even while
    // the HUD advertised "Copilot Mode" with a human-in-the-loop gate. Pass the
    // operator's selected mode through; the simulator mode maps to the backend's
    // "autopilot" value.
    portClient.send("SUBMIT_OFFER", {
      sessionId: sessionContext.sessionId,
      offer: offer,
      context: rawText,
      mode: overlayEl._state.mode === "copilot" ? "copilot" : "autopilot",
    });
  }

  // Copilot 模式說明：本擴充套件**只提供剪貼簿複製**，不寫入聊天框、也不代為發送。
  //
  // 為何不直接寫入聊天框並代發？
  // 1. 法務：在即時通訊中確認品名/價格/定金即構成法定要約，締約權必須留在人類手上。
  // 2. 風控：偽造 input/change 合成事件會被 1688/千牛判定為非人類操作而封號。
  // 詳見 TARGET_ARCHITECTURE_AND_ROADMAP.md §6.1。
  // 決策卡的複製行為由 renderDecisionDeck() 內的 .tf-copy-btn 處理。

  let tripThreshold = null;

  async function loadHeuristics() {
    try {
      const res = await fetch("http://localhost:8000/api/v1/heuristics");
      if (!res.ok) return;
      const data = await res.json();
      const n = Number(data.breakdown_trip_threshold);
      if (Number.isFinite(n)) tripThreshold = n;
    } catch (err) {
      tripThreshold = null;
    }
  }

  function setup() {
    initOverlay();
    loadHeuristics();
    portClient = new RobustPortClient();

    // Register this tab's dynamic session context
    portClient.send("REGISTER_SESSION", {
      ...sessionContext,
      // V1 (F6): record the HUD's selected mode alongside the context so the
      // tab registry does not claim "autopilot" while the overlay shows Copilot.
      mode: overlayEl?._state.mode === "copilot" ? "copilot" : "autopilot",
    });

    // Listen for Engine updates
    portClient.on("SESSION_UPDATED", (msg) => {
      const data = msg.data;
      if (!data) return;

      // [P0-2 修正] 改讀後端「真實」契約欄位（已核對 src/app/main.py 的 copilot
      // 分支 :1551-1563、autopilot 分支 :1646-1668、熔斷分支 :1320-1329，以及
      // NegotiationTurn 契約 src/session/schema.py:52-64）：
      //   turn 在 copilot 模式恆為 null，且 NegotiationTurn 根本沒有
      //   supplier_offer / buyer_counter_offer / breakdown_risk / round_number。
      //   舊版讀 data.turn?.supplier_offer 永遠是 undefined → 3 張決策卡
      //   「任何模式」都不會渲染，熔斷判定也永遠不成立。
      //   • 供應商報價 → effective_opp_price（TCO 等效）/ action_object.counterpart_offered_price
      //   • 我方還盤價 → recommended_offer_price / action_object.authorized_price
      //   • 破裂風險   → action_object.breakdown_risk / turn.p_break
      const actionObj = data.action_object || {};
      const sessionObj = data.session || {};
      const offer = data.effective_opp_price ?? actionObj.counterpart_offered_price ?? null;
      const counterOffer = data.recommended_offer_price ?? actionObj.authorized_price ?? null;
      const risk = actionObj.breakdown_risk ?? data.turn?.p_break ?? null;
      const roundNo = sessionObj.current_round ?? data.turn?.round_index ?? null;
      const stateName = data.autopilot_state || sessionObj.state || null;

      // REGISTER_SESSION 會回推只有 sessionMeta 的 payload（background.js
      // broadcastSessionUpdate）：那不是引擎回應，不該當成錯誤或渲染依據。
      const hasEnginePayload =
        data.session || data.action_object || data.turn || data.effective_opp_price != null;
      if (!hasEnginePayload) return;

      // Check Circuit Breaker（改用後端明確旗標 + 真實風險值）
      const tripped =
        data.circuit_tripped === true ||
        stateName === "CIRCUIT_TRIPPED_ESCALATED" ||
        (risk != null && tripThreshold != null && risk >= tripThreshold);

      if (tripped) {
        circuitTripped = true;
        overlayEl.updateStatus(
          "CIRCUIT TRIPPED",
          "#ef4444",
          // [修正] 舊版 `|| 0.15` 在無風險值時也顯示 15.0%——改為只顯示後端有給的值。
          `Breakdown risk ${risk != null ? (risk * 100).toFixed(1) : "—"}% — engine withheld the reply; resolve via human authorization (/approve or mobile command 1/2/3).`
        );
        return;
      }
      circuitTripped = false;

      if (offer == null || counterOffer == null) {
        // turn:null 是 copilot 模式的「正常」回應形狀（main.py:1551-1563），
        // 不是錯誤。此時顯示明確佔位狀態（而非不渲染 → HUD 永遠卡在
        // ENGINE SOLVING...），且絕不補上捏造的數字。
        overlayEl.updateStatus(
          "AWAITING PRICED QUOTE",
          "#94a3b8",
          `Round ${roundNo != null ? roundNo : "—"} • backend returned no priced offer/counter — no cards rendered (no fabricated figures).`
        );
        return;
      }

      overlayEl.updateStatus(
        "COPILOT CARDS READY",
        "#10b981",
        `Target Counter-Offer ¥${tfNum(counterOffer, 0).toFixed(2)} • Round ${roundNo != null ? roundNo : "—"} • 3 Tactical Cards Generated`
      );

      overlayEl.renderDecisionDeck(
        data,
        offer,
        counterOffer,
        (text) => console.log("[TradeFlow Copilot] Copied:", text.slice(0, 30))
      );
    });

    portClient.on("ENGINE_ERROR", (msg) => {
      overlayEl.updateStatus("ENGINE ERROR", "#ef4444", msg.error || "Offline fallback");
    });

    // Start Targeted Chat Observer
    observerManager = new DOMChatObserverManager((text) => handleInboundQuote(text));
    observerManager.start();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
