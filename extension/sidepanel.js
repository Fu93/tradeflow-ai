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
const TF_API_KEY = localStorage.getItem("tfApiKey") || "";

function tfAuthHeaders() {
  const headers = {};
  if (TF_API_KEY) headers["X-API-Key"] = TF_API_KEY;
  return headers;
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
        } else if (
          msg.result?.status === "ignored" ||
          /ignored/i.test(String(msg.error || ""))
        ) {
          // P1-4: AWAITING_HUMAN_DECISION 下指令 1/2 後端回 200 + status=ignored，
          // 這是正常語義（指令在熔斷掛起時才生效），不是錯誤，不該紅字。
          valLastSignal.innerText = `Command received — applies once the circuit breaker arms the session${statusSuffix}`;
          valLastSignal.style.color = "#f59e0b";
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
  valBreakdownRisk.innerText = "--";
  progressBreakdown.style.width = "0%";
  roundCounter.innerText = "Round — / —";
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

  // P1-2: 待命視圖不再顯示寫死的示例數字，全部以 "--" 呈現。
  valTargetPrice.innerText = "--";
  valReserveCeiling.innerText = "--";
  valPosteriorMean.innerText = "--";
  valPosteriorStd.innerText = "--";
  valBreakdownRisk.innerText = "--";
  progressBreakdown.style.width = "0%";
  roundCounter.innerText = "Round — / —";
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

  // P1-1/P1-2: NegotiationSession 的值在 spec 內層；turn 風險欄位是 p_break。
  // 缺值顯示 "--"，絕不捏造數字。
  const spec = session.spec || {};
  const tPrice = tfNum(spec.target_price ?? session.target_price, null);
  const cPrice = tfNum(spec.ceiling_price ?? session.walk_away_ceiling, null);
  valTargetPrice.innerText = tPrice === null ? "--" : `¥${tPrice.toFixed(2)}`;
  valReserveCeiling.innerText = cPrice === null ? "--" : `¥${cPrice.toFixed(2)}`;

  const postMean = tfNum(belief.posterior_mean, null);
  valPosteriorMean.innerText = postMean === null ? "--" : postMean.toFixed(3);
  const postStd = tfNum(belief.posterior_std, null);
  valPosteriorStd.innerText = postStd === null ? "--" : postStd.toFixed(3);

  const risk = tfNum(turn?.p_break ?? turn?.breakdown_risk, null);
  if (risk === null) {
    valBreakdownRisk.innerText = "--";
    progressBreakdown.style.width = "0%";
    valBreakdownRisk.style.color = "";
  } else {
    valBreakdownRisk.innerText = `${(risk * 100).toFixed(1)}%`;
    progressBreakdown.style.width = `${Math.min(100, Math.max(5, risk * 100))}%`;
    valBreakdownRisk.style.color = risk >= 0.15 ? "#f43f5e" : "#10b981";
  }

  // H3 (F5): turn.round_number is interpolated raw into innerHTML below; it is
  // escaped with tfEsc() and the numeric fields are coerced with tfNum().
  // P0-2/P1-2 契約修正：autopilot turn 欄位為 round_index/parsed_price；風險在
  // action_object.breakdown_risk 或 turn.p_break；缺值一律 "--"，不捏造。
  const action = data.action_object || {};
  const maxRounds = tfNum(spec.max_rounds, null);
  const roundNo = tfNum(turn?.round_index ?? session.current_round, null);
  roundCounter.innerText = `Round ${roundNo === null ? "—" : roundNo} / ${maxRounds === null ? "—" : maxRounds}`;
  const counterOffer = tfNum(
    data.recommended_offer_price ?? action.authorized_price ?? turn?.buyer_counter_offer,
    null,
  );
  const supplierPrice = tfNum(
    data.effective_opp_price ?? action.counterpart_offered_price ?? turn?.parsed_price,
    null,
  );
  turnsTimeline.innerHTML = `
    <div class="timeline-turn">
      <div style="display: flex; justify-content: space-between;">
        <strong>Round ${roundNo === null ? "—" : tfEsc(roundNo)}</strong>
        <span style="color: var(--accent-emerald);">${counterOffer === null ? "Counter: --" : `¥${counterOffer.toFixed(2)} Counter`}</span>
      </div>
      <div style="color: var(--text-muted); margin-top: 2px;">
        Supplier: ${supplierPrice === null ? "--" : `¥${supplierPrice.toFixed(2)}`} | Risk: ${risk === null ? "--" : `${(risk * 100).toFixed(1)}%`}
      </div>
    </div>
  `;
}

function renderSession(session) {
  if (!session) return;

  // P1-1: NegotiationSession 形狀 — 值在 spec 內層、輪次在 history 陣列。
  const spec2 = session.spec || {};
  const t0 = Array.isArray(session.history) && session.history.length
    ? session.history[session.history.length - 1]
    : null;
  sessionIdDisplay.innerText = spec2.session_id ?? session.session_id ?? "";
  const tPrice0 = tfNum(spec2.target_price, null);
  const cPrice0 = tfNum(spec2.ceiling_price, null);
  valTargetPrice.innerText = tPrice0 === null ? "--" : `¥${tPrice0.toFixed(2)}`;
  valReserveCeiling.innerText = cPrice0 === null ? "--" : `¥${cPrice0.toFixed(2)}`;

  const pm0 = tfNum(t0?.posterior_mean, null);
  const ps0 = tfNum(t0?.posterior_std, null);
  valPosteriorMean.innerText = pm0 === null ? "--" : pm0.toFixed(3);
  valPosteriorStd.innerText = ps0 === null ? "--" : ps0.toFixed(3);

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

  const turns = Array.isArray(session.history) ? session.history : session.turns || [];
  const maxRounds0 = tfNum(spec2.max_rounds, null);
  roundCounter.innerText = `Round ${turns.length} / ${maxRounds0 === null ? "—" : maxRounds0}`;

  if (turns.length > 0) {
    const lastTurn = turns[turns.length - 1];
    const risk = tfNum(lastTurn.p_break ?? lastTurn.breakdown_risk, 0);
    valBreakdownRisk.innerText = `${(risk * 100).toFixed(1)}%`;
    progressBreakdown.style.width = `${Math.min(100, Math.max(5, risk * 100))}%`;
    valBreakdownRisk.style.color = risk >= 0.15 ? "#f43f5e" : "#10b981";

    // H3 (F5) + H2-class coercion: every backend value rendered here is escaped
    // (round_number, which is interpolated raw) or numerically coerced before
    // .toFixed()/.toLocaleString(). The previous `t.buyer_counter_offer.toFixed(2)`
    // threw a TypeError for any turn whose counter-offer was absent.
    turnsTimeline.innerHTML = turns
      .slice()
      .reverse()
      .map((t) => {
        // P1-1: turn 契約欄位為 round_index/parsed_price/p_break；缺值 "--"。
        const tRisk = tfNum(t.p_break ?? t.breakdown_risk, null);
        const tCounter = tfNum(t.buyer_counter_offer, null);
        const tSupplier = tfNum(t.parsed_price ?? t.supplier_offer, null);
        const tRound = t.round_index ?? t.round_number ?? "—";
        return `
      <div class="timeline-turn">
        <div style="display: flex; justify-content: space-between;">
          <strong>Round ${tfEsc(tRound)}</strong>
          <span style="color: var(--accent-emerald);">${tCounter === null ? "Counter: --" : `¥${tCounter.toFixed(2)} Counter`}</span>
        </div>
        <div style="color: var(--text-muted); margin-top: 2px;">
          Supplier: ${tSupplier === null ? "--" : `¥${tSupplier.toFixed(2)}`} | Risk: ${tRisk === null ? "--" : `${(tRisk * 100).toFixed(1)}%`}
        </div>
      </div>
    `;
      })
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
