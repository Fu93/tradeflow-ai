/**
 * TradeFlow Copilot — Background Service Worker (Manifest V3)
 * Port-based bidirectional communication router, multi-tab session registry,
 * and FastAPI backend proxy.
 */

const API_BASE_URL = "http://localhost:8000";

// Active Port mappings
const activePorts = {
  sidepanels: new Map(), // windowId -> port
  contentTabs: new Map(), // tabId -> port
};

// In-memory tab session registry: tabId -> SessionMetadata
const tabSessions = new Map();

// Initialize side panel behavior & restore sessions on startup
chrome.runtime.onInstalled.addListener(async () => {
  console.log("[TradeFlow SW] Installed. Initializing MV3 Port Router & SidePanel behavior...");
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  }

  // Restore existing session mappings from chrome.storage.session if available
  if (chrome.storage && chrome.storage.session) {
    try {
      const stored = await chrome.storage.session.get(null);
      for (const [key, val] of Object.entries(stored)) {
        if (key.startsWith("tab_")) {
          const tabId = parseInt(key.replace("tab_", ""), 10);
          tabSessions.set(tabId, val);
        }
      }
      console.log(`[TradeFlow SW] Restored ${tabSessions.size} active sessions from session storage.`);
    } catch (err) {
      console.warn("[TradeFlow SW] Storage restore warning:", err);
    }
  }
});

// Clean up closed tabs from registry
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabSessions.has(tabId)) {
    tabSessions.delete(tabId);
    if (chrome.storage && chrome.storage.session) {
      chrome.storage.session.remove(`tab_${tabId}`).catch(() => {});
    }
    console.log(`[TradeFlow SW] Cleaned up session registry for closed tab: ${tabId}`);
  }
  activePorts.contentTabs.delete(tabId);
});

// Broadcast session update to Content Script and any listening SidePanel
function broadcastSessionUpdate(tabId, sessionData) {
  // Push to Content Script
  const contentPort = activePorts.contentTabs.get(tabId);
  if (contentPort) {
    try {
      contentPort.postMessage({ type: "SESSION_UPDATED", data: sessionData });
    } catch (e) {
      console.warn("[TradeFlow SW] Content port postMessage failed:", e);
    }
  }

  // Push to SidePanel(s)
  activePorts.sidepanels.forEach((port) => {
    try {
      port.postMessage({ type: "SESSION_STATE_PUSH", tabId, session: sessionData });
    } catch (e) {
      console.warn("[TradeFlow SW] Sidepanel port postMessage failed:", e);
    }
  });
}

// Forward offer submission to FastAPI backend
async function handleOfferSubmission(tabId, payload) {
  const { sessionId, offer, context } = payload;
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/sessions/${sessionId}/round`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        supplier_offer: offer,
        supplier_text: context || `Inbound quote: ${offer}`,
      }),
    });

    if (!res.ok) {
      throw new Error(`Engine HTTP ${res.status}: ${res.statusText}`);
    }

    const data = await res.json();
    // Cache latest turn data into tabSession
    if (tabSessions.has(tabId)) {
      const meta = tabSessions.get(tabId);
      meta.latestData = data;
      meta.lastActiveTimestamp = Date.now();
      tabSessions.set(tabId, meta);
      if (chrome.storage && chrome.storage.session) {
        chrome.storage.session.set({ [`tab_${tabId}`]: meta }).catch(() => {});
      }
    }

    broadcastSessionUpdate(tabId, data);
  } catch (err) {
    console.error("[TradeFlow SW] handleOfferSubmission error:", err);
    const contentPort = activePorts.contentTabs.get(tabId);
    if (contentPort) {
      try {
        contentPort.postMessage({ type: "ENGINE_ERROR", error: err.message });
      } catch (e) {}
    }
  }
}

// Bidirectional Port Connection Router
chrome.runtime.onConnect.addListener((port) => {
  // 1. SidePanel Port Connection: tf-sidepanel-${windowId}
  if (port.name.startsWith("tf-sidepanel-")) {
    const windowId = parseInt(port.name.replace("tf-sidepanel-", ""), 10);
    activePorts.sidepanels.set(windowId, port);
    console.log(`[TradeFlow SW] SidePanel port connected: window ${windowId}`);

    port.onDisconnect.addListener(() => {
      activePorts.sidepanels.delete(windowId);
      console.log(`[TradeFlow SW] SidePanel port disconnected: window ${windowId}`);
    });

    port.onMessage.addListener(async (msg) => {
      if (msg.type === "SYNC_ACTIVE_TAB") {
        const session = tabSessions.get(msg.tabId);
        port.postMessage({
          type: "SESSION_STATE_PUSH",
          tabId: msg.tabId,
          session: session || null,
        });
      }

      if (msg.type === "DISPATCH_OVERRIDE_COMMAND") {
        const { sessionId, command } = msg.payload;
        try {
          const res = await fetch(`${API_BASE_URL}/webhook/mobile-command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              channel: "sidepanel",
              sender_id: "human_supervisor",
              raw_text: command,
              session_id: sessionId,
            }),
          });
          const result = await res.json();
          port.postMessage({ type: "COMMAND_RESULT", success: true, result });
        } catch (err) {
          port.postMessage({ type: "COMMAND_RESULT", success: false, error: err.message });
        }
      }
    });
  }

  // 2. Content Script Port Connection: tf-content
  if (port.name.startsWith("tf-content")) {
    const tabId = port.sender?.tab?.id || (port.name.includes("-") ? parseInt(port.name.split("-")[1], 10) : 0);
    activePorts.contentTabs.set(tabId, port);
    console.log(`[TradeFlow SW] Content port connected: tab ${tabId}`);

    port.onDisconnect.addListener(() => {
      activePorts.contentTabs.delete(tabId);
      console.log(`[TradeFlow SW] Content port disconnected: tab ${tabId}`);
    });

    port.onMessage.addListener(async (msg) => {
      if (msg.type === "REGISTER_SESSION") {
        const payload = msg.payload;
        const meta = {
          tabId,
          sessionId: payload.sessionId,
          supplierId: payload.supplierId || "unknown",
          supplierName: payload.supplierName || "1688 Supplier",
          platform: payload.platform || "generic",
          url: payload.url || "",
          lastActiveTimestamp: Date.now(),
        };
        tabSessions.set(tabId, meta);
        if (chrome.storage && chrome.storage.session) {
          chrome.storage.session.set({ [`tab_${tabId}`]: meta }).catch(() => {});
        }
        console.log(`[TradeFlow SW] Tab ${tabId} bound to session: ${payload.sessionId}`);
        broadcastSessionUpdate(tabId, { sessionMeta: meta });
      }

      if (msg.type === "SUBMIT_OFFER") {
        await handleOfferSubmission(tabId, msg.payload);
      }

      if (msg.type === "TELEMETRY_ALERT") {
        const { sessionId, message } = msg.payload;
        try {
          await fetch(`${API_BASE_URL}/api/v1/telemetry/test-push`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              channel: "all",
              message: `[SECURITY SENTINEL ALERT] Session: ${sessionId} - ${message}`,
            }),
          });
        } catch (err) {
          console.warn("[TradeFlow SW] Telemetry alert push failed:", err);
        }
      }
    });
  }
});

// Fallback message listener for one-off queries
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { type, tabId } = message;

  if (type === "GET_TAB_SESSION") {
    const targetTabId = tabId || sender?.tab?.id;
    const session = tabSessions.get(targetTabId);
    sendResponse({ success: true, session: session || null });
    return true;
  }

  if (type === "GET_ALL_SESSIONS") {
    sendResponse({ success: true, sessions: Array.from(tabSessions.values()) });
    return true;
  }
});
