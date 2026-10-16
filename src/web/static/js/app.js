// TradeFlow AI — Web Portal Client Script (Vanilla JS, Zero NPM, Zero Emoji)

let beliefChart = null;
let currentSessionId = "";

// ---------------------------------------------------------------------------
// Security helpers: outbound auth + HTML escaping
// ---------------------------------------------------------------------------
// H9: the backend fail-closes in production without a valid X-API-Key. Leave
// this empty for a sandbox demo where TRADEFLOW_API_KEY is unset (no header is
// then sent and the sandbox gate allows it). When the backend operator sets a
// real key, put it here; an empty constant sends no header at all, which is
// deliberate (an empty header is indistinguishable from a bad key once a key
// is configured).
const TF_API_KEY = localStorage.getItem("tfApiKey") || "";

function tfAuthHeaders() {
  const headers = {};
  if (TF_API_KEY) headers["X-API-Key"] = TF_API_KEY;
  return headers;
}

function tfJsonHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (TF_API_KEY) headers["X-API-Key"] = TF_API_KEY;
  return headers;
}

// Every backend/SSE-sourced string must pass through tfEsc() before it is
// interpolated into an innerHTML template. Layout and CSS classes are
// unchanged; only the values are escaped. session_id / product_name /
// state are free-form strings persisted from the API, so they are untrusted.
function tfEsc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Numeric coercion for values that reach .toFixed(): a string sent by the
// backend would throw a TypeError instead of rendering, so coerce first.
function tfNum(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

document.addEventListener("DOMContentLoaded", () => {
  // P1-3: SSE 與遙測配置必須先於圖表初始化；圖表失敗（CDN 不可達）不得中止
  // 整個啟動鏈，否則會話清單永遠停在 "Connecting..."。
  initSSE();
  fetchTelemetryConfig();
  try {
    initBeliefChart();
  } catch (err) {
    console.warn("[TradeFlow] belief chart init failed (continuing):", err);
  }
});

// ---------------------------------------------------------------------------
// Chart Initialization (Chart.js)
// ---------------------------------------------------------------------------
function initBeliefChart() {
  const ctx = document.getElementById("beliefChart");
  if (!ctx) return;
  if (typeof Chart === "undefined") {
    // P1-3: Chart.js CDN 不可達時降級為無圖表，其餘功能照常。
    console.warn("[TradeFlow] Chart.js unavailable; belief chart disabled.");
    return;
  }

  beliefChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: ["Prior"],
      datasets: [
        {
          label: "Posterior Mean (¥)",
          data: [12.96],
          borderColor: "#06b6d4",
          backgroundColor: "rgba(6, 182, 212, 0.15)",
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: "#06b6d4",
          tension: 0.25,
          fill: true,
        },
        {
          label: "Breakdown Risk (%)",
          data: [null],
          borderColor: "#ef4444",
          borderWidth: 1.5,
          borderDash: [4, 4],
          pointRadius: 3,
          pointBackgroundColor: "#ef4444",
          yAxisID: "yRisk",
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: "#8899ac",
            font: { size: 10, family: "monospace" }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#8899ac", font: { size: 10 } },
          grid: { color: "#212c42" }
        },
        y: {
          ticks: { color: "#8899ac", font: { size: 10 } },
          grid: { color: "#212c42" },
          title: { display: true, text: "Price (¥)", color: "#8899ac", font: { size: 10 } }
        },
        yRisk: {
          position: "right",
          min: 0,
          max: 100,
          ticks: { color: "#ef4444", font: { size: 9 } },
          grid: { display: false },
          title: { display: true, text: "Risk %", color: "#ef4444", font: { size: 10 } }
        }
      }
    }
  });
}

// ---------------------------------------------------------------------------
// SSE Live Connection
// ---------------------------------------------------------------------------
function initSSE() {
  const statusDot = document.getElementById("sseStatus");
  const statusLabel = document.getElementById("sseLabel");

  const evtSource = new EventSource("/api/v1/stream/session");

  evtSource.onopen = () => {
    statusDot.className = "status-dot active";
    statusLabel.textContent = "CONNECTED (1s SSE)";
  };

  evtSource.onerror = () => {
    statusDot.className = "status-dot";
    statusLabel.textContent = "DISCONNECTED";
  };

  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      renderSessions(data.sessions || []);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };
}

// ---------------------------------------------------------------------------
// Render Sessions
// ---------------------------------------------------------------------------
function renderSessions(sessions) {
  const listEl = document.getElementById("sessionList");
  if (!listEl) return;

  if (sessions.length === 0) {
    listEl.innerHTML = `<div class="empty-state">No sessions. Start via API or CLI.</div>`;
    return;
  }

  listEl.innerHTML = "";
  sessions.forEach((s) => {
    const item = document.createElement("div");
    item.className = `session-item ${s.session_id === currentSessionId ? "active" : ""}`;
    item.onclick = () => selectSession(s);

    let stateClass = "badge-idle";
    if (s.state === "AUTOPILOT_CRUISING" || s.state === "COPILOT_MONITORING") stateClass = "badge-cruising";
    else if (s.state === "CIRCUIT_TRIPPED_ESCALATED" || s.state === "AWAITING_HUMAN_DECISION") stateClass = "badge-tripped";
    else if (s.state === "DEAL_CLOSED") stateClass = "badge-closed";

    // H1: session_id / state / product are free-form strings persisted from
    // the API and pushed over SSE. Every one is escaped before interpolation.
    item.innerHTML = `
      <div class="session-item-row">
        <span class="session-item-id">${tfEsc(s.session_id)}</span>
        <span class="session-badge ${tfEsc(stateClass)}">${tfEsc(s.state)}</span>
      </div>
      <div class="session-item-row" style="margin-top: 4px; color: #8899ac; font-size: 11px;">
        <span>${tfEsc(s.product)} (Target: ¥${tfEsc(s.target)})</span>
        <span>Rounds: ${tfEsc(s.rounds)}</span>
      </div>
    `;
    listEl.appendChild(item);
  });
}

function selectSession(s) {
  currentSessionId = s.session_id;
  document.getElementById("cmdSessionId").value = s.session_id;
  document.getElementById("sessionLabel").textContent = s.session_id;

  // Update Circuit Breaker view
  const pBreak = s.last_p_break != null ? (tfNum(s.last_p_break, 0) * 100).toFixed(1) : "0.0";
  document.getElementById("cbPct").textContent = `${pBreak}%`;
  document.getElementById("pBreak").textContent = `${pBreak}%`;

  const badge = document.getElementById("cbBadge");
  const ring = document.getElementById("cbRing");

  const cbRisk = tfNum(s.last_p_break, 0);
  if (s.state === "AWAITING_HUMAN_DECISION") {
    badge.className = "cb-badge cb-warn";
    badge.textContent = "AWAITING HUMAN DECISION";
    ring.className = "cb-ring";
  } else if (cbRisk >= 0.15) {
    badge.className = "cb-badge cb-tripped";
    badge.textContent = "CIRCUIT TRIPPED (ESCALATED)";
    ring.className = "cb-ring tripped";
  } else if (cbRisk >= 0.08) {
    badge.className = "cb-badge cb-warn";
    badge.textContent = "CAUTION HIGH RISK";
    ring.className = "cb-ring";
  } else if (s.state === "COPILOT_MONITORING") {
    badge.className = "cb-badge cb-safe";
    badge.textContent = "COPILOT MONITORING";
    ring.className = "cb-ring";
  } else {
    badge.className = "cb-badge cb-safe";
    badge.textContent = "COPILOT CRUISING";
    ring.className = "cb-ring";
  }

  if (s.last_posterior != null) {
    document.getElementById("postMean").textContent = `¥${tfNum(s.last_posterior, 0).toFixed(2)}`;
  }
  document.getElementById("roundCount").textContent = s.rounds;

  // Re-fetch rounds history to update chart
  fetchSessionDetails(s.session_id);
}

// V3: index.html:52 has onclick="refreshSessions()" but no such function was
// ever defined, so the Refresh button was a dead control (and threw a
// ReferenceError in the console). The SSE feed pushes every second anyway, but
// an explicit refresh should still do something honest: re-read the session
// list from the REST API and re-render it, rather than only clearing the view.
async function refreshSessions() {
  const listEl = document.getElementById("sessionList");
  try {
    const res = await fetch("/api/v1/sessions", { headers: tfAuthHeaders() });
    if (!res.ok) {
      if (listEl) {
        listEl.innerHTML = `<div class="empty-state">Could not refresh sessions (HTTP ${tfEsc(res.status)}).</div>`;
      }
      return;
    }
    const data = await res.json();
    const sessions = (Array.isArray(data) ? data : (data.sessions || [])).map((s) => {
      // GET /api/v1/sessions returns full NegotiationSession objects whose
      // fields live under `spec`; map them to the flat SSE snapshot shape that
      // renderSessions() consumes so both paths render identically.
      const spec = s.spec || {};
      return {
        session_id: spec.session_id || s.session_id,
        state: s.state,
        product: spec.product_name,
        target: spec.target_price,
        rounds: (s.history || []).length,
        last_p_break: (s.history && s.history.length) ? s.history[s.history.length - 1].p_break : null,
        last_posterior: (s.history && s.history.length) ? s.history[s.history.length - 1].posterior_mean : null,
      };
    });
    renderSessions(sessions);
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div class="empty-state">Could not refresh sessions: ${tfEsc(err.message)}</div>`;
    }
  }
}

async function fetchSessionDetails(sid) {
  try {
    const res = await fetch(`/api/v1/sessions/${encodeURIComponent(sid)}`, {
      headers: tfAuthHeaders()
    });
    if (!res.ok) return;
    const data = await res.json();
    const sessObj = data.session || data;
    if (sessObj && (sessObj.history || sessObj.turns)) {
      updateBeliefChartWithTurns(sessObj);
    }
  } catch (err) {
    console.error("fetchSessionDetails error:", err);
  }
}

function updateBeliefChartWithTurns(session) {
  if (!beliefChart) return;
  const labels = ["Prior"];
  const target = session.spec ? session.spec.target_price : 12.0;
  const postData = [1.08 * target];
  // [修正] 舊版把 Prior 的 Breakdown Risk 硬編成 5.0%，但後端從未提供任何
  // prior 破裂風險 —— 這是捏造數字。改為 null（未知），由後端/第一輪資料帶入。
  const riskData = [null];

  const turns = session.history || session.turns || [];
  turns.forEach((t, idx) => {
    labels.push(`R${t.round_index}`);
    postData.push(t.posterior_mean ? t.posterior_mean * target : target);
    riskData.push(t.p_break ? t.p_break * 100 : 0);
  });

  beliefChart.data.labels = labels;
  beliefChart.data.datasets[0].data = postData;
  beliefChart.data.datasets[1].data = riskData;
  beliefChart.update();
}

// Category presets. Each entry carries the FULL mandate, not just display
// strings: previously this only rewrote the labels and the chart, so switching
// category changed what the dashboard *claimed* while the engine kept using the
// original umbrella spec. The values below are now POSTed to
// /api/v1/sessions so the displayed mandate and the enforced mandate agree.
const CATEGORY_PRESETS = {
  umbrella: {
    name: "Custom Carbon Auto Umbrella",
    target: 12.0,
    ceiling: 16.2,
    lead: "12 - 35 Days",
    deposit: "30% - 50%",
    tooling: "Exemption Demanded (¥3,000)",
    moq: 5000
  },
  cnc_case: {
    name: "Aerospace CNC Metal Case",
    target: 28.0,
    ceiling: 36.5,
    lead: "15 - 40 Days",
    deposit: "25% - 45%",
    tooling: "Tooling Waived (¥8,000)",
    moq: 2000
  },
  battery: {
    name: "Industrial LiFePO4 Battery Pack",
    target: 185.0,
    ceiling: 240.0,
    lead: "20 - 45 Days",
    deposit: "30% - 60%",
    tooling: "Certification Waived (¥15,000)",
    moq: 500
  }
};

async function applyCategoryPreset(cat) {
  const p = CATEGORY_PRESETS[cat] || CATEGORY_PRESETS.umbrella;

  document.getElementById("mandatePrice").textContent = `¥${p.target.toFixed(1)} / ¥${p.ceiling.toFixed(1)}`;
  document.getElementById("mandateLead").textContent = p.lead;
  document.getElementById("mandateDeposit").textContent = p.deposit;
  document.getElementById("mandateTooling").textContent = p.tooling;

  // Create (or reuse) a real session so the engine actually operates under this
  // mandate instead of the display merely changing.
  const newSid = `DEMO_${cat.toUpperCase()}_01`;
  const body = {
    session_id: newSid,
    product_name: p.name,
    target_price: p.target,
    ceiling_price: p.ceiling,
    moq: p.moq,
    max_rounds: 5
  };

  const statusEl = document.getElementById("presetStatus");
  try {
    const res = await fetch("/api/v1/sessions", {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify(body)
    });
    if (res.ok) {
      currentSessionId = newSid;
      document.getElementById("cmdSessionId").value = newSid;
      document.getElementById("sessionLabel").textContent = `${newSid} (${p.name})`;
      if (statusEl) {
        statusEl.textContent = `Session ${newSid} created — engine now bound to this mandate.`;
        statusEl.style.color = "#4ade80";
      }
      fetchSessionDetails(newSid);
    } else {
      const detail = await res.text();
      if (statusEl) {
        statusEl.textContent = `Could not create session (HTTP ${tfEsc(res.status)}): ${detail.slice(0, 140)}`;
        statusEl.style.color = "#f87171";
      }
    }
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = `Session creation failed: ${err.message}`;
      statusEl.style.color = "#f87171";
    }
  }

  if (beliefChart) {
    beliefChart.data.labels = ["Prior"];
    beliefChart.data.datasets[0].data = [p.target * 1.08];
    beliefChart.data.datasets[1].data = [null];
    beliefChart.update();
  }
}

// ---------------------------------------------------------------------------
// Remote Command Execution (Mobile Proxy)
// ---------------------------------------------------------------------------
// H9 (webhook half): POST /webhook/mobile-command is gated by a shared secret
// header (X-Webhook-Token), sent only when a value is configured. It stays
// fail-open in sandbox when the server-side value is unset. Same truthy-guard
// rule as TF_API_KEY (send no header when we have no value configured). Set a
// value at runtime via localStorage.setItem("tfWebhookToken", ...) in DevTools.
const TF_WEBHOOK_AUTH = localStorage.getItem("tfWebhookToken") || "";

async function sendCommand(cmdCode) {
  const sid = document.getElementById("cmdSessionId").value.trim() || currentSessionId;
  const resEl = document.getElementById("cmdResult");

  if (!sid) {
    resEl.className = "cmd-result";
    resEl.textContent = "Error: Please specify or select a Session ID first.";
    resEl.classList.remove("hidden");
    return;
  }

  resEl.className = "cmd-result";
  resEl.textContent = `Dispatching remote command '${cmdCode}' for session ${sid}...`;
  resEl.classList.remove("hidden");

  try {
    const headers = { "Content-Type": "application/json" };
    if (TF_WEBHOOK_AUTH) headers["X-Webhook-Token"] = TF_WEBHOOK_AUTH;

    const resp = await fetch("/webhook/mobile-command", {
      method: "POST",
      headers,
      body: JSON.stringify({
        command: cmdCode,
        session_id: sid
      })
    });
    // H4-style false-success guard: a 400/401/500 must never be reported as a
    // successful dispatch. Read the body defensively (an error response with a
    // non-JSON body would otherwise throw on .json()).
    let result = null;
    try {
      result = await resp.json();
    } catch (_) {
      result = null;
    }
    if (!resp.ok) {
      const detail = (result && result.detail) || "";
      resEl.className = "cmd-result";
      resEl.style.color = "#f87171";
      resEl.textContent = `[HTTP ${resp.status}] Command NOT applied: ${detail || resp.statusText || "request rejected"}`;
      return;
    }
    if (result && result.status === "success") {
      resEl.textContent = `[SUCCESS] Resolved Action: ${result.action} | Session state updated.`;
    } else {
      // HTTP 200 but the backend did not apply the command (e.g. unknown
      // session with no pending authorization bridge). Do not claim success.
      const reason = (result && (result.detail || result.reason)) || "no action applied";
      resEl.className = "cmd-result";
      resEl.style.color = "#f87171";
      resEl.textContent = `[NOT APPLIED] ${reason}`;
    }
  } catch (err) {
    resEl.className = "cmd-result";
    resEl.style.color = "#f87171";
    resEl.textContent = `[NETWORK ERROR] ${err.message}`;
  }
}

// ---------------------------------------------------------------------------
// Multi-Supplier Reverse Auction (28.8M Rollouts Demo)
// ---------------------------------------------------------------------------
async function runMultiAuction() {
  const loader = document.getElementById("auctionLoader");
  const cardsWrap = document.getElementById("supplierCards");
  const summaryEl = document.getElementById("auctionSummary");

  loader.classList.remove("hidden");
  cardsWrap.innerHTML = "";
  summaryEl.classList.add("hidden");

  try {
    const res = await fetch("/api/v1/multi-auction/run", {
      method: "POST",
      headers: tfAuthHeaders()
    });
    if (!res.ok) {
      loader.classList.add("hidden");
      cardsWrap.innerHTML = `<div class="empty-state">Multi-auction rejected (HTTP ${tfEsc(res.status)}).</div>`;
      return;
    }
    const data = await res.json();
    loader.classList.add("hidden");

    if (!data.success) {
      cardsWrap.innerHTML = `<div class="empty-state">Error running multi-auction: ${tfEsc(data.detail || "Unknown")}</div>`;
      return;
    }

    // Render Trace of Round 3 (Final outcome)
    const lastRound = data.trace[data.trace.length - 1];
    const suppliers = (data.suppliers && data.suppliers.length > 0)
      ? data.suppliers.map(s => typeof s === "object" ? s.name : s)
      // [修正] 這是永不觸發的防呆 fallback（API 永遠回傳 suppliers）。
      // 舊版在此硬編了「浙江頂尖/深圳宏達/義烏博創/東莞榮耀」等虛構公司名，
      // 與引擎真實身份(敏捷讓步型…) 錯位。改為中立標籤，不再捏造公司。
      : ["Supplier A", "Supplier B", "Supplier C", "Supplier D"];

    cardsWrap.innerHTML = "";
    suppliers.forEach((sName, i) => {
      const quote = lastRound.quotes[i];
      const tactic = lastRound.tactics[i];
      const ceiling = lastRound.effective_ceilings_cny[i];
      const pBreak = (tfNum(lastRound.p_break_array[i], 0) * 100).toFixed(1);

      let tagClass = "tactic-anchor";
      if (tactic === "LEAN_IN") tagClass = "tactic-lean";
      else if (tactic === "SIDELINE") tagClass = "tactic-sideline";

      const card = document.createElement("div");
      card.className = "supplier-card";
      card.innerHTML = `
        <div class="card-top">
          <span class="supplier-title">${tfEsc(sName)}</span>
          <span class="tactic-tag ${tfEsc(tagClass)}">${tfEsc(tactic)}</span>
        </div>
        <div class="card-metrics">
          <div>Quote: <span class="metric-val">¥${tfNum(quote, 0).toFixed(2)}</span></div>
          <div>BATNA: <span class="metric-val">¥${tfNum(ceiling, 0).toFixed(2)}</span></div>
          <div>Risk: <span class="metric-val">${tfEsc(pBreak)}%</span></div>
        </div>
      `;
      cardsWrap.appendChild(card);
    });

    summaryEl.innerHTML = `
      <strong>Winner:</strong> ${tfEsc(data.winning_supplier)} @ ¥${tfNum(data.winning_price, 0).toFixed(2)}<br/>
      <strong>Savings:</strong> ¥${tfNum(data.total_savings_cny, 0).toLocaleString()} (${(tfNum(data.total_savings_pct, 0) * 100).toFixed(1)}%)<br/>
      <strong>Rollouts:</strong> ${tfNum(data.total_rollouts, 0).toLocaleString()} | Compute: ${tfEsc(data.total_compute_seconds)}s (${tfEsc(data.backend)})
    `;
    summaryEl.classList.remove("hidden");

  } catch (err) {
    loader.classList.add("hidden");
    cardsWrap.innerHTML = `<div class="empty-state">Request failed: ${tfEsc(err.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Telemetry Settings
// ---------------------------------------------------------------------------
function toggleConfig() {
  const body = document.getElementById("configBody");
  body.classList.toggle("hidden");
}

async function fetchTelemetryConfig() {
  try {
    const res = await fetch("/api/v1/config/telemetry", { headers: tfAuthHeaders() });
    if (!res.ok) {
      console.warn("Telemetry config unavailable (HTTP", res.status + ")");
      return;
    }
    const data = await res.json();
    const cfg = data.config || {};
    for (const [key, val] of Object.entries(cfg)) {
      const input = document.getElementById(`cfg_${key}`);
      // Backend may return {"set": bool, "preview": str} or, after the security
      // hardening pass, only {"set": bool} with no credential preview at all.
      // Handle both shapes so a missing preview does not render "Active (undefined)".
      if (input && val && val.set) {
        input.placeholder = val.preview ? `Active (${val.preview})` : "Active (configured)";
      }
    }
  } catch (err) {
    console.error("fetchTelemetryConfig error:", err);
  }
}

async function saveConfig() {
  const resEl = document.getElementById("configResult");
  const payload = {};

  // V2: the server allowlist (_TELEMETRY_FIELDS in routes_web.py:114-121,
  // matching TelemetryNotifier.__init__) is WHATSAPP_PHONE / WHATSAPP_APIKEY /
  // SERVERCHAN_SENDKEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. The previous
  // list here (CALLMEBOT_*/PUSHPLUS_TOKEN) matched no allowlisted key, so
  // saveConfig() silently posted an empty object and reported "[SAVED]" while
  // nothing was ever written. Keep sending nothing for a key the operator did
  // not fill in, but only name allowlisted keys.
  ["WHATSAPP_PHONE", "WHATSAPP_APIKEY", "SERVERCHAN_SENDKEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"].forEach(f => {
    const el = document.getElementById(`cfg_${f}`);
    if (el && el.value.trim()) payload[f] = el.value.trim();
  });

  try {
    const res = await fetch("/api/v1/config/telemetry", {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      resEl.className = "cmd-result";
      resEl.style.color = "#f87171";
      resEl.textContent = `[SAVE FAILED] HTTP ${res.status}: ${res.statusText || "request rejected"}`;
      resEl.classList.remove("hidden");
      return;
    }
    const d = await res.json();
    resEl.className = "cmd-result";
    const updated = Array.isArray(d.fields_updated) ? d.fields_updated.join(", ") : "";
    resEl.textContent = `[SAVED] Hot reload status: ${d.hot_reload}. Updated: ${updated || "(no allowlisted fields changed)"}`;
    resEl.classList.remove("hidden");
  } catch (err) {
    resEl.className = "cmd-result";
    resEl.style.color = "#f87171";
    resEl.textContent = `[SAVE ERROR] ${err.message}`;
    resEl.classList.remove("hidden");
  }
}

async function testPush(ch) {
  const resEl = document.getElementById("configResult");
  resEl.className = "cmd-result";
  resEl.textContent = `Sending test message to channel '${ch}'...`;
  resEl.classList.remove("hidden");

  try {
    const res = await fetch("/api/v1/telemetry/test-push", {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify({ channel: ch })
    });
    if (!res.ok) {
      resEl.className = "cmd-result";
      resEl.style.color = "#f87171";
      resEl.textContent = `[TEST FAILED] HTTP ${res.status}: ${res.statusText || "request rejected"}`;
      return;
    }
    const d = await res.json();
    resEl.textContent = `[RESULT] ${JSON.stringify(d.results)}`;
  } catch (err) {
    resEl.className = "cmd-result";
    resEl.style.color = "#f87171";
    resEl.textContent = `[TEST ERROR] ${err.message}`;
  }
}
