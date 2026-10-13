/**
 * TradeFlow Autopilot — Background Service Worker (Manifest V3)
 * Manages keep-alive heartbeat, side panel lifecycle, and API proxy routing.
 */

const API_BASE_URL = "http://localhost:8000";

// Ensure side panel opens when extension action icon is clicked
chrome.runtime.onInstalled.addListener(() => {
  console.log("[TradeFlow SW] Installed. Initializing side panel behavior...");
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  }
  // Setup keep-alive alarm to prevent service worker hibernation during live sessions
  chrome.alarms.create("tradeflow_keepalive", { periodInMinutes: 0.33 }); // ~20 seconds
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "tradeflow_keepalive") {
    // Ping memory to keep service worker execution context warm
    // console.debug("[TradeFlow SW] Keep-alive tick:", Date.now());
  }
});

// Message Router
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { type, payload } = message;

  if (type === "SUBMIT_OFFER_TO_ENGINE") {
    const { sessionId, offer, currency, context } = payload;
    fetch(`${API_BASE_URL}/api/v1/sessions/${sessionId}/round`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        supplier_offer: offer,
        supplier_text: context || `Inbound offer: ${offer}`,
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        return res.json();
      })
      .then((data) => {
        sendResponse({ success: true, data });
      })
      .catch((err) => {
        console.error("[TradeFlow SW] Engine API error:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true; // Keep message channel open for async response
  }

  if (type === "CHECK_SESSION_STATUS") {
    const { sessionId } = payload;
    fetch(`${API_BASE_URL}/api/v1/sessions/${sessionId}`)
      .then((res) => res.json())
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (type === "TELEMETRY_ALERT") {
    const { sessionId, message } = payload;
    fetch(`${API_BASE_URL}/api/v1/telemetry/test-push`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel: "all",
        message: `[SECURITY SENTINEL ALERT] Session: ${sessionId} - ${message}`,
      }),
    })
      .then((res) => res.json())
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (type === "BROADCAST_STATUS_UPDATE") {
    // Relay update from background to sidepanel or content
    chrome.runtime.sendMessage(message).catch(() => {});
    return false;
  }
});
