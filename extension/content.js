/**
 * TradeFlow Autopilot — In-Browser Autonomous Negotiation Content Script
 * 
 * Features:
 * 1. Streaming DOM MutationObserver with 400ms debounce & message deduplication.
 * 2. Multi-platform chat bubble selector matching (1688, Alibaba, WhatsApp Web, Generic).
 * 3. Anti-Bot Human Emulation:
 *    - Reading latency: T_delay = 1200 + L * 25 + Uniform(-300, 450) ms
 *    - Keypress typing jitter: 40 ~ 180 ms / char
 *    - React / Vue virtual DOM synthetic event penetration
 * 4. Visual HUD status pill & emergency safety lock on circuit trip.
 */

(() => {
  console.log("[TradeFlow Autopilot] Content script injected successfully.");

  // State Management
  const state = {
    active: true,
    sessionId: "live-session-001",
    circuitTripped: false,
    captchaPaused: false,
    processedMessages: new Set(),
    debounceTimer: null,
  };

  // Anti-Bot Risk & Captcha Selectors (1688 / Alibaba / Generic Cloudflare)
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
      if (el && el.offsetParent !== null) { // 元素可見
        return true;
      }
    }
    return false;
  }

  // 1. In-Page Floating HUD Overlay
  function injectHUD() {
    if (document.getElementById("tradeflow-hud-container")) return;

    const hud = document.createElement("div");
    hud.id = "tradeflow-hud-container";
    hud.innerHTML = `
      <div id="tradeflow-hud" style="
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f172a;
        color: #f8fafc;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 12px 18px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 0 15px rgba(16, 185, 129, 0.2);
        display: flex;
        align-items: center;
        gap: 12px;
        font-size: 13px;
        user-select: none;
        transition: all 0.3s ease;
      ">
        <div id="tradeflow-status-dot" style="
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: #10b981;
          box-shadow: 0 0 8px #10b981;
          animation: pulse 2s infinite;
        "></div>
        <div>
          <div style="font-weight: 700; display: flex; align-items: center; gap: 6px;">
            <span style="color: #38bdf8;">TradeFlow AI</span>
            <span id="tradeflow-state-label" style="color: #10b981; font-size: 11px; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">AUTOPILOT CRUISING</span>
          </div>
          <div id="tradeflow-subtext" style="color: #94a3b8; font-size: 11px; margin-top: 2px;">
            Bayesian MCTS Active • Listening for quotes...
          </div>
        </div>
        <button id="tradeflow-toggle-btn" style="
          background: #1e293b;
          border: 1px solid #475569;
          color: #f1f5f9;
          padding: 6px 10px;
          border-radius: 6px;
          cursor: pointer;
          font-size: 11px;
          font-weight: 600;
        ">Pause</button>
      </div>
      <style>
        @keyframes pulse {
          0% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.9); }
          100% { opacity: 1; transform: scale(1); }
        }
      </style>
    `;
    document.body.appendChild(hud);

    document.getElementById("tradeflow-toggle-btn")?.addEventListener("click", () => {
      state.active = !state.active;
      updateHUD(
        state.active ? "AUTOPILOT CRUISING" : "AUTOPILOT PAUSED",
        state.active ? "#10b981" : "#f59e0b",
        state.active ? "Listening for quotes..." : "Manual control engaged"
      );
      const btn = document.getElementById("tradeflow-toggle-btn");
      if (btn) btn.innerText = state.active ? "Pause" : "Resume";
    });
  }

  function updateHUD(statusText, color, subtext) {
    const label = document.getElementById("tradeflow-state-label");
    const dot = document.getElementById("tradeflow-status-dot");
    const sub = document.getElementById("tradeflow-subtext");
    const hud = document.getElementById("tradeflow-hud");
    if (label) {
      label.innerText = statusText;
      label.style.color = color;
      label.style.background = `${color}25`;
    }
    if (dot) {
      dot.style.background = color;
      dot.style.boxShadow = `0 0 8px ${color}`;
    }
    if (sub && subtext) sub.innerText = subtext;
    if (hud) hud.style.borderColor = color;
  }

  // 2. Chat Selectors Multi-Platform Strategy
  // 2. Chat Selectors Multi-Platform Strategy (Universal Support)
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
    wechat_web: {
      messageBubbles: ".bubble_left, .chat_item_left, .you",
      inputField: "#editArea, div[contenteditable='true']",
      sendButton: ".btn_send, button.send-btn",
    },
    generic: {
      messageBubbles: ".msg-received, .inbound-message, [data-role='seller'], .incoming, .chat-left, .message-left",
      inputField: "textarea, input[type='text'], div[contenteditable='true']",
      sendButton: "button[type='submit'], .send-button, #sendBtn, .btn-send",
    },
  };

  function findChatInput() {
    for (const key of Object.keys(PLATFORMS)) {
      const el = document.querySelector(PLATFORMS[key].inputField);
      if (el) return el;
    }
    return document.querySelector("textarea, input[type='text'], [contenteditable='true']");
  }

  function findSendButton() {
    for (const key of Object.keys(PLATFORMS)) {
      const el = document.querySelector(PLATFORMS[key].sendButton);
      if (el) return el;
    }
    return document.querySelector("button[type='submit'], button:has(svg), .send-btn");
  }

  // 3. Price Ladder Extraction
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

  // 4. Anti-Bot Human Emulation: Natural Reading & Typing
  function calculateReadingDelay(text) {
    const len = text ? text.length : 10;
    // T_delay = 1200 + L * 25 + Uniform(-300, 450) ms
    const jitter = Math.floor(Math.random() * 750) - 300;
    return Math.max(900, 1200 + len * 25 + jitter);
  }

  async function humanType(element, text) {
    element.focus();

    // Check if element is contenteditable
    const isContentEditable = element.isContentEditable || element.getAttribute("contenteditable") === "true";

    if (isContentEditable) {
      element.innerHTML = "";
    } else {
      element.value = "";
    }

    // Jittered keystroke typing: 40 ~ 140ms per char
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (isContentEditable) {
        element.innerText += char;
      } else {
        // React/Vue setter penetration
        const prototype = Object.getPrototypeOf(element);
        const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
        if (descriptor && descriptor.set) {
          descriptor.set.call(element, element.value + char);
        } else {
          element.value += char;
        }
      }

      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
      element.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));

      const keyJitter = 40 + Math.floor(Math.random() * 90);
      await new Promise((r) => setTimeout(r, keyJitter));
    }

    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // 5. Autopilot Process Turn
  async function handleInboundMessage(rawText) {
    if (!state.active || state.circuitTripped) return;

    const offer = extractPriceFromText(rawText);
    if (!offer) {
      console.log("[TradeFlow Autopilot] Message has no detectable price offer:", rawText);
      return;
    }

    console.log(`[TradeFlow Autopilot] Detected incoming offer: ¥${offer}. Consulting Bayesian Engine...`);
    updateHUD("ENGINE SOLVING...", "#38bdf8", `Processing quote ¥${offer} via MCTS`);

    // Human reading delay before responding
    const readDelay = calculateReadingDelay(rawText);
    await new Promise((r) => setTimeout(r, readDelay));

    // Submit offer to FastAPI backend via Service Worker
    chrome.runtime.sendMessage(
      {
        type: "SUBMIT_OFFER_TO_ENGINE",
        payload: {
          sessionId: state.sessionId,
          offer: offer,
          context: rawText,
        },
      },
      async (response) => {
        if (!response || !response.success) {
          console.error("[TradeFlow Autopilot] Engine communication failed:", response?.error);
          updateHUD("ENGINE ERROR", "#ef4444", response?.error || "Offline fallback");
          return;
        }

        const data = response.data;
        const turn = data.turn;

        // Check Circuit Breaker
        if (data.session?.state === "CIRCUIT_TRIPPED_ESCALATED" || turn?.breakdown_risk >= 0.15) {
          state.circuitTripped = true;
          updateHUD(
            "CIRCUIT TRIPPED (ESCALATED)",
            "#ef4444",
            `Risk ${((turn?.breakdown_risk || 0.15) * 100).toFixed(1)}% ≥ 15%. Telemetry sent to WhatsApp/WeChat!`
          );
          alert(
            `[TradeFlow Safety Circuit Breaker]\nHigh breakdown risk detected (${((turn?.breakdown_risk || 0.15) * 100).toFixed(1)}%).\nNegotiation paused. Check your mobile WhatsApp/WeChat to reply 1 (Concede), 2 (Hold), or 3 (Walk away).`
          );
          return;
        }

        // Deal Closed
        if (data.session?.state === "COMPLETED") {
          updateHUD("DEAL CLOSED [SUCCESS]", "#10b981", `Settled at ¥${turn?.buyer_counter_offer || offer}!`);
          return;
        }

        // Generate response text
        const counterOffer = turn?.buyer_counter_offer || (offer * 0.95).toFixed(2);
        const replyMessage = `感謝報價。考量本次訂單批量與交期，我們期望目標價格為 ¥${counterOffer}，若可配合將可立即簽核首批 PO。`;

        // Type response into chat
        const inputEl = findChatInput();
        if (inputEl) {
          updateHUD("AUTOPILOT TYPING...", "#a855f7", `Counter-offering ¥${counterOffer} with human cadence`);
          await humanType(inputEl, replyMessage);

          // Click send after small hesitation
          await new Promise((r) => setTimeout(r, 450 + Math.random() * 300));
          const sendBtn = findSendButton();
          if (sendBtn) {
            sendBtn.click();
          } else {
            inputEl.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
          }

          updateHUD("AUTOPILOT CRUISING", "#10b981", `Replied ¥${counterOffer}. Awaiting next turn.`);
        } else {
          console.warn("[TradeFlow Autopilot] Chat input element not found in DOM.");
        }
      }
    );
  }

  // 6. Streaming DOM MutationObserver with Debounce & Risk Sentinel
  function initObserver() {
    const observer = new MutationObserver((mutations) => {
      // 6.1 風控滑塊哨兵：一旦阿里彈出風控驗證碼，瞬間自鎖凍結並切換為安全副駕駛模式
      if (detectCaptchaChallenge()) {
        if (!state.captchaPaused) {
          state.captchaPaused = true;
          state.circuitTripped = true;
          updateHUD(
            "CAPTCHA DETECTED [PAUSED]",
            "#f59e0b",
            "Platform anti-bot challenge active! Autopilot paused. Please slide manually."
          );
          chrome.runtime.sendMessage({
            type: "TELEMETRY_ALERT",
            payload: {
              sessionId: state.sessionId,
              message: "1688 Captcha slide challenge detected on screen! Autopilot safely halted. Human slide required.",
            }
          });
        }
        return;
      } else if (state.captchaPaused) {
        // 使用者手動滑過後，自動解除凍結恢復自駕
        state.captchaPaused = false;
        state.circuitTripped = false;
        updateHUD("AUTOPILOT CRUISING", "#10b981", "Captcha solved! Autonomous navigation resumed.");
      }

      clearTimeout(state.debounceTimer);
      state.debounceTimer = setTimeout(() => {
        // Search through all potential chat bubble selectors
        const allSelectors = Object.values(PLATFORMS)
          .map((p) => p.messageBubbles)
          .join(", ");
        const bubbles = document.querySelectorAll(allSelectors);

        if (bubbles.length > 0) {
          const lastBubble = bubbles[bubbles.length - 1];
          const text = lastBubble.innerText || lastBubble.textContent || "";
          const hash = text.trim();

          if (hash && !state.processedMessages.has(hash)) {
            state.processedMessages.add(hash);
            handleInboundMessage(text);
          }
        }
      }, 400); // 400ms debounce
    });

    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    console.log("[TradeFlow Autopilot] MutationObserver active with 400ms debounce & Captcha Sentinel.");
  }

  // Initialize
  window.addEventListener("load", () => {
    injectHUD();
    initObserver();
  });

  if (document.readyState === "complete" || document.readyState === "interactive") {
    injectHUD();
    initObserver();
  }
})();
