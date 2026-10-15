// TradeFlow AI — Web Portal Client Script (Vanilla JS, Zero NPM, Zero Emoji)

let beliefChart = null;
let currentSessionId = "";

document.addEventListener("DOMContentLoaded", () => {
  initBeliefChart();
  initSSE();
  fetchTelemetryConfig();
});

// ---------------------------------------------------------------------------
// Chart Initialization (Chart.js)
// ---------------------------------------------------------------------------
function initBeliefChart() {
  const ctx = document.getElementById("beliefChart");
  if (!ctx) return;

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
          data: [5.0],
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

    item.innerHTML = `
      <div class="session-item-row">
        <span class="session-item-id">${s.session_id}</span>
        <span class="session-badge ${stateClass}">${s.state}</span>
      </div>
      <div class="session-item-row" style="margin-top: 4px; color: #8899ac; font-size: 11px;">
        <span>${s.product} (Target: ¥${s.target})</span>
        <span>Rounds: ${s.rounds}</span>
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
  const pBreak = s.last_p_break !== null ? (s.last_p_break * 100).toFixed(1) : "0.0";
  document.getElementById("cbPct").textContent = `${pBreak}%`;
  document.getElementById("pBreak").textContent = `${pBreak}%`;

  const badge = document.getElementById("cbBadge");
  const ring = document.getElementById("cbRing");

  if (s.state === "AWAITING_HUMAN_DECISION") {
    badge.className = "cb-badge cb-warn";
    badge.textContent = "AWAITING HUMAN DECISION";
    ring.className = "cb-ring";
  } else if (s.last_p_break >= 0.15) {
    badge.className = "cb-badge cb-tripped";
    badge.textContent = "CIRCUIT TRIPPED (ESCALATED)";
    ring.className = "cb-ring tripped";
  } else if (s.last_p_break >= 0.08) {
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

  if (s.last_posterior !== null) {
    document.getElementById("postMean").textContent = `¥${s.last_posterior.toFixed(2)}`;
  }
  document.getElementById("roundCount").textContent = s.rounds;

  // Re-fetch rounds history to update chart
  fetchSessionDetails(s.session_id);
}

async function fetchSessionDetails(sid) {
  try {
    const res = await fetch(`/api/v1/sessions/${sid}`);
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
  const riskData = [5.0];

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

function applyCategoryPreset(cat) {
  const presets = {
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

  const p = presets[cat] || presets.umbrella;
  document.getElementById("mandatePrice").textContent = `¥${p.target.toFixed(1)} / ¥${p.ceiling.toFixed(1)}`;
  document.getElementById("mandateLead").textContent = p.lead;
  document.getElementById("mandateDeposit").textContent = p.deposit;
  document.getElementById("mandateTooling").textContent = p.tooling;

  // Create or switch active demo session with preset
  const newSid = `DEMO_${cat.toUpperCase()}_01`;
  document.getElementById("cmdSessionId").value = newSid;
  document.getElementById("sessionLabel").textContent = `${newSid} (${p.name})`;
  if (beliefChart) {
    beliefChart.data.labels = ["Prior"];
    beliefChart.data.datasets[0].data = [p.target * 1.08];
    beliefChart.data.datasets[1].data = [5.0];
    beliefChart.update();
  }
}

// ---------------------------------------------------------------------------
// Remote Command Execution (Mobile Proxy)
// ---------------------------------------------------------------------------
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
    const resp = await fetch("/webhook/mobile-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command: cmdCode,
        session_id: sid
      })
    });
    const result = await resp.json();
    if (result.status === "success") {
      resEl.textContent = `[SUCCESS] Resolved Action: ${result.action} | Session state updated.`;
    } else {
      resEl.textContent = `[FAILED] ${JSON.stringify(result)}`;
    }
  } catch (err) {
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
    const res = await fetch("/api/v1/multi-auction/run", { method: "POST" });
    const data = await res.json();
    loader.classList.add("hidden");

    if (!data.success) {
      cardsWrap.innerHTML = `<div class="empty-state">Error running multi-auction: ${data.detail || "Unknown"}</div>`;
      return;
    }

    // Render Trace of Round 3 (Final outcome)
    const lastRound = data.trace[data.trace.length - 1];
    const suppliers = (data.suppliers && data.suppliers.length > 0)
      ? data.suppliers.map(s => typeof s === "object" ? s.name : s)
      : [
          "Supplier A (浙江頂尖雨具製造)",
          "Supplier B (深圳宏達實業)",
          "Supplier C (義烏博創商貿)",
          "Supplier D (東莞榮耀五金)"
        ];

    cardsWrap.innerHTML = "";
    suppliers.forEach((sName, i) => {
      const quote = lastRound.quotes[i];
      const tactic = lastRound.tactics[i];
      const ceiling = lastRound.effective_ceilings_cny[i];
      const pBreak = (lastRound.p_break_array[i] * 100).toFixed(1);

      let tagClass = "tactic-anchor";
      if (tactic === "LEAN_IN") tagClass = "tactic-lean";
      else if (tactic === "SIDELINE") tagClass = "tactic-sideline";

      const card = document.createElement("div");
      card.className = "supplier-card";
      card.innerHTML = `
        <div class="card-top">
          <span class="supplier-title">${sName}</span>
          <span class="tactic-tag ${tagClass}">${tactic}</span>
        </div>
        <div class="card-metrics">
          <div>Quote: <span class="metric-val">¥${quote.toFixed(2)}</span></div>
          <div>BATNA: <span class="metric-val">¥${ceiling.toFixed(2)}</span></div>
          <div>Risk: <span class="metric-val">${pBreak}%</span></div>
        </div>
      `;
      cardsWrap.appendChild(card);
    });

    summaryEl.innerHTML = `
      <strong>Winner:</strong> ${data.winning_supplier} @ ¥${data.winning_price.toFixed(2)}<br/>
      <strong>Savings:</strong> ¥${data.total_savings_cny.toLocaleString()} (${(data.total_savings_pct * 100).toFixed(1)}%)<br/>
      <strong>Rollouts:</strong> ${data.total_rollouts.toLocaleString()} | Compute: ${data.total_compute_seconds}s (${data.backend})
    `;
    summaryEl.classList.remove("hidden");

  } catch (err) {
    loader.classList.add("hidden");
    cardsWrap.innerHTML = `<div class="empty-state">Request failed: ${err.message}</div>`;
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
    const res = await fetch("/api/v1/config/telemetry");
    if (!res.ok) return;
    const data = await res.json();
    const cfg = data.config || {};
    for (const [key, val] of Object.entries(cfg)) {
      const input = document.getElementById(`cfg_${key}`);
      if (input && val.set) {
        input.placeholder = `Active (${val.preview})`;
      }
    }
  } catch (err) {
    console.error("fetchTelemetryConfig error:", err);
  }
}

async function saveConfig() {
  const resEl = document.getElementById("configResult");
  const payload = {};
  
  ["CALLMEBOT_PHONE", "CALLMEBOT_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PUSHPLUS_TOKEN"].forEach(f => {
    const el = document.getElementById(`cfg_${f}`);
    if (el && el.value.trim()) payload[f] = el.value.trim();
  });

  try {
    const res = await fetch("/api/v1/config/telemetry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    resEl.className = "cmd-result";
    resEl.textContent = `[SAVED] Hot reload status: ${d.hot_reload}. Updated: ${d.fields_updated.join(", ")}`;
    resEl.classList.remove("hidden");
  } catch (err) {
    resEl.className = "cmd-result";
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: ch })
    });
    const d = await res.json();
    resEl.textContent = `[RESULT] ${JSON.stringify(d.results)}`;
  } catch (err) {
    resEl.textContent = `[TEST ERROR] ${err.message}`;
  }
}
