/**
 * TradeFlow Autopilot — Side Panel Controller
 * Synchronizes real-time Bayesian beliefs, breakdown probabilities,
 * and two-way remote mobile override telemetry with the FastAPI backend.
 */

const API_BASE_URL = "http://localhost:8000";
let activeSessionId = "live-session-001";

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

// Fetch and render session data
async function fetchSessionData() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/sessions/${activeSessionId}`);
    if (!res.ok) {
      // If live-session-001 doesn't exist yet, attempt to auto-create
      if (res.status === 404) {
        await initDefaultSession();
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const session = await res.json();
    renderSession(session);
  } catch (err) {
    console.warn("[TradeFlow Sidepanel] Could not fetch session:", err.message);
  }
}

async function initDefaultSession() {
  try {
    const createRes = await fetch(`${API_BASE_URL}/api/v1/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: activeSessionId,
        supplier_id: "supp-1688-top",
        product_name: "Auto Umbrella (Custom Carbon Fiber)",
        target_price: 12.0,
        walk_away_ceiling: 16.2,
      }),
    });
    if (createRes.ok) {
      const data = await createRes.json();
      renderSession(data);
    }
  } catch (e) {
    console.error("[TradeFlow Sidepanel] Failed to init session:", e);
  }
}

function renderSession(session) {
  if (!session) return;

  // Session ID & targets
  sessionIdDisplay.innerText = session.session_id;
  valTargetPrice.innerText = `¥${session.target_price.toFixed(2)}`;
  valReserveCeiling.innerText = `¥${session.walk_away_ceiling.toFixed(2)}`;

  // Bayesian Posterior
  valPosteriorMean.innerText = session.current_posterior_mean.toFixed(3);
  valPosteriorStd.innerText = session.current_posterior_std.toFixed(3);

  // Status & Circuit Breaker
  const stateStr = session.state;
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

  // Turns timeline & Breakdown Risk
  const turns = session.turns || [];
  roundCounter.innerText = `Round ${turns.length} / ${session.max_rounds}`;

  if (turns.length > 0) {
    const lastTurn = turns[turns.length - 1];
    const risk = lastTurn.breakdown_risk;
    const riskPct = (risk * 100).toFixed(1);
    valBreakdownRisk.innerText = `${riskPct}%`;

    // Fill width capped at 100%
    const barWidth = Math.min(100, Math.max(5, risk * 100));
    progressBreakdown.style.width = `${barWidth}%`;

    if (risk >= 0.15) {
      valBreakdownRisk.style.color = "#f43f5e";
    } else {
      valBreakdownRisk.style.color = "#10b981";
    }

    // Render timeline
    turnsTimeline.innerHTML = turns
      .map(
        (t) => `
      <div class="timeline-turn">
        <div style="display: flex; justify-content: space-between;">
          <strong>Round ${t.round_number}</strong>
          <span style="color: var(--accent-emerald);">¥${t.buyer_counter_offer.toFixed(2)} Counter</span>
        </div>
        <div style="color: var(--text-muted); margin-top: 2px;">
          Supplier: ¥${t.supplier_offer.toFixed(2)} | Risk: ${(t.breakdown_risk * 100).toFixed(1)}%
        </div>
      </div>
    `
      )
      .reverse()
      .join("");
  }
}

// Send Remote Override Command
async function sendRemoteCommand(cmd) {
  try {
    const res = await fetch(`${API_BASE_URL}/webhook/mobile-command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel: "sidepanel",
        sender_id: "human_supervisor",
        raw_text: cmd,
        session_id: activeSessionId,
      }),
    });
    const result = await res.json();
    valLastSignal.innerText = `Command '${cmd}' dispatched (${result.action || "OK"})`;
    setTimeout(fetchSessionData, 500);
  } catch (err) {
    console.error("[TradeFlow Sidepanel] Command dispatch error:", err);
  }
}

// Wire Event Listeners
document.getElementById("btn-refresh")?.addEventListener("click", fetchSessionData);
document.getElementById("btn-concede")?.addEventListener("click", () => sendRemoteCommand("1"));
document.getElementById("btn-hold")?.addEventListener("click", () => sendRemoteCommand("2"));
document.getElementById("btn-abort")?.addEventListener("click", () => sendRemoteCommand("3"));

// Listen for broadcast messages from background script
chrome.runtime.onMessage?.addListener((msg) => {
  if (msg.type === "SESSION_UPDATED") {
    fetchSessionData();
  }
});

// Periodic polling every 2.5 seconds
setInterval(fetchSessionData, 2500);

// Initial Load
fetchSessionData();
