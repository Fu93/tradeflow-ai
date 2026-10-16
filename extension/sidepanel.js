/**
 * TradeFlow Copilot — Modern Event-Driven SidePanel Controller
 * 
 * Features:
 * 1. ActiveTab Focus Tracking (chrome.tabs.onActivated & onUpdated).
 * 2. Event-Driven Push via Port (tf-sidepanel-${windowId}), eliminating 2.5s polling.
 * 3. Dynamic Multi-Tab Session Isolation with Dormant Standby View on non-ecommerce tabs.
 * 4. Reactive Bayesian Belief & Should-Cost HUD synchronization.
 */

const API_BASE_URL = "http://localhost:8000";

// ---------------------------------------------------------------------------
// Security helpers (H3): outbound auth + HTML escaping.
// ---------------------------------------------------------------------------
// H9: the backend fail-closes in production without a valid X-API-Key. Empty by
// default so a sandbox demo with no configured key keeps working (no header is
// sent at all when this is empty — an empty header is worse than none once a
// real key is configured).
const TF_API_KEY = "";

function tfAuthHeaders() {
  return TF_API_KEY ? { "X-API-Key": TF_API_KEY } : {};
}

// H3 (F5): round_number and every other backend-sourced value that is
// interpolated into an innerHTML template must be escaped first. Layout and CSS
// classes are unchanged.
function tfEsc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Numeric coercion for values that reach .toFixed()/.toLocaleString(): the
// backend may send a numeric string, which would throw instead of rendering.
function tfNum(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

// DOM Elements
const statusBadge = document.getElementById("status-badge");
const statusText = document.getElementById("status-text");
const sessionIdDisplay = document.getElementById("session-id-display");
const valTargetPrice = document.getElementById("val-target-price");
const valReserveCeiling = document.getElementById("val-reserve-ceiling");
const valPosteriorMean = document.getElementById("val-posterior-mean");
const valPosteriorStd = document.getElementById("val-posterior-std");
const valBreakdownRisk = document.getElementById("val-breakdown-risk");
const progressBreakdown = document.getElementById("progress-breakdown");
const roundCounter = document.getElementById("round-counter");
const turnsTimeline = document.getElementById("turns-timeline");
const valLastSignal = document.getElementById("val-last-signal");
const btnRefresh = document.getElementById("btn-refresh");

// State
let currentTabId = null;
let currentSessionId = null;
let sidepanelPort = null;

// Establish persistent Port to Background Worker
async function initPortConnection() {
  try {
    const currentWindow = await chrome.windows.getCurrent();
    const windowId = currentWindow?.id || 0;

    sidepanelPort = chrome.runtime.connect({ name: `tf-sidepanel-${windowId}` });
    console.log(`[TradeFlow Sidepanel] Port connected: window ${windowId}`);

    sidepanelPort.onMessage.addListener((msg) => {
      if (msg.type === "SESSION_STATE_PUSH") {
        if (!msg.tabId || msg.tabId === currentTabId) {
          handleSessionPush(msg.session);
        }
      }

      if (msg.type === "COMMAND_RESULT") {
        // H4: background.js now reports the REAL outcome (success is false for a
        // non-2xx response and for an HTTP 200 whose body says the command was
        // not applied). Never print "(OK)" for something that did not apply.
        const statusSuffix = msg.httpStatus ? ` [HTTP ${msg.httpStatus}]` : "";
        if (msg.success) {
          valLastSignal.innerText = `Command dispatched (${msg.result?.action || "OK"})${statusSuffix}`;
          valLastSignal.style.color = "var(--accent-emerald)";
        } else {
          valLastSignal.innerText = `Command NOT applied${statusSuffix}: ${msg.error || "unknown error"}`;
          valLastSignal.style.color = "var(--accent-rose)";
        }
      }
    });

    sidepanelPort.onDisconnect.addListener(() => {
      console.warn("[TradeFlow Sidepanel] Port disconnected. Reconnecting in 2s...");
      setTimeout(initPortConnection, 2000);
    });

    // Initial sync
    syncWithActiveTab();
  } catch (err) {
    console.error("[TradeFlow Sidepanel] Port init error:", err);
  }
}

// Sync with user's currently focused Active Tab
async function syncWithActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) return;

    currentTabId = tab.id;

    if (sidepanelPort) {
      sidepanelPort.postMessage({ type: "SYNC_ACTIVE_TAB", tabId: currentTabId });
    }
  } catch (err) {
    console.warn("[TradeFlow Sidepanel] syncWithActiveTab error:", err);
  }
}

// Render session data or dormant standby view
function handleSessionPush(sessionMeta) {
  if (!sessionMeta || !sessionMeta.sessionId) {
    renderDormantView();
    return;
  }

  currentSessionId = sessionMeta.sessionId;
  sessionIdDisplay.innerText = sessionMeta.sessionId;

  // Check if session has live turn data from engine
  if (sessionMeta.latestData) {
    renderEngineData(sessionMeta.latestData);
  } else {
    // Fetch latest session status from backend if not yet cached
    fetchSessionFromBackend(currentSessionId);
  }
}

async function fetchSessionFromBackend(sessionId) {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      headers: tfAuthHeaders(),
    });
    if (!res.ok) {
      if (res.status === 404) {
        renderStandbyView(sessionId);
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const session = await res.json();
    renderSession(session);
  } catch (err) {
    console.warn("[TradeFlow Sidepanel] Backend fetch failed:", err.message);
  }
}

function renderDormantView() {
  currentSessionId = null;
  sessionIdDisplay.innerText = "No Active Tab Session";
  statusText.innerText = "STANDBY";
  statusBadge.className = "status-pill";
  statusBadge.style.background = "rgba(100, 116, 139, 0.25)";
  statusBadge.style.color = "#94a3b8";

  valTargetPrice.innerText = "--";
  valReserveCeiling.innerText = "--";
  valPosteriorMean.innerText = "--";
  valPosteriorStd.innerText = "--";
  valBreakdownRisk.innerText = "0.0%";
  progressBreakdown.style.width = "0%";
  roundCounter.innerText = "Round 0 / 0";
  turnsTimeline.innerHTML = `
    <div style="color: var(--text-muted); font-size: 11px; padding: 12px 0; text-align: center;">
      Switch to a 1688 / Alibaba / WhatsApp tab to view live negotiation radar.
    </div>
  `;
}

function renderStandbyView(sessionId) {
  sessionIdDisplay.innerText = sessionId;
  statusText.innerText = "RADAR LISTENING";
  statusBadge.className = "status-pill";
  statusBadge.style.background = "rgba(6, 182, 212, 0.2)";
  statusBadge.style.color = "#06b6d4";

  valTargetPrice.innerText = "¥12.00";
  valReserveCeiling.innerText = "¥16.20";
  valPosteriorMean.innerText = "1.000";
  valPosteriorStd.innerText = "0.150";
  valBreakdownRisk.innerText = "0.0%";
  progressBreakdown.style.width = "5%";
  roundCounter.innerText = "Round 0 / 5";
  turnsTimeline.innerHTML = `
    <div style="color: var(--text-muted); font-size: 11px; padding: 8px 0;">
      Awaiting supplier inbound quote in chat...
    </div>
  `;
}

function renderEngineData(data) {
  if (!data) return;

  const session = data.session || {};
  const turn = data.turn || {};
  const belief = data.bayesian_belief_state || {};

  statusText.innerText = (session.state || "COPILOT_MONITORING").replace(/_/g, " ");

  if (session.state === "CIRCUIT_TRIPPED_ESCALATED") {
    statusBadge.className = "status-pill tripped";
    statusBadge.style.background = "rgba(244, 63, 94, 0.25)";
    statusBadge.style.color = "#f43f5e";
  } else {
    statusBadge.className = "status-pill";
    statusBadge.style.background = "rgba(16, 185, 129, 0.25)";
    statusBadge.style.color = "#10b981";
  }

  valTargetPrice.innerText = `¥${tfNum(session.target_price, 12.0).toFixed(2)}`;
  valReserveCeiling.innerText = `¥${tfNum(session.walk_away_ceiling, 16.2).toFixed(2)}`;

  valPosteriorMean.innerText = tfNum(belief.posterior_mean, 1.05).toFixed(3);
  valPosteriorStd.innerText = tfNum(belief.posterior_std, 0.08).toFixed(3);

  const risk = tfNum(turn.breakdown_risk, 0.048);
  valBreakdownRisk.innerText = `${(risk * 100).toFixed(1)}%`;
  progressBreakdown.style.width = `${Math.min(100, Math.max(5, risk * 100))}%`;
  valBreakdownRisk.style.color = risk >= 0.15 ? "#f43f5e" : "#10b981";

  // H3 (F5): turn.round_number is interpolated raw into innerHTML below; it is
  // escaped with tfEsc() and the numeric fields are coerced with tfNum().
  const roundNo = tfNum(turn.round_number, 1);
  roundCounter.innerText = `Round ${roundNo} / 5`;
  turnsTimeline.innerHTML = `
    <div class="timeline-turn">
      <div style="display: flex; justify-content: space-between;">
        <strong>Round ${tfEsc(roundNo)}</strong>
        <span style="color: var(--accent-emerald);">¥${tfNum(data.recommended_offer_price, tfNum(turn.buyer_counter_offer, 12.5)).toFixed(2)} Counter</span>
      </div>
      <div style="color: var(--text-muted); margin-top: 2px;">
        Supplier: ¥${tfNum(turn.supplier_offer, 15.0).toFixed(2)} | Risk: ${(risk * 100).toFixed(1)}%
      </div>
    </div>
  `;
}

function renderSession(session) {
  if (!session) return;

  sessionIdDisplay.innerText = session.session_id;
  valTargetPrice.innerText = `¥${tfNum(session.target_price, 0).toFixed(2)}`;
  valReserveCeiling.innerText = `¥${tfNum(session.walk_away_ceiling, 0).toFixed(2)}`;

  valPosteriorMean.innerText = tfNum(session.current_posterior_mean, 1.08).toFixed(3);
  valPosteriorStd.innerText = tfNum(session.current_posterior_std, 0.095).toFixed(3);

  const stateStr = session.state || "COPILOT_MONITORING";
  statusText.innerText = stateStr.replace(/_/g, " ");

  if (stateStr === "CIRCUIT_TRIPPED_ESCALATED") {
    statusBadge.className = "status-pill tripped";
    statusBadge.style.background = "rgba(244, 63, 94, 0.25)";
    statusBadge.style.color = "#f43f5e";
  } else if (stateStr === "COMPLETED") {
    statusBadge.className = "status-pill";
    statusBadge.style.background = "rgba(16, 185, 129, 0.25)";
    statusBadge.style.color = "#10b981";
  } else {
    statusBadge.className = "status-pill";
    statusBadge.style.background = "rgba(6, 182, 212, 0.2)";
    statusBadge.style.color = "#06b6d4";
  }

  const turns = session.turns || [];
  roundCounter.innerText = `Round ${turns.length} / ${session.max_rounds || 5}`;

  if (turns.length > 0) {
    const lastTurn = turns[turns.length - 1];
    const risk = tfNum(lastTurn.breakdown_risk, 0.048);
    valBreakdownRisk.innerText = `${(risk * 100).toFixed(1)}%`;
    progressBreakdown.style.width = `${Math.min(100, Math.max(5, risk * 100))}%`;
    valBreakdownRisk.style.color = risk >= 0.15 ? "#f43f5e" : "#10b981";

    // H3 (F5) + H2-class coercion: every backend value rendered here is escaped
    // (round_number, which is interpolated raw) or numerically coerced before
    // .toFixed()/.toLocaleString(). The previous `t.buyer_counter_offer.toFixed(2)`
    // threw a TypeError for any turn whose counter-offer was absent.
    turnsTimeline.innerHTML = turns
      .map(
        (t) => `
      <div class="timeline-turn">
        <div style="display: flex; justify-content: space-between;">
          <strong>Round ${tfEsc(t.round_number)}</strong>
          <span style="color: var(--accent-emerald);">¥${tfNum(t.buyer_counter_offer, 0).toFixed(2)} Counter</span>
        </div>
        <div style="color: var(--text-muted); margin-top: 2px;">
          Supplier: ¥${tfNum(t.supplier_offer, 0).toFixed(2)} | Risk: ${(tfNum(t.breakdown_risk, 0) * 100).toFixed(1)}%
        </div>
      </div>
    `
      )
      .reverse()
      .join("");
  }
}

// Dispatch Remote Override Command via Port
function dispatchCommand(cmd) {
  if (!currentSessionId) {
    valLastSignal.innerText = "No active session to dispatch command.";
    valLastSignal.style.color = "var(--accent-amber)";
    return;
  }

  if (sidepanelPort) {
    sidepanelPort.postMessage({
      type: "DISPATCH_OVERRIDE_COMMAND",
      payload: { sessionId: currentSessionId, command: cmd },
    });
    valLastSignal.innerText = `Dispatching command '${cmd}'...`;
    valLastSignal.style.color = "var(--accent-cyan)";
  }
}

// Wire Event Listeners
btnRefresh?.addEventListener("click", () => {
  if (currentSessionId) {
    fetchSessionFromBackend(currentSessionId);
  } else {
    syncWithActiveTab();
  }
});

document.getElementById("btn-concede")?.addEventListener("click", () => dispatchCommand("1"));
document.getElementById("btn-hold")?.addEventListener("click", () => dispatchCommand("2"));
document.getElementById("btn-abort")?.addEventListener("click", () => dispatchCommand("3"));

// Active Tab listeners (auto-switch view when user switches tabs)
chrome.tabs.onActivated.addListener(() => {
  syncWithActiveTab();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tab.active && changeInfo.status === "complete") {
    syncWithActiveTab();
  }
});

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", initPortConnection);
