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
    radar: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:5px; display:inline-block;"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/><path d="m4.93 4.93 4.24 4.24"/><circle cx="12" cy="12" r="2"/></svg>`,
    cards: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:5px; display:inline-block;"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
    edit: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>`,
    copy: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>`,
    check: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px; margin-right:4px; display:inline-block;"><polyline points="20 6 9 17 4 12"/></svg>`,
    shield: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:4px; display:inline-block;"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
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

  function extractSessionContext() {
    const url = new URL(window.location.href);

    // 1. URL search params
    const candidateParams = ["sellerId", "memberId", "chat_id", "supplier_id", "conversation_id", "session_id", "id"];
    for (const p of candidateParams) {
      const val = url.searchParams.get(p);
      if (val && val.length > 2) {
        const cleanId = val.replace(/[^a-zA-Z0-9_-]/g, "_");
        return {
          sessionId: `sess_${cleanId}`,
          supplierId: cleanId,
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
        const cleanId = rawName.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5-]/g, "_");
        return {
          sessionId: `sess_${cleanId}`,
          supplierId: cleanId,
          supplierName: rawName,
          platform: detectPlatform(),
          url: window.location.href,
        };
      }
    }

    // 3. Document title or pathname fallback
    const pathPart = (url.hostname + url.pathname).replace(/[^a-zA-Z0-9_-]/g, "_").slice(-20);
    return {
      sessionId: `sess_tab_${pathPart || "001"}`,
      supplierId: pathPart || "supp_001",
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
  const PLATFORMS = {
    c1688: {
      messageBubbles: ".im-message-item, .chat-msg-received, .msg-bubble, .message-receive",
      inputField: ".chat-box textarea, .im-input-area textarea, #chatInput",
      sendButton: ".im-send-btn, .btn-primary, #sendBtn",
    },
    alibaba: {
      messageBubbles: ".im-msg-item, .chat-message-item, .message-bubble, .chat-item-receive",
      inputField: ".chat-input textarea, textarea[placeholder*='message'], #chatInput",
      sendButton: ".chat-send-btn, button[type='submit'], .btn-send",
    },
    globalsources: {
      messageBubbles: ".msg-item-left, .chat-history-item-left, .receiver-msg",
      inputField: ".chat-input-textarea, textarea.msg-textarea",
      sendButton: ".btn-msg-send, button.send-btn",
    },
    made_in_china: {
      messageBubbles: ".chat-list-left, .receive-bubble, .msg-content-left",
      inputField: ".input-content textarea, textarea#msgInput",
      sendButton: ".btn-send-msg, button#sendBtn",
    },
    whatsapp: {
      messageBubbles: ".message-in, div[data-pre-plain-text]",
      inputField: "footer div[contenteditable='true']",
      sendButton: "button[data-testid='compose-btn-send'], span[data-icon='send']",
    },
    generic: {
      messageBubbles: ".msg-received, .inbound-message, [data-role='seller'], .incoming, .chat-left, .message-left",
      inputField: "textarea, input[type='text'], div[contenteditable='true']",
      sendButton: "button[type='submit'], .send-button, #sendBtn, .btn-send",
    },
  };

  // 注意：本擴充套件為 Copilot 模式，**刻意不提供**任何自動填入聊天框或自動點擊送出的能力。
  // 相關的 findChatInput() / findSendButton() 已移除，以避免留下可被誤用的自動締約路徑。
  // 採購員透過決策卡的「複製」按鈕取得草稿，自行貼上並手動發送。

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
  // 6. Web Component: <tradeflow-overlay> (Open Shadow DOM & Event Barrier)
  // ---------------------------------------------------------------------------
  class TradeFlowOverlay extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._state = {
        active: true,
        mode: "copilot", // "copilot" (default safe assistant) | "autopilot_simulator"
        sessionId: "live-session-001",
        statusText: "COPILOT RADAR ON",
        statusColor: "#10b981",
        subText: "Should-Cost Radar Active • Human in the loop",
      };
    }

    connectedCallback() {
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
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          font-size: 13px;
          line-height: 1.5;
          color-scheme: dark;
          pointer-events: auto;
          user-select: none;
        }

        *, *::before, *::after {
          box-sizing: border-box !important;
          margin: 0;
          padding: 0;
        }

        .tf-hud-pill {
          background: #0f172a;
          color: #f8fafc;
          border: 1px solid #334155;
          border-radius: 12px;
          padding: 12px 18px;
          box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6), 0 0 15px rgba(16, 185, 129, 0.2);
          display: flex;
          align-items: center;
          gap: 12px;
          transition: border-color 0.3s ease, box-shadow 0.3s ease;
        }

        .tf-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--tf-dot-color, #10b981);
          box-shadow: 0 0 8px var(--tf-dot-color, #10b981);
          animation: tf-pulse 2s infinite ease-in-out;
        }

        @keyframes tf-pulse {
          0% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.35; transform: scale(0.85); }
          100% { opacity: 1; transform: scale(1); }
        }

        .tf-title {
          font-weight: 700;
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .tf-badge {
          font-size: 11px;
          padding: 2px 6px;
          border-radius: 4px;
          font-weight: 600;
        }

        .tf-subtext {
          color: #94a3b8;
          font-size: 11px;
          margin-top: 2px;
        }

        .tf-btn {
          border-radius: 6px;
          cursor: pointer;
          font-size: 11px;
          font-weight: 600;
          padding: 6px 10px;
          border: 1px solid transparent;
          transition: all 0.2s;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }

        .tf-btn:active {
          transform: scale(0.96);
        }

        .tf-btn-primary {
          background: #0369a1;
          border-color: #38bdf8;
          color: #f1f5f9;
        }

        .tf-btn-secondary {
          background: #1e293b;
          border-color: #475569;
          color: #f1f5f9;
        }

        /* Decision Deck Card Overlay */
        .tf-deck-container {
          position: fixed;
          bottom: 84px;
          right: 24px;
          width: 380px;
          background: #0f172a;
          border: 1px solid #334155;
          border-radius: 12px;
          padding: 14px;
          box-shadow: 0 12px 30px rgba(0, 0, 0, 0.7);
          color: #f8fafc;
          display: none;
        }

        .tf-card {
          background: #1e293b;
          border: 1px solid #334155;
          border-radius: 6px;
          padding: 8px;
          margin-bottom: 8px;
        }

        .tf-card:last-child {
          margin-bottom: 0;
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

      const winProb = Math.round((data.bayesian_belief_state?.deal_probability || 0.68) * 100);

      const card1 = `【採購預算核定施壓 (推薦)】感謝報價。我方本期專案採購核定單價為 ¥${counterOffer}，現款現結走官方系統，若可配合將可立即簽核首批 PO。`;
      const card2 = `【商業條款置換】若能保證 15 天交期且免除模具開機費，我方首批定金可提升至 35%，意向單價為 ¥${counterOffer}。`;
      const card3 = `【備選供應商威懾】同規格備選廠出廠價在 ¥${(counterOffer * 0.98).toFixed(2)} 附近，若無法貼近我們將啟動第二供應商排產。`;

      deck.style.display = "block";
      deck.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 10px;">
          <span style="font-weight:700; color:#38bdf8; display:inline-flex; align-items:center;">${TF_SVG.radar}採購決策副駕駛 (Copilot Decision Deck)</span>
          <span style="font-size:10px; background:#1e293b; color:#94a3b8; padding:2px 6px; border-radius:4px;">WIN PROB: ${winProb}%</span>
        </div>
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; background: #1e293b; padding: 8px; border-radius: 8px;">
          <div>供應商報價: <b style="color:#f59e0b;">¥${parseFloat(offer).toFixed(2)}</b></div>
          <div>建議還盤價: <b style="color:#10b981;">¥${counterOffer}</b></div>
        </div>
        <div style="font-weight:600; color:#94a3b8; margin-bottom:6px; display:inline-flex; align-items:center;">${TF_SVG.cards}3 條商務反擊卡 (複製後請自行貼上發送)：</div>

        <div class="tf-card">
          <div style="font-weight:600; color:#38bdf8; margin-bottom:4px;">1. 採購預算核定施壓 (推薦)</div>
          <div style="color:#cbd5e1; font-size:11px; margin-bottom:6px; line-height:1.4;">${card1}</div>
          <div style="display:flex; gap:6px;">
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card1)}">${TF_SVG.copy}複製話術</button>
          </div>
        </div>

        <div class="tf-card">
          <div style="font-weight:600; color:#06b6d4; margin-bottom:4px;">2. 條款置換 (定金換交期)</div>
          <div style="color:#cbd5e1; font-size:11px; margin-bottom:6px; line-height:1.4;">${card2}</div>
          <div style="display:flex; gap:6px;">
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card2)}">${TF_SVG.copy}複製話術</button>
          </div>
        </div>

        <div class="tf-card">
          <div style="font-weight:600; color:#f59e0b; margin-bottom:4px;">3. 備選工廠威懾</div>
          <div style="color:#cbd5e1; font-size:11px; margin-bottom:6px; line-height:1.4;">${card3}</div>
          <div style="display:flex; gap:6px;">
            <button class="tf-btn tf-btn-primary tf-copy-btn" data-text="${encodeURIComponent(card3)}">${TF_SVG.copy}複製話術</button>
          </div>
        </div>
      `;

      deck.querySelectorAll(".tf-copy-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const text = decodeURIComponent(btn.getAttribute("data-text") || "");
          navigator.clipboard.writeText(text);
          if (onCopy) onCopy(text);
          btn.innerHTML = `${TF_SVG.check}已複製！`;
          setTimeout(() => { btn.innerHTML = `${TF_SVG.copy}複製話術`; }, 2000);
        });
      });
    }
  }

  customElements.define("tradeflow-overlay", TradeFlowOverlay);

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
    if (document.querySelector("tradeflow-overlay")) return;
    overlayEl = document.createElement("tradeflow-overlay");
    overlayEl._state.sessionId = sessionContext.sessionId;
    document.body.appendChild(overlayEl);
  }

  async function handleInboundQuote(rawText) {
    if (!overlayEl || !overlayEl._state.active || circuitTripped) return;

    // 1. Captcha Sentinel check
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
    } else if (captchaPaused) {
      captchaPaused = false;
      circuitTripped = false;
      overlayEl.updateStatus("COPILOT RADAR ON", "#10b981", "Captcha solved! Copilot resumed.");
    }

    // 2. Extract price
    const offer = extractPriceFromText(rawText);
    if (!offer) return;

    console.log(`[TradeFlow Copilot] Detected quote ¥${offer} in session ${sessionContext.sessionId}`);
    overlayEl.updateStatus("ENGINE SOLVING...", "#38bdf8", `Processing quote ¥${offer} via Bayesian MCTS`);

    // 3. Dispatch to background via Port
    portClient.send("SUBMIT_OFFER", {
      sessionId: sessionContext.sessionId,
      offer: offer,
      context: rawText,
    });
  }

  // Copilot 模式說明：本擴充套件**只提供剪貼簿複製**，不寫入聊天框、也不代為發送。
  //
  // 為何不直接寫入聊天框並代發？
  // 1. 法務：在即時通訊中確認品名/價格/定金即構成法定要約，締約權必須留在人類手上。
  // 2. 風控：偽造 input/change 合成事件會被 1688/千牛判定為非人類操作而封號。
  // 詳見 TARGET_ARCHITECTURE_AND_ROADMAP.md §6.1。
  // 決策卡的複製行為由 renderDecisionDeck() 內的 .tf-copy-btn 處理。

  function setup() {
    initOverlay();
    portClient = new RobustPortClient();

    // Register this tab's dynamic session context
    portClient.send("REGISTER_SESSION", sessionContext);

    // Listen for Engine updates
    portClient.on("SESSION_UPDATED", (msg) => {
      const data = msg.data;
      if (!data) return;

      const offer = data.turn?.supplier_offer || 15.0;
      const counterOffer = data.recommended_offer_price || data.turn?.buyer_counter_offer || (offer * 0.95).toFixed(2);

      // Check Circuit Breaker
      if (data.session?.state === "CIRCUIT_TRIPPED_ESCALATED" || data.turn?.breakdown_risk >= 0.15) {
        circuitTripped = true;
        overlayEl.updateStatus(
          "CIRCUIT TRIPPED",
          "#ef4444",
          `Breakdown risk ${((data.turn?.breakdown_risk || 0.15) * 100).toFixed(1)}% ≥ 15%. Telemetry alert sent!`
        );
        return;
      }

      overlayEl.updateStatus(
        "COPILOT CARDS READY",
        "#10b981",
        `Target Counter-Offer ¥${counterOffer} • 3 Tactical Cards Generated`
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
