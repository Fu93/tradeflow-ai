/**
 * TradeFlow Copilot — Background Service Worker (Manifest V3)
 * Port-based bidirectional communication router, multi-tab session registry,
 * and FastAPI backend proxy.
 */

const API_BASE_URL = "http://localhost:8000";

// ---------------------------------------------------------------------------
// Security: outbound auth (H9)
// ---------------------------------------------------------------------------
// The backend gates /api/v1/* behind X-API-Key and the /webhook/* routes behind
// X-Webhook-Token. Both checks are fail-open in sandbox when the server has no
// secret configured, so leaving these empty keeps the demo working (no header is
// sent at all when empty). An operator who configures TRADEFLOW_API_KEY /
// TRADEFLOW_WEBHOOK_TOKEN must fill in the matching value here.
const TF_API_KEY = "";
const TF_WEBHOOK_TOKEN = "";

function apiKeyHeader() {
  return TF_API_KEY ? { "X-API-Key": TF_API_KEY } : {};
}

function webhookHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (TF_WEBHOOK_TOKEN) headers["X-Webhook-Token"] = TF_WEBHOOK_TOKEN;
  return headers;
}

function jsonHeaders() {
  return { "Content-Type": "application/json", ...apiKeyHeader() };
}

// Active Port mappings
const activePorts = {
  sidepanels: new Map(), // windowId -> port
  contentTabs: new Map(), // tabId -> port
};

// In-memory tab session registry: tabId -> SessionMetadata
const tabSessions = new Map();

// H7 (F1): the registry is memory-only, and MV3 terminates the service worker on
// idle. Restoring it ONLY from chrome.runtime.onInstalled means that after any
// normal worker termination tabSessions is empty: GET_TAB_SESSION returns null,
// SYNC_ACTIVE_TAB pushes nothing, and `tabSessions.has(tabId)` silently skips
// caching. Restore it on every worker start.
async function restoreTabSessions() {
  if (!(chrome.storage && chrome.storage.session)) return;
  try {
    // chrome.storage.session defaults to TRUSTED_CONTEXTS, which is fine here,
    // but an explicit setAccessLevel keeps content-script access intentional if
    // a future version needs it. Guarded: older Chrome builds lack the API.
    if (chrome.storage.session.setAccessLevel) {
      await chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
    }
    const stored = await chrome.storage.session.get(null);
    for (const [key, val] of Object.entries(stored)) {
      if (typeof key === "string" && key.startsWith("tab_")) {
        // H8 (F2): keys are only ever written as `tab_<finite integer>`. Reject
        // anything else (e.g. a legacy `tab_NaN` written by the old port-name
        // parsing bug) instead of reviving it into the registry.
        const raw = key.slice("tab_".length);
        const tabId = Number(raw);
        if (!Number.isInteger(tabId)) {
          console.warn(`[TradeFlow SW] Dropping malformed session-storage key: ${key}`);
          continue;
        }
        tabSessions.set(tabId, val);
      }
    }
    console.log(`[TradeFlow SW] Restored ${tabSessions.size} active sessions from session storage.`);
  } catch (err) {
    console.warn("[TradeFlow SW] Storage restore warning:", err);
  }
}

// Install/update path.
chrome.runtime.onInstalled.addListener(async () => {
  console.log("[TradeFlow SW] Installed. Initializing MV3 Port Router & SidePanel behavior...");
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  }
  await restoreTabSessions();
});

// H7 (F1): normal browser startup — the missing path that left the registry
// empty after MV3 worker termination.
if (chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    console.log("[TradeFlow SW] Browser startup: restoring tab session registry...");
    restoreTabSessions();
  });
}

// H7: also restore when the worker itself is revived without an onStartup event
// (e.g. an event woke the service worker after mobile/desktop sleep).
restoreTabSessions();

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
  const { sessionId, offer, context, mode } = payload;
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/sessions/${encodeURIComponent(sessionId)}/round`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        supplier_offer: offer,
        supplier_text: context || `Inbound quote: ${offer}`,
        // V1 (F6): pass the operator's selected mode through. Previously this was
        // never sent, so RoundActionRequest.mode defaulted to "autopilot" and
        // every quote skipped the copilot human-approval branch even while the
        // HUD displayed Copilot Mode.
        mode: mode === "copilot" ? "copilot" : "autopilot",
      }),
    });

    if (!res.ok) {
      // Read the error body so the panel can show the real reason (401 for a
      // missing key, 409 CAS conflict, 400 validation, ...) instead of a
      // generic network error.
      let detail = "";
      try {
        const errBody = await res.json();
        detail = errBody && (errBody.detail || errBody.message) ? `: ${errBody.detail || errBody.message}` : "";
      } catch (_) {
        detail = "";
      }
      throw new Error(`Engine HTTP ${res.status}${detail}`);
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
          // H4 (F7) FALSE SUCCESS, High. This previously posted `raw_text`, but
          // the server (/webhook/mobile-command, main.py:309) reads
          // `body.get("command") or body.get("command_text")`. `raw_text` was
          // ignored, user_command was "", and the endpoint returned HTTP 400 —
          // yet this code never checked res.ok and hardcoded success: true, so
          // sidepanel.js printed "Command dispatched (OK)". The Concede/Hold/
          // Emergency-Abort safety controls were silent no-ops reported as
          // success. Send the field the server actually reads, and check the
          // response.
          const res = await fetch(`${API_BASE_URL}/webhook/mobile-command`, {
            method: "POST",
            headers: webhookHeaders(),
            body: JSON.stringify({
              channel: "sidepanel",
              sender_id: "human_supervisor",
              command: command,
              session_id: sessionId,
            }),
          });

          let result = null;
          try {
            result = await res.json();
          } catch (_) {
            result = null;
          }

          if (!res.ok) {
            // Propagate the real HTTP status instead of inventing success.
            const detail = (result && (result.detail || result.message)) || res.statusText || "request rejected";
            port.postMessage({
              type: "COMMAND_RESULT",
              success: false,
              httpStatus: res.status,
              error: `HTTP ${res.status}: ${detail}`,
            });
            return;
          }

          // HTTP 200 can still mean the command was not applied (the backend
          // returns status "ignored"/"error" for an unknown session that has no
          // pending authorization bridge). Only a true "success" is a success.
          const applied = !!(result && result.status === "success");
          port.postMessage({
            type: "COMMAND_RESULT",
            success: applied,
            httpStatus: res.status,
            result,
            error: applied
              ? undefined
              : (result && (result.detail || result.reason)) || "command was not applied",
          });
        } catch (err) {
          port.postMessage({
            type: "COMMAND_RESULT",
            success: false,
            error: err.message,
          });
        }
      }
    });
  }

  // 2. Content Script Port Connection: tf-content
  // H8 (F2/F18): the port is always named exactly "tf-content" (content.js:513
  // `chrome.runtime.connect({ name: "tf-content" })`), so
  // `parseInt(port.name.split("-")[1], 10)` parsed "content" -> NaN and merged
  // every tab under the key NaN (persisted as `tab_NaN`). The tab id must come
  // from port.sender.tab.id; an unresolvable id is a hard error, not a shared
  // NaN bucket.
  if (port.name.startsWith("tf-content")) {
    const tabId = port.sender?.tab?.id;
    if (!Number.isInteger(tabId)) {
      console.error(
        `[TradeFlow SW] Rejecting content port "${port.name}": sender tab id is unavailable (${tabId}). ` +
        "Refusing to register the port under a shared NaN key."
      );
      try { port.disconnect(); } catch (_) {}
      return;
    }
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
          mode: payload.mode === "copilot" ? "copilot" : "autopilot",
          lastActiveTimestamp: Date.now(),
        };
        meta.marketContext = { status: "unavailable", reason: "not_loaded" };
        if (payload.sessionId) {
          try {
            const sessionRes = await fetch(
              `${API_BASE_URL}/api/v1/sessions/${encodeURIComponent(payload.sessionId)}`,
              { headers: jsonHeaders() }
            );
            if (sessionRes.ok) {
              const body = await sessionRes.json();
              const spec = body.spec || (body.session && body.session.spec) || {};
              meta.marketContext = spec.market_context || { status: "unavailable", reason: "missing" };
              meta.hsCode = spec.hs_code || null;
            } else {
              meta.marketContext = { status: "unavailable", reason: "session_unread" };
            }
          } catch (err) {
            meta.marketContext = { status: "unavailable", reason: "offline" };
          }
        }
        tabSessions.set(tabId, meta);
        // H8: only ever persist under an integer tab key (guarded by the tabId
        // check on connect, retained here as belt-and-braces).
        if (Number.isInteger(tabId) && chrome.storage && chrome.storage.session) {
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
          // H9: /api/v1/telemetry/test-push is gated by X-API-Key. Check the
          // response so a 401 is not mistaken for a delivered alert.
          const res = await fetch(`${API_BASE_URL}/api/v1/telemetry/test-push`, {
            method: "POST",
            headers: jsonHeaders(),
            body: JSON.stringify({
              channel: "all",
              message: `[SECURITY SENTINEL ALERT] Session: ${sessionId} - ${message}`,
            }),
          });
          if (!res.ok) {
            console.warn(`[TradeFlow SW] Telemetry alert push rejected: HTTP ${res.status}`);
          }
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

  if (type === "IMPORT_DESK_QUOTE") {
    importLatestVisible().then(sendResponse);
    return true;
  }
});

function foldDeskName(value) {
  return String(value || "").normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
}

function matchSuppliers(suppliers, page) {
  const hay = foldDeskName([page && page.shop, page && page.title, page && page.url].filter(Boolean).join("\n"));
  return (suppliers || []).filter((supplier) => {
    const name = foldDeskName(supplier.name);
    const address = foldDeskName(supplier.address);
    if (name && hay.indexOf(name) >= 0) return true;
    if (address && hay.indexOf(address) >= 0) return true;
    return false;
  });
}

async function importLatestVisible() {
  const stored = await new Promise((resolve) => {
    chrome.storage.local.get(
      ["tfDeskDeal", "tfDeskSupplier", "tfDeskSupplierConfirmed"],
      (value) => resolve(value || {})
    );
  });
  if (!stored.tfDeskDeal) return { ok: false, reason: "no-deal" };
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (!tab || !tab.id) return { ok: false, reason: "no-tab" };
  const resp = await new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { type: "IMPORT_VISIBLE_QUOTE" }, (reply) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, reason: "no-page" });
        return;
      }
      resolve(reply || { ok: false, reason: "empty" });
    });
  });
  if (!resp || !resp.ok || !resp.text) return resp || { ok: false, reason: "empty" };
  const dealRes = await fetch(`${API_BASE_URL}/api/v1/desk/deals/${stored.tfDeskDeal}`, {
    headers: jsonHeaders(),
  });
  if (!dealRes.ok) return { ok: false, reason: "no-deal", status: dealRes.status };
  const snapshot = await dealRes.json();
  const matches = matchSuppliers(snapshot.suppliers || [], resp);
  let supplierId = null;
  if (matches.length === 1) supplierId = matches[0].id;
  else if (stored.tfDeskSupplierConfirmed && stored.tfDeskSupplier) supplierId = stored.tfDeskSupplier;
  else {
    return {
      ok: false,
      reason: matches.length > 1 ? "ambiguous" : "unmapped",
      names: (matches.length ? matches : snapshot.suppliers || []).map((supplier) => supplier.name),
    };
  }
  const quote = {
    source: "chat-import",
    raw_text: resp.text,
    supplier_id: supplierId,
    simulated: !!resp.simulated,
  };
  const res = await fetch(`${API_BASE_URL}/api/v1/desk/deals/${stored.tfDeskDeal}/quotes`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(quote),
  });
  return {
    ok: res.ok,
    status: res.status,
    simulated: !!resp.simulated,
    supplierId,
  };
}

if (chrome.commands && chrome.commands.onCommand) {
  chrome.commands.onCommand.addListener((command) => {
    if (command === "import-latest") importLatestVisible().catch(() => {});
  });
}
