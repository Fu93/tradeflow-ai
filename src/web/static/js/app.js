// TradeFlow AI — Web Portal Client Script (Vanilla JS, Zero NPM, Zero Emoji)

let beliefChart = null;
let currentSessionId = "";
// 上一次已渲染的「選取會話」快照簽章，用來避免 SSE 每秒重繪造成閃爍。
let _lastSelectedSig = "";

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
//
// [P0 REGRESSION — 2026-10-17 實機稽查] `Number(null)` 與 `Number("")` 都是 0，
// 因此舊版把「後端沒有這個值」渲染成 0：latest_our_offer=null 顯示 ¥0.00、
// last_p_break=null 顯示 0.0% / SAFE。這正是「看起來有數據、其實沒有」的
// 假數據來源。null / undefined / 空字串一律視為缺值，回傳 fallback。
function tfNum(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

// Safety bands and the sandbox catalogue. Empty until /api/v1/heuristics and
// /api/v1/simulation/mandates answer. The desk does not invent either.
let tfHeuristics = null;
let categoryPresets = null;

function tfTrip() {
  const n = tfHeuristics && Number(tfHeuristics.breakdown_trip_threshold);
  return Number.isFinite(n) ? n : null;
}

function tfCaution() {
  const n = tfHeuristics && Number(tfHeuristics.caution_risk_threshold);
  return Number.isFinite(n) ? n : null;
}

function tfMoney(value) {
  return tfAmount(value, "CNY");
}

function tfAmount(value, currency) {
  const n = tfNum(value, null);
  if (n === null) return "—";
  const negative = n < 0;
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 0 : 2;
  const scale = digits === 0 ? 1 : 100;
  const cents = Math.round(abs * scale);
  const wholeNum = digits === 0 ? cents : Math.floor(cents / scale);
  const frac = digits === 0 ? "" : String(cents % scale).padStart(2, "0");
  const whole = String(wholeNum).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const body = frac && frac !== "00" ? whole + "." + frac : whole;
  const code = currency ? " " + currency : "";
  return (negative ? "-" : "") + body + code;
}

function tfPct(value) {
  const n = tfNum(value, null);
  if (n === null) return "";
  const shown = Math.round(n * 1000) / 10;
  const text = shown.toFixed(1) + "%";
  return shown > 0 ? "+" + text : text;
}

function isHiddenSession(id) {
  return /^(smoke-|probe-|ui-demo-|xss)/i.test(String(id || ""));
}

function quoteOnSession(session) {
  const turns = (session && (session.history || session.turns)) || [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const entities = (turns[i].metadata || {}).entities || {};
    if (entities.currency || entities.quoted_amount != null) {
      return {
        currency: entities.currency || null,
        amount: tfNum(entities.quoted_amount, null),
        note: entities.currency_note || "",
      };
    }
  }
  return null;
}

function friendlyState(name) {
  const labels = {
    AWAITING_HUMAN_DECISION: "Waiting for you",
    COPILOT_MONITORING: "Watching",
    CIRCUIT_TRIPPED_ESCALATED: "Needs you",
    HUMAN_APPROVED_APPLIED: "Approved",
    WALK_AWAY: "Walked away",
    DEAL_CLOSED: "Closed",
    AUTOPILOT_CRUISING: "Cruising",
  };
  return labels[name] || name;
}

function tfTone(el, tone) {
  if (!el) return;
  el.style.color = "";
  el.classList.toggle("is-ok", tone === "is-ok");
  el.classList.toggle("is-bad", tone === "is-bad");
}

document.addEventListener("DOMContentLoaded", () => {
  try {
    const marketRoot = document.getElementById("marketIntel");
    if (marketRoot && window.TradeFlowMarket) {
      window.TradeFlowMarket.mount(marketRoot, {
        endpoint: "/api/v1/market",
        headers: tfAuthHeaders,
      });
    }
  } catch (err) {
    console.warn("[TradeFlow] market intel init failed (continuing):", err);
  }
  // P1-3: SSE 與遙測配置必須先於圖表初始化；圖表失敗（CDN 不可達）不得中止
  // 整個啟動鏈，否則會話清單永遠停在 "Connecting..."。
  initSSE();
  bindDeskChrome();
  fetchTelemetryConfig();
  loadDeskConfig();
  paintAmdAvailability();
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
          label: "Posterior mean (CNY)",
          // Empty until a real posterior arrives: the old hardcoded 12.96 drew
          // a data point for a session that had never been measured.
          data: [],
          borderColor: "#1e3a4c",
          backgroundColor: "rgba(30, 58, 76, 0.12)",
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: "#1e3a4c",
          tension: 0.25,
          fill: true,
        },
        {
          label: "Breakdown risk (%)",
          data: [null],
          borderColor: "#9a3412",
          borderWidth: 2,
          borderDash: [4, 4],
          pointRadius: 3,
          pointBackgroundColor: "#9a3412",
          yAxisID: "yRisk",
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          labels: {
            color: "#3e463f",
            boxWidth: 14,
            font: { size: 12, family: '"Outfit TF", sans-serif' }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#4e5a52", font: { size: 11, family: '"Outfit TF", sans-serif' } },
          grid: { color: "#ddd2c2" },
          border: { color: "#b7a894" }
        },
        y: {
          ticks: { color: "#4e5a52", font: { size: 11, family: '"Outfit TF", sans-serif' } },
          grid: { color: "#efe6d6" },
          border: { color: "#b7a894" },
          title: { display: true, text: "Price (CNY)", color: "#4e5a52", font: { size: 11, family: '"Outfit TF", sans-serif' } }
        },
        yRisk: {
          position: "right",
          min: 0,
          max: 100,
          ticks: { color: "#9a3412", font: { size: 10, family: '"Outfit TF", sans-serif' } },
          grid: { display: false },
          border: { color: "#ddd2c2" },
          title: { display: true, text: "Risk %", color: "#9a3412", font: { size: 11, family: '"Outfit TF", sans-serif' } }
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
    statusDot.className = "feed-dot active";
    statusLabel.textContent = "Live";
  };

  evtSource.onerror = () => {
    statusDot.className = "feed-dot";
    statusLabel.textContent = "Reconnecting";
  };

  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      // [P0] 除了更新清單，也要讓「已選取會話」的詳情跟著串流走（見 onSseSessions）。
      onSseSessions(data.sessions || []);
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

  const visible = sessions.filter((s) => !isHiddenSession(s.session_id));
  if (visible.length === 0) {
    listEl.innerHTML = `<div class="empty-state">No deals on file. Open a sandbox deal from the mandate, or start one from the API.</div>`;
    return;
  }

  listEl.innerHTML = "";
  visible.forEach((s) => {
    const item = document.createElement("div");
    item.className = `session-item ${s.session_id === currentSessionId ? "active" : ""}`;
    item.onclick = () => selectSession(s);

    let stateClass = "badge-idle";
    if (s.state === "AUTOPILOT_CRUISING" || s.state === "COPILOT_MONITORING") stateClass = "badge-cruising";
    else if (s.state === "CIRCUIT_TRIPPED_ESCALATED" || s.state === "AWAITING_HUMAN_DECISION" || s.state === "WALK_AWAY") stateClass = "badge-tripped";
    else if (s.state === "DEAL_CLOSED" || s.state === "HUMAN_APPROVED_APPLIED") stateClass = "badge-closed";

    // H1: session_id / state / product are free-form strings persisted from
    // the API and pushed over SSE. Every one is escaped before interpolation.
    item.innerHTML = `
      <div class="session-item-row">
        <span class="session-item-id">${tfEsc(s.session_id)}</span>
        <span class="session-badge ${tfEsc(stateClass)}">${tfEsc(friendlyState(s.state))}</span>
      </div>
      <div class="session-item-row session-item-sub">
        <span>${tfEsc(s.product)} (target ${tfEsc(tfAmount(s.target, "CNY"))})</span>
        <span>${tfEsc(s.rounds)} ${Number(s.rounds) === 1 ? "round" : "rounds"}</span>
      </div>
    `;
    listEl.appendChild(item);
  });
}

function clearStaleDecisionPrompt() {
  const result = document.getElementById("decisionResult");
  if (result && result.textContent.indexOf("Open a sandbox deal first") === 0) {
    result.textContent = "";
  }
}

function selectSession(s) {
  currentSessionId = s.session_id;
  clearStaleDecisionPrompt();
  document.getElementById("cmdSessionId").value = s.session_id;
  document.getElementById("sessionLabel").textContent = s.session_id;
  const titleEl = document.getElementById("dealTitle");
  if (titleEl) {
    titleEl.textContent = s.product ? String(s.product) : s.session_id;
  }

  // Update Circuit Breaker view.
  // [P0 REGRESSION — 2026-10-17 實機稽查] 舊版在 last_p_break 缺失時把畫面填成
  // "0.0%"（並落入 SAFE 分支），等於對一個從未量測過的會話宣佈「零破局風險」。
  // 現在缺值一律顯示 "—"，且不得被標示為 SAFE。
  const riskKnown = s.last_p_break !== null && s.last_p_break !== undefined &&
    Number.isFinite(Number(s.last_p_break));
  const cbRisk = riskKnown ? Number(s.last_p_break) : null;
  const pBreak = riskKnown ? (cbRisk * 100).toFixed(1) : null;
  document.getElementById("cbPct").textContent = pBreak === null ? "—" : `${pBreak}%`;
  document.getElementById("pBreak").textContent = pBreak === null ? "—" : `${pBreak}%`;

  const badge = document.getElementById("cbBadge");
  const ring = document.getElementById("cbRing");
  // The risk scale is a ruled strip whose marker lives at --risk (0-100).
  if (ring) {
    ring.style.setProperty("--risk", cbRisk === null ? "0" : String(Math.max(0, Math.min(100, cbRisk * 100))));
  }

  if (s.state === "CIRCUIT_TRIPPED_ESCALATED") {
    badge.className = "cb-badge cb-tripped";
    badge.textContent = "CIRCUIT TRIPPED (ESCALATED)";
    ring.className = "cb-ring tripped";
  } else if (s.state === "WALK_AWAY") {
    badge.className = "cb-badge cb-tripped";
    badge.textContent = "WALK AWAY";
    ring.className = "cb-ring tripped";
  } else if (s.state === "AWAITING_HUMAN_DECISION") {
    badge.className = "cb-badge cb-warn";
    badge.textContent = "Waiting for your decision";
    ring.className = "cb-ring";
  } else if (s.state === "DEAL_CLOSED") {
    badge.className = "cb-badge cb-safe";
    badge.textContent = "DEAL CLOSED";
    ring.className = "cb-ring";
  } else if (s.state === "HUMAN_APPROVED_APPLIED") {
    badge.className = "cb-badge cb-safe";
    badge.textContent = "HUMAN APPROVED";
    ring.className = "cb-ring";
  } else if (cbRisk !== null && tfTrip() !== null && cbRisk >= tfTrip()) {
    badge.className = "cb-badge cb-tripped";
    badge.textContent = "CIRCUIT TRIPPED (ESCALATED)";
    ring.className = "cb-ring tripped";
  } else if (cbRisk !== null && tfCaution() !== null && cbRisk >= tfCaution()) {
    badge.className = "cb-badge cb-warn";
    badge.textContent = "CAUTION HIGH RISK";
    ring.className = "cb-ring";
  } else if (cbRisk === null) {
    badge.className = "cb-badge";
    badge.textContent = "RISK NOT YET MEASURED";
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
    // [P1 REGRESSION — 瀏覽器實測發現] last_posterior 是**歸一化**的後驗底價乘數
    // （≈1.08–1.18，1.0 = 目標價），不是價格。舊版直接 `¥${v.toFixed(2)}` 渲染成
    // 「¥1.14」，而同一個面板的折線圖畫的是已乘上目標價的 13.74 —— 面板與自己的
    // 圖表相差一個數量級（目標價 ¥12.00 的會話顯示 ¥1.14）。
    // 現改為乘上 session 的 target（SSE payload 已帶此欄位）再顯示。
    const targetPx = tfNum(s.target, 0);
    const meanPx = targetPx > 0 ? tfNum(s.last_posterior, 0) * targetPx : null;
    document.getElementById("postMean").textContent =
      meanPx == null ? "—" : tfAmount(meanPx, "CNY");
  } else {
    document.getElementById("postMean").textContent = "--";
  }
  document.getElementById("roundCount").textContent = s.rounds;

  // Re-fetch rounds history to update chart
  fetchSessionDetails(s.session_id);
}

// [P0 REGRESSION — 瀏覽器實測發現] SSE 每秒推播只呼叫 renderSessions() 更新左側
// 清單；而熔斷環、後驗均值、折線圖這些「詳情」只在**使用者點擊**時才更新
// （selectSession 由 item.onclick 呼叫）。因此當有人在頁面開著時透過 API / CLI
// 跑一輪談判，畫面看起來像凍結：清單的 Rounds 變了，但右側數字與圖表完全不動。
// 這直接打穿了 demo 的「即時串流」敘事。
// 現補上：若目前有選取中的會話，且該會話的快照有變化（回合數或風險值改變），
// 就重新渲染詳情並重抓歷史。只做「有變化才更新」，避免每秒重繪造成閃爍。
function onSseSessions(sessions) {
  renderSessions(sessions);
  if (!currentSessionId) return;
  const sel = sessions.find((x) => x.session_id === currentSessionId);
  if (!sel) return;
  const sig = `${sel.rounds}|${sel.last_p_break}|${sel.last_posterior}|${sel.state}`;
  if (sig === _lastSelectedSig) return;
  _lastSelectedSig = sig;
  selectSession(sel);
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
    const spec = (sessObj && sessObj.spec) || {};
    paintCustomsReference(spec.market_context);
    if (sessObj && (sessObj.history || sessObj.turns)) {
      renderPriceLadder(sessObj);
      updateBeliefChartWithTurns(sessObj);
      renderEngineDecision(sessObj);
    }
  } catch (err) {
    console.error("fetchSessionDetails error:", err);
  }
}

// [P0 新增] 把引擎的**實際決策**渲染到 Engine Decision 面板。
// 資料來源全部取自本次 GET /api/v1/sessions/{id} 的 history[-1]：
//   - 意圖        → turn.metadata.entities / turn.raw_text 無法反推，改由
//                   turn.raw_text 顯示原文，意圖則來自 API 的最近回應（見下）
//   - 建議還價    → 後端未把 recommendation 存進 history，故以
//                   latest_our_offer（我方最後出價）與 latest_opponent_offer 呈現
//   - 破局風險    → turn.p_break（真實值）
//   - 生成話術    → turn.raw_text 中 sender 為 buyer_agent / buyer_human_approved 者
// 缺值一律顯示 "—"（絕不捏造）。
// [UI REDESIGN] The price ladder is the dashboard's hero: it answers the one
// question a buyer asks first — how much room is left between the supplier's
// ask and the walk-away ceiling. Every value comes from the session snapshot;
// anything missing renders "—" and hides its marker rather than guessing.
function renderPriceLadder(session) {
  const setText = (id, txt) => {
    const el = document.getElementById(id);
    if (el) el.textContent = txt;
  };
  const spec = session.spec || {};
  const target = tfNum(spec.target_price, null);
  const ceiling = tfNum(spec.ceiling_price, null);

  setText("ladderTarget", tfAmount(target, "CNY"));
  setText("ladderCeiling", tfAmount(ceiling, "CNY"));

  const turns = session.history || session.turns || [];
  const lastOpp = [...turns].reverse().find((t) => t.sender === "opponent");
  let ask = tfNum(session.latest_opponent_offer, null);
  if (ask === null && lastOpp) ask = tfNum(lastOpp.parsed_price, null);

  let counter = tfNum(session.latest_our_offer, null);
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const v = tfNum((turns[i].metadata || {}).recommended_offer_price, null);
    if (v !== null) { counter = v; break; }
  }

  const span = (target !== null && ceiling !== null) ? ceiling - target : null;
  const place = (value) => {
    // Ceiling sits at the top of the track, target at the bottom.
    const pct = ((ceiling - value) / span) * 100;
    return Math.max(0, Math.min(100, pct));
  };

  const askMarker = document.getElementById("ladderAskMarker");
  const counterMarker = document.getElementById("ladderCounterMarker");
  const hintEl = document.getElementById("ladderHint");
  const noteEl = document.getElementById("ladderNote");
  const plottable = span !== null && span > 0;
  const hasQuote = plottable && (ask !== null || counter !== null);
  if (hintEl) hintEl.hidden = hasQuote;
  const ladderEl = document.getElementById("ladder");
  if (ladderEl) ladderEl.classList.toggle("is-empty", !hasQuote);

  if (plottable && ask !== null) {
    askMarker.style.top = `calc(${place(ask)}% - 8px)`;
    askMarker.hidden = false;
    setText("ladderAskValue", tfAmount(ask, "CNY"));
  } else {
    askMarker.hidden = true;
    setText("ladderAskValue", "—");
  }

  if (plottable && counter !== null) {
    // When the counter sits on (or nearly on) the ask, both tags would overlap;
    // nudge only the label down so each number stays readable.
    let topPct = place(counter);
    if (ask !== null && Math.abs(place(ask) - topPct) < 9) {
      topPct = Math.min(100, topPct + 9);
    }
    counterMarker.style.top = `calc(${topPct}% - 8px)`;
    counterMarker.hidden = false;
    setText("ladderCounterValue", tfAmount(counter, "CNY"));
  } else {
    counterMarker.hidden = true;
    setText("ladderCounterValue", "—");
  }

  const round = tfNum(session.current_round, null);
  if (noteEl) {
    noteEl.textContent = !plottable
      ? "Select a deal to plot the quote"
      : (round === null ? "Quote against your authorised range" : `Round ${round}, quote against your authorised range`);
  }

  // Quarter ticks make the axis readable between the two end labels.
  const ticksEl = document.getElementById("ladderTicks");
  if (ticksEl) {
    ticksEl.innerHTML = "";
    if (plottable) {
      // Round money steps, not quarter fractions: a price axis reads in ¥1 or
      // ¥0.50 increments, and the labels stay stable as the range changes.
      const fine = tfHeuristics && Number(tfHeuristics.ladder_step_fine);
      const mid = tfHeuristics && Number(tfHeuristics.ladder_step_mid);
      const edgeLo = tfHeuristics && Number(tfHeuristics.ladder_edge_lo);
      const edgeHi = tfHeuristics && Number(tfHeuristics.ladder_edge_hi);
      const step = span <= 1 ? fine : span <= 4 ? mid : span <= 12 ? 1 : span <= 60 ? 5 : 25;
      if (!Number.isFinite(step) || step <= 0 || !Number.isFinite(edgeLo) || !Number.isFinite(edgeHi)) {
        ticksEl.innerHTML = "";
      } else {
      let lastTickPct = -1;
      for (let v = Math.ceil(target / step) * step; v < ceiling; v += step) {
        const frac = (ceiling - v) / span;
        // Keep clear of the two end labels (ceiling at 0, target at 1).
        if (v <= target || frac < edgeLo || frac > edgeHi) continue;
        const tickPct = Math.round(frac * 100);
        if (lastTickPct >= 0 && Math.abs(tickPct - lastTickPct) < 8) continue;
        lastTickPct = tickPct;
        const row = document.createElement("div");
        row.className = "track-tick";
        row.style.top = `${frac * 100}%`;
        const label = document.createElement("span");
        label.textContent = tfAmount(v, "CNY");
        row.appendChild(label);
        ticksEl.appendChild(row);
      }
      }
    }
  }

  const quote = quoteOnSession(session);
  const foreign = quote && quote.currency && quote.currency !== "CNY" && quote.amount !== null;
  setText(
    "ladderRoom",
    ask === null || ceiling === null ? "—" : tfAmount(ceiling - ask, "CNY")
  );
  setText(
    "ladderPremium",
    ask === null || target === null || target <= 0
      ? "—"
      : `${tfAmount(ask - target, "CNY")} (${tfPct((ask - target) / target)})`
  );
  if (foreign && ask === null && noteEl) {
    noteEl.textContent = `Supplier quote ${tfAmount(quote.amount, quote.currency)} is not plotted on this CNY ladder. ${quote.note || "No conversion rate is set."}`;
  }
}

function customsReferenceLine(ctx) {
  if (!ctx || typeof ctx !== "object") {
    return "REFERENCE ONLY. Customs reference: not available. Implied customs unit value is not a price quote.";
  }
  const pick = (value) => (value === null || value === undefined || value === "" ? "not available" : String(value));
  const provenance = ctx.provenance || {};
  return (
    "REFERENCE ONLY. Implied customs unit value is not a price quote. HS " + pick(ctx.hs) +
    ". Year " + pick(ctx.year) +
    ". UK imports from China " + pick(ctx.value_usd) + " USD. Quantity " + pick(ctx.qty) +
    ". Implied customs unit value " + pick(ctx.unit_value_usd) + " USD. Source " + pick(provenance.source) +
    ". Fetched " + pick(provenance.fetched_at) +
    ". Status " + pick(ctx.status) +
    ". CIF customs valuation is not a supplier offer and does not set the counter."
  );
}

function paintCustomsReference(ctx) {
  const el = document.getElementById("marketReference");
  if (el) el.textContent = customsReferenceLine(ctx);
  const root = document.getElementById("marketIntel");
  if (root && window.TradeFlowMarket) window.TradeFlowMarket.paint(root, ctx || null);
}

function renderEngineDecision(session) {
  const el = (id) => document.getElementById(id);
  if (!el("decisionIntent")) return; // 面板不存在時安全退出

  const turns = session.history || session.turns || [];
  const maxRounds = tfNum((session.spec || {}).max_rounds, null);
  // [P0 REGRESSION — 2026-10-17 稽核] 舊版用 `history.length` 當回合數，而
  // 人工核准/交涉紀錄也會進 history，於是畫面出現「Round 6 / 5」。現在改用
  // 引擎自己的 current_round（真實對手輪次），超過上限時明確標示。
  const curRound = tfNum(session.current_round, null);
  if (curRound === null) {
    el("decisionRound").textContent = "—";
  } else if (maxRounds !== null && curRound > maxRounds) {
    const roundWord = curRound === 1 ? "round" : "rounds";
    el("decisionRound").textContent = `${curRound} ${roundWord} (limit ${maxRounds})`;
  } else {
    el("decisionRound").textContent = `Round ${curRound} / ${maxRounds === null ? "—" : maxRounds}`;
  }

  // 供應商意圖：歷史輪次不持久化 intent，改顯示最後一則對手原文（截斷）
  const lastOpp = [...turns].reverse().find((t) => t.sender === "opponent");
  if (lastOpp && lastOpp.raw_text) {
    const txt = String(lastOpp.raw_text);
    el("decisionIntent").textContent = txt;
    el("decisionIntent").title = txt;
  } else {
    el("decisionIntent").textContent = "—";
  }

  // 建議還價：優先顯示引擎最近一次建議（copilot 模式持久化在對手輪的
  // metadata.recommended_offer_price），其次才是已核准/已送出的最後報價。
  // 舊版只讀 latest_our_offer，在 copilot 模式下永遠是 null → "—"，評審在
  // 決策面板上看不到引擎到底建議多少。缺值一律 "—"，絕不填 0。
  let recommended = null;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const meta = turns[i].metadata || {};
    const v = tfNum(meta.recommended_offer_price, null);
    if (v !== null) { recommended = v; break; }
  }
  const shownOffer = recommended !== null ? recommended : tfNum(session.latest_our_offer, null);
  el("decisionCounter").textContent = shownOffer === null ? "—" : tfAmount(shownOffer, "CNY");
  const unitNote = document.getElementById("decisionUnit");
  if (unitNote) {
    const quote = quoteOnSession(session);
    if (quote && quote.currency && quote.currency !== "CNY") {
      const asked = quote.amount === null ? quote.currency : tfAmount(quote.amount, quote.currency);
      unitNote.textContent = quote.note
        ? `Supplier asked ${asked}. ${quote.note}`
        : `Supplier asked ${asked}. The counter above is CNY.`;
    } else {
      unitNote.textContent = "Prices in this panel are CNY, the mandate currency.";
    }
  }

  // 破局風險：取最近一輪**有量測值**的 p_break。人工核准輪沿用上一輪的實測值，
  // 但舊資料可能有 p_break=null；一律往回找到第一個真實值，找不到就 "—"。
  let risk = null;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const v = tfNum(turns[i].p_break, null);
    if (v !== null) { risk = v; break; }
  }
  el("decisionRisk").textContent = risk === null ? "—" : `${(risk * 100).toFixed(1)}%`;
  tfTone(
    el("decisionRisk"),
    risk === null || tfTrip() === null ? "" : (risk >= tfTrip() ? "is-bad" : "is-ok")
  );

  // 最新生成話術：先找最後一則我方（AI 或人工核准）的發言；copilot 模式不會
  // 產生我方輪次，話術存在對手輪 metadata.generated_reply（後端持久化），
  // 否則重新整理頁面後這段「要人工貼回去」的話就消失成 "—"。
  const lastOurs = [...turns].reverse().find(
    (t) => t.sender === "buyer_agent" || t.sender === "buyer_human_approved"
  );
  let replyText = lastOurs && lastOurs.raw_text ? String(lastOurs.raw_text) : null;
  if (!replyText) {
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const meta = turns[i].metadata || {};
      if (typeof meta.generated_reply === "string" && meta.generated_reply.trim()) {
        replyText = meta.generated_reply;
        break;
      }
    }
  }
  el("decisionReply").textContent = replyText || "—";
  paintCustomsReference((session.spec || {}).market_context);
}

// The draft is meant to be pasted into the supplier chat by a human. Until now
// the dashboard only displayed it, so the operator had to select text by hand.
async function decide(action) {
  const result = document.getElementById("decisionResult");
  const sid = currentSessionId || ((document.getElementById("cmdSessionId") || {}).value || "");
  if (!sid) {
    if (result) result.textContent = "Open a sandbox deal first. Nothing was recorded.";
    return;
  }
  const rationale = action === "hold"
    ? "Hold firm at the engine price."
    : action === "reject"
      ? "Buyer rejected the offer."
      : "Buyer approved the engine price.";
  if (result) result.textContent = "Recording your decision…";
  try {
    const res = await fetch(`/api/v1/sessions/${encodeURIComponent(sid)}/approve`, {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify({ action: action, tactical_rationale: rationale }),
    });
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    if (!res.ok) {
      const detail = (body && (body.detail || body.reason)) || "The desk could not record that decision.";
      if (result) result.textContent = typeof detail === "string" ? detail : "Try again after the supplier replies.";
      return;
    }
    if (action === "reject") {
      if (result) result.textContent = "Rejected. The deal is closed on our side. Nothing was sent to the supplier.";
    } else if (action === "hold") {
      if (result) result.textContent = (body && body.message) || "Held the engine price. The deal is still waiting for you to approve or reject. Nothing was sent.";
    } else if (result) {
      result.textContent = "Approved the engine price. Paste the draft yourself. Nothing was sent automatically.";
    }
    await fetchSessionDetails(sid);
  } catch (err) {
    if (result) result.textContent = err.message || "The desk could not record that decision.";
  }
}

function bindDeskChrome() {
  const themeBtn = document.getElementById("themeToggle");
  const langBtn = document.getElementById("langToggle");
  const apply = () => {
    const root = document.getElementById("marketIntel");
    if (themeBtn) themeBtn.textContent = document.documentElement.dataset.theme === "dark" ? "Light" : "Dark";
    if (langBtn) langBtn.textContent = document.documentElement.lang === "zh-Hant" ? "EN" : "繁";
    if (root && window.TradeFlowMarket) window.TradeFlowMarket.paint(root, root._last || null);
  };
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("tfMarketTheme", next); } catch (err) { /* ignore */ }
      apply();
    });
  }
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      const next = document.documentElement.lang === "zh-Hant" ? "en" : "zh-Hant";
      document.documentElement.lang = next;
      const root = document.getElementById("marketIntel");
      if (root) root.dataset.lang = next;
      try { localStorage.setItem("tfMarketLang", next); } catch (err) { /* ignore */ }
      apply();
    });
  }
  apply();
}

function copyDraftReply() {
  const btn = document.getElementById("copyReplyBtn");
  const node = document.getElementById("decisionReply");
  const text = node ? node.textContent.trim() : "";
  const say = (label) => {
    if (!btn) return;
    const original = "Copy draft";
    btn.textContent = label;
    setTimeout(() => { btn.textContent = original; }, 1800);
  };

  if (!text || text === "—") {
    say("Nothing to copy");
    return;
  }
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    say("Select the text and copy");
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => say("Copied"),
    () => say("Clipboard blocked")
  );
}

function updateBeliefChartWithTurns(session) {
  if (!beliefChart) return;
  const labels = ["Prior"];
  const target = session.spec ? tfNum(session.spec.target_price, null) : null;
  // [P0 REGRESSION — 2026-10-17 稽查] 舊版先驗點硬編成 `1.08 * target`，即使
  // 會話的 prior_reserve_mean 不是 1.08（例如 1.20 的 CNC/電池情境）也照畫；
  // 這是拿預設常數冒充該會話的先驗。改讀 spec.prior_reserve_mean。
  const priorMean = session.spec ? tfNum(session.spec.prior_reserve_mean, null) : null;
  const postData = [priorMean !== null && target !== null ? priorMean * target : null];
  // [修正] 舊版把 Prior 的 Breakdown Risk 硬編成 5.0%，但後端從未提供任何
  // prior 破裂風險 —— 這是捏造數字。改為 null（未知），由後端/第一輪資料帶入。
  const riskData = [null];

  const turns = session.history || session.turns || [];
  turns.forEach((t, idx) => {
    labels.push(`R${t.round_index}`);
    // 缺值一律 null（圖上斷點）；不得以 0 冒充「零風險/零後驗」。
    const pm = tfNum(t.posterior_mean, null);
    postData.push(pm !== null && target !== null ? pm * target : null);
    const pb = tfNum(t.p_break, null);
    riskData.push(pb === null ? null : pb * 100);
  });

  beliefChart.data.labels = labels;
  beliefChart.data.datasets[0].data = postData;
  beliefChart.data.datasets[1].data = riskData;
  beliefChart.update();
  setBeliefChartEmpty(turns.length === 0);
}

// An empty Chart.js canvas renders a 0–1.0 axis, which looks like a broken
// measurement. Cover it with a sentence until there is something to plot.
function setBeliefChartEmpty(isEmpty) {
  const empty = document.getElementById("beliefEmpty");
  const canvas = document.getElementById("beliefChart");
  if (empty) empty.hidden = !isEmpty;
  if (canvas) canvas.style.visibility = isEmpty ? "hidden" : "visible";
}

// Sandbox commodities. The catalogue arrives from /api/v1/simulation/mandates.
// The selector only previews a mandate. Open sandbox deal POSTs /api/v1/sessions.
function paintRiskBands() {
  const trip = tfTrip();
  const caution = tfCaution();
  const safeEl = document.getElementById("riskBandSafe");
  const cautionEl = document.getElementById("riskBandCaution");
  const tripEl = document.getElementById("riskBandTrip");
  const note = document.getElementById("riskFineprint");
  if (trip === null || caution === null) return;
  const pct = (n) => `${Math.round(n * 100)}%`;
  if (safeEl) safeEl.textContent = `safe < ${pct(caution)}`;
  if (cautionEl) cautionEl.textContent = `caution ${pct(caution)}–${pct(trip)}`;
  if (tripEl) tripEl.textContent = `trip ≥ ${pct(trip)}`;
  if (note) {
    note.textContent = `The marker sits at the measured probability. At ${pct(trip)} the engine stops proposing and waits for you.`;
  }
}

function paintMandate(p) {
  document.getElementById("mandatePrice").textContent = `${tfMoney(p.target)} / ${tfMoney(p.ceiling)}`;
  document.getElementById("mandateLead").textContent = p.lead;
  document.getElementById("mandateDeposit").textContent = p.deposit;
  document.getElementById("mandateTooling").textContent = p.tooling;
}

async function loadDeskConfig() {
  try {
    const [heuristicsRes, mandatesRes] = await Promise.all([
      fetch("/api/v1/heuristics", { headers: tfAuthHeaders() }),
      fetch("/api/v1/simulation/mandates", { headers: tfAuthHeaders() }),
    ]);
    if (heuristicsRes.ok) tfHeuristics = await heuristicsRes.json();
    if (mandatesRes.ok) {
      const body = await mandatesRes.json();
      categoryPresets = {};
      for (const item of body.mandates || []) {
        categoryPresets[item.key] = {
          name: item.name,
          target: item.target_price,
          ceiling: item.ceiling_price,
          lead: item.lead_label,
          deposit: item.deposit_label,
          tooling: item.tooling_label,
          moq: item.moq,
        };
      }
    }
  } catch (err) {
    console.warn("[TradeFlow] desk config failed:", err);
  }
  paintRiskBands();
  const select = document.getElementById("presetCategorySelect");
  const statusEl = document.getElementById("presetStatus");
  if (select && categoryPresets && categoryPresets[select.value]) {
    paintMandate(categoryPresets[select.value]);
    if (statusEl) {
      statusEl.textContent = "Preview only. No deal was opened. Use Open sandbox deal when you want the engine to use this mandate.";
    }
  }
}

function previewMandate(cat) {
  const p = categoryPresets && categoryPresets[cat];
  const statusEl = document.getElementById("presetStatus");
  if (!p) {
    if (statusEl) {
      statusEl.textContent = "Simulated mandates have not loaded. Refresh the desk.";
      tfTone(statusEl, "is-bad");
    }
    return;
  }
  paintMandate(p);
  if (statusEl) {
    statusEl.textContent = "Preview only. No deal was opened. Use Open sandbox deal when you want the engine to use this mandate.";
    tfTone(statusEl, "");
  }
}

async function openSandboxDeal() {
  const select = document.getElementById("presetCategorySelect");
  return applyCategoryPreset(select ? select.value : "");
}

async function applyCategoryPreset(cat) {
  const p = categoryPresets && categoryPresets[cat];
  const statusEl = document.getElementById("presetStatus");
  if (!p) {
    if (statusEl) {
      statusEl.textContent = "Simulated mandates have not loaded. Refresh the desk.";
      tfTone(statusEl, "is-bad");
    }
    return;
  }

  paintMandate(p);

  // Create (or reuse) a real session so the engine actually operates under this
  // mandate instead of the display merely changing. Called only from Open sandbox deal.
  const newSid = `DEMO_${cat.toUpperCase()}_01`;
  const body = {
    session_id: newSid,
    product_name: p.name,
    target_price: p.target,
    ceiling_price: p.ceiling,
    moq: p.moq,
    max_rounds: 5
  };

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
        statusEl.textContent = `Sandbox session ${newSid} created. The engine is bound to this simulated mandate, not a factory quote.`;
        tfTone(statusEl, "is-ok");
      }
      clearStaleDecisionPrompt();
      fetchSessionDetails(newSid);
    } else {
      const detail = await res.text();
      if (statusEl) {
        statusEl.textContent = `Could not create session (HTTP ${res.status}): ${detail.slice(0, 140)}`;
        tfTone(statusEl, "is-bad");
      }
    }
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = `Session creation failed: ${err.message || "request failed"}`;
      tfTone(statusEl, "is-bad");
    }
  }

  setBeliefChartEmpty(true);
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

function humanCommandResult(reason) {
  const text = String(reason || "");
  if (text.indexOf("waiting for a person") !== -1 || text.indexOf("AWAITING_HUMAN_DECISION") !== -1) {
    return "This deal is waiting for you. Use Approve, Hold firm, or Reject. The remote command was not applied.";
  }
  if (!text) return "The command was not applied.";
  return text;
}

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
      resEl.className = "cmd-result is-bad";
      tfTone(resEl, "is-bad");
      resEl.textContent = `[HTTP ${resp.status}] Command NOT applied: ${detail || resp.statusText || "request rejected"}`;
      return;
    }
    if (result && result.status === "success") {
      resEl.className = "cmd-result is-ok";
      tfTone(resEl, "is-ok");
      resEl.textContent = `[SUCCESS] Resolved Action: ${result.action} | Session state updated.`;
    } else {
      // HTTP 200 but the backend did not apply the command (e.g. unknown
      // session with no pending authorization bridge). Do not claim success.
      const reason = (result && (result.detail || result.reason)) || "no action applied";
      resEl.className = "cmd-result is-bad";
      tfTone(resEl, "is-bad");
      resEl.textContent = humanCommandResult(reason);
    }
  } catch (err) {
    resEl.className = "cmd-result is-bad";
    tfTone(resEl, "is-bad");
    resEl.textContent = `[NETWORK ERROR] ${err.message || "request failed"}`;
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
    const select = document.getElementById("presetCategorySelect");
    const preset = select && categoryPresets ? categoryPresets[select.value] : null;
    const auctionBody = {};
    if (preset && preset.name) auctionBody.product_name = preset.name;
    const res = await fetch("/api/v1/multi-auction/run", {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify(auctionBody)
    });
    if (!res.ok) {
      loader.classList.add("hidden");
      cardsWrap.innerHTML = `<div class="empty-state is-error">Multi-auction rejected (HTTP ${tfEsc(res.status)}).</div>`;
      return;
    }
    const data = await res.json();
    loader.classList.add("hidden");

    if (!data.success) {
      cardsWrap.innerHTML = `<div class="empty-state is-error">Error running multi-auction: ${tfEsc(data.detail || "Unknown")}</div>`;
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
    const walkAway = tfNum(data.walk_away_ceiling, null);
    suppliers.forEach((sName, i) => {
      const quote = lastRound.quotes[i];
      const tactic = lastRound.tactics[i];
      const ceiling = lastRound.effective_ceilings_cny[i];
      const pBreakNum = tfNum(lastRound.p_break_array[i], null);
      const pBreak = pBreakNum === null ? "—" : (pBreakNum * 100).toFixed(1);
      const quoteNum = tfNum(quote, null);
      const ceilingNum = tfNum(ceiling, null);
      const scale = walkAway !== null && walkAway > 0 ? walkAway : (quoteNum === null ? 1 : Math.max(quoteNum, 1));
      const quotePct = Math.max(0, Math.min(100, (Number(quoteNum) / scale) * 100));

      let tagClass = "tactic-anchor";
      let plainTactic = "Hold the line";
      if (tactic === "LEAN_IN") {
        tagClass = "tactic-lean";
        plainTactic = "Press for a better price";
      } else if (tactic === "SIDELINE") {
        tagClass = "tactic-sideline";
        plainTactic = "Step back";
      }

      const card = document.createElement("div");
      card.className = "supplier-card";
      if (sName === data.winning_supplier) card.classList.add("is-winner");
      const quoteLabel = tfAmount(quoteNum, "CNY");
      card.innerHTML = `
        <div class="card-top">
          <span class="supplier-title">${tfEsc(sName)}</span>
          <span class="tactic-tag ${tfEsc(tagClass)}">${tfEsc(plainTactic)}</span>
        </div>
        <div class="card-metrics">
          <div>Quote: <span class="metric-val">${tfEsc(quoteLabel)}</span></div>
          <div>Best alternative: <span class="metric-val">${tfEsc(tfAmount(ceilingNum, "CNY"))}</span></div>
          <div>Risk: <span class="metric-val">${pBreak === "—" ? "—" : tfEsc(pBreak) + "%"}</span></div>
        </div>
        <div class="meter-row">
          <div class="quote-meter" role="img" aria-label="Quote is ${quotePct.toFixed(0)} percent of the walk-away ceiling">
            <span class="quote-meter-fill" style="width: ${quotePct.toFixed(1)}%"></span>
          </div>
          <span class="quote-meter-num">${tfEsc(quoteLabel)}</span>
        </div>
      `;
      cardsWrap.appendChild(card);
    });

    const versusTarget = tfNum(data.savings_vs_target_cny, null);
    const pieceCount = tfNum(data.moq, null);
    const pieceBasis = pieceCount === null ? "" : ` for ${tfAmount(pieceCount, "")} pcs`;
    summaryEl.innerHTML = `
      <p class="summary-lead"><strong>Simulated suppliers</strong> These are scripted archetypes, not live factory quotes. Winner: ${tfEsc(data.winning_supplier)} at ${tfEsc(tfAmount(data.winning_price, "CNY"))}. Amounts on this card are CNY.</p>
      <p class="summary-headline"><strong>Savings versus the first-round best quote</strong> (${tfEsc(tfAmount(data.first_round_best_quote, "CNY"))}): ${tfEsc(tfAmount(data.total_savings_cny, "CNY"))}${tfEsc(pieceBasis)} (${tfEsc(tfPct(data.total_savings_pct))})</p>
      <p class="${versusTarget !== null && versusTarget < 0 ? "is-negative" : ""}"><strong>Versus your target</strong> (${tfEsc(tfAmount(data.product_target, "CNY"))}): ${tfEsc(tfAmount(data.savings_vs_target_cny, "CNY"))}${tfEsc(pieceBasis)} (${tfEsc(tfPct(data.savings_vs_target_pct))})</p>
      <p class="summary-secondary"><strong>Versus the walk-away ceiling</strong> (${tfEsc(tfAmount(data.walk_away_ceiling, "CNY"))}): ${tfEsc(tfAmount(data.savings_vs_ceiling_cny, "CNY"))}${tfEsc(pieceBasis)} (${tfEsc(tfPct(data.savings_vs_ceiling_pct))})</p>
      <p class="summary-secondary">Best alternative is the cheapest adjusted offer from the other simulated suppliers, capped by your walk-away ceiling. It can sit above a supplier's own quote when that supplier is the cheaper one. Suppliers who share the same cheapest rival show the same figure. It is not that supplier's private reserve.</p>
      <details class="dev-details">
        <summary>Engine notes</summary>
        <p>Search size ${tfEsc(String(tfNum(data.total_rollouts, 0)))} parameter cells. Compute ${tfEsc(data.total_compute_seconds)}s (${tfEsc(data.backend)}). Engine tactic codes stay in the solver; the cards use plain words.</p>
      </details>
    `;
    const customsNote = document.createElement("p");
    customsNote.className = "summary-secondary";
    customsNote.textContent = customsReferenceLine(data.market_context);
    summaryEl.appendChild(customsNote);
    summaryEl.classList.remove("hidden");

  } catch (err) {
    loader.classList.add("hidden");
    cardsWrap.innerHTML = `<div class="empty-state is-error">Request failed: ${tfEsc(err.message)}</div>`;
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
      resEl.className = "cmd-result is-bad";
      tfTone(resEl, "is-bad");
      resEl.textContent = `[SAVE FAILED] HTTP ${res.status}: ${res.statusText || "request rejected"}`;
      resEl.classList.remove("hidden");
      return;
    }
    const d = await res.json();
    resEl.className = "cmd-result is-ok";
    tfTone(resEl, "is-ok");
    const updated = Array.isArray(d.fields_updated) ? d.fields_updated.join(", ") : "";
    resEl.textContent = `[SAVED] Hot reload status: ${d.hot_reload}. Updated: ${updated || "(no allowlisted fields changed)"}`;
    resEl.classList.remove("hidden");
  } catch (err) {
    resEl.className = "cmd-result is-bad";
    tfTone(resEl, "is-bad");
    resEl.textContent = `[SAVE ERROR] ${err.message || "request failed"}`;
    resEl.classList.remove("hidden");
  }
}

// ---------------------------------------------------------------------------
// Supplier quote: paste / sample / AMD live reading
// The round endpoint decides the price. /quotes/read only words and extracts.
// ---------------------------------------------------------------------------
let quoteImagePayload = "";

function paintAmdAvailability() {
  const hint = document.getElementById("amdLiveHint");
  if (!hint) return;
  fetch("/health")
    .then((res) => (res.ok ? res.json() : null))
    .then((body) => {
      const ver = document.getElementById("feedVer");
      if (ver) ver.textContent = body && body.version ? `API ${body.version}` : "—";
      if (!body || !body.llm) return;
      const model = body.llm.default_model || body.llm.model || "the configured model";
      if (body.llm.amd_live_available) {
        hint.textContent = `AMD live mode can call ${model}. It stays off until you turn it on. The solver still sets every price.`;
      } else {
        hint.textContent = `Live reading is off on this server. Turning the switch on still uses the offline reader until a live model key is configured. The solver still sets every price.`;
      }
    })
    .catch(() => {});
}

async function loadSampleQuote(name) {
  const note = document.getElementById("quoteSampleNote");
  const paste = document.getElementById("quotePaste");
  const img = document.getElementById("quoteSampleImg");
  quoteImagePayload = "";
  try {
    const res = await fetch(`/static/samples/${name}.txt`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    paste.value = await res.text();
    img.src = `/static/samples/${name}.png`;
    img.hidden = false;
    img.alt = "Labeled simulation, not a real supplier screenshot";
    if (note) {
      note.textContent = "Labeled simulation. The picture was generated in this repo. The reader is given this transcript, not a pixel OCR claim.";
    }
  } catch (err) {
    if (note) note.textContent = `Could not load the sample: ${err.message || "request failed"}`;
  }
}

function onQuoteFile(input) {
  const file = input.files && input.files[0];
  const note = document.getElementById("quoteSampleNote");
  const img = document.getElementById("quoteSampleImg");
  quoteImagePayload = "";
  if (!file) return;
  if (note) {
    note.textContent = "Picture received. Offline mode cannot read pixels, so paste the supplier's words or open Sample 1, 2, or 3.";
  }
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = String(reader.result || "");
    quoteImagePayload = dataUrl;
    if (img && dataUrl.indexOf("data:") === 0) {
      img.src = dataUrl;
      img.hidden = false;
      img.alt = "Uploaded supplier image";
    }
  };
  reader.readAsDataURL(file);
}

function paintQuoteReading(payload) {
  const callout = document.getElementById("amdCallout");
  const fields = document.getElementById("quoteFields");
  const accuracy = document.getElementById("quoteAccuracy");
  if (callout) {
    callout.hidden = false;
    const status = payload && payload.status;
    let text = (payload && payload.display) || "No reading returned.";
    const liveLine = typeof text === "string" && text.indexOf("AMD GPU Cloud ·") === 0;
    const liveOk = status === "ok" && liveLine;
    if (status === "ok" && !liveLine) {
      text = "AMD reading was not applied. The response was not a confirmed live call.";
    } else if (status !== "ok" && liveLine) {
      text = "AMD live call failed. The offline fallback is shown instead.";
    }
    // The health line already says live reading is off, and the sample note
    // already says the pictures are simulations. Don't repeat either here.
    if (typeof text === "string" && text.indexOf("Offline fallback") === 0 && text.indexOf("Live reading is off") !== -1) {
      text = "Offline fallback. These fields are the supplier's numbers, not our price.";
    } else if (typeof text === "string" && text.indexOf("labeled simulations") !== -1) {
      text = "Offline mode cannot read a picture by itself. Paste the supplier's words, or open Sample 1, 2, or 3.";
    }
    callout.textContent = text;
    tfTone(callout, liveOk ? "is-ok" : "");
  }
  if (fields && payload && payload.fields) {
    const f = payload.fields;
    const quoted = tfNum(f.quoted_amount, null);
    const currency = f.currency ? String(f.currency) : "";
    const price = quoted === null ? "missing" : tfAmount(quoted, currency);
    const moq = f.moq === null || f.moq === undefined ? "missing" : String(f.moq);
    const lead = f.lead_time_days === null || f.lead_time_days === undefined
      ? "Lead time —"
      : `Lead time ${f.lead_time_days} days`;
    const depositRaw = f.deposit_pct;
    let deposit = "Deposit —";
    const depositNum = Number(depositRaw);
    if (depositRaw !== null && depositRaw !== undefined && depositRaw !== "" && Number.isFinite(depositNum)) {
      const depositPct = depositNum <= 1 ? Math.round(depositNum * 100) : Math.round(depositNum);
      deposit = `${depositPct}% deposit`;
    }
    fields.textContent = `Extracted supplier fields (not our price): price ${price}, MOQ ${moq}, ${lead}, ${deposit}.`;
    const about = document.getElementById("quoteAbout");
    const note = document.getElementById("quoteCurrencyNote");
    if (about && note) {
      const currencyNote = f.currency_note ? String(f.currency_note) : "";
      note.textContent = currencyNote;
      about.hidden = !currencyNote;
    }
  }
  if (accuracy) {
    const reading = payload && payload.reading ? ` Model wording: ${payload.reading}` : "";
    let friendly = "The offline reader filled these supplier fields. It does not set our price.";
    if (payload && payload.status === "ok") {
      friendly = "Live reading returned these supplier fields. It does not set our price.";
    } else if (payload && payload.status === "needs_text") {
      friendly = "No transcript was read. Use a sample image or paste the chat.";
    }
    accuracy.textContent = `${friendly}${reading}`;
  }
}

async function readSupplierQuote() {
  const paste = document.getElementById("quotePaste");
  const callout = document.getElementById("amdCallout");
  const live = !!(document.getElementById("amdLiveToggle") && document.getElementById("amdLiveToggle").checked);
  const text = paste ? paste.value : "";
  const sid = currentSessionId || (document.getElementById("cmdSessionId") || {}).value || "";
  if (callout) {
    callout.hidden = false;
    callout.textContent = live ? "Asking AMD GPU Cloud…" : "Reading with the offline extractor…";
    tfTone(callout, "");
  }
  const readBody = { text: text, amd_live: live };
  if (!text.trim() && quoteImagePayload) readBody.image_base64 = quoteImagePayload;
  const readPromise = fetch("/api/v1/quotes/read", {
    method: "POST",
    headers: tfJsonHeaders(),
    body: JSON.stringify(readBody),
  });
  let roundOk = false;
  if (sid && text.trim()) {
    try {
      const roundRes = await fetch(`/api/v1/sessions/${encodeURIComponent(sid)}/round`, {
        method: "POST",
        headers: tfJsonHeaders(),
        body: JSON.stringify({
          opponent_raw_text: text,
          mode: "copilot",
          amd_live: live,
        }),
      });
      roundOk = roundRes.ok;
      if (roundOk) await fetchSessionDetails(sid);
      else if (callout) {
        callout.textContent = `The solver round was rejected (HTTP ${roundRes.status}). The AMD reading is separate and still cannot set the price.`;
      }
    } catch (err) {
      if (callout) callout.textContent = `Solver round failed: ${err.message || "request failed"}`;
    }
  }
  try {
    const res = await readPromise;
    let payload = null;
    try { payload = await res.json(); } catch (_) { payload = null; }
    if (!res.ok) {
      const detail = (payload && payload.detail) || res.statusText || "request rejected";
      if (callout) callout.textContent = `AMD reading was not applied (HTTP ${res.status}): ${detail}`;
      return;
    }
    paintQuoteReading(payload);
    if (!roundOk && callout && !text.trim()) {
      // keep the reading display; there was no solver round
    }
  } catch (err) {
    if (callout) callout.textContent = `AMD reading failed: ${err.message || "request failed"}`;
  }
}

async function testPush(ch) {
  const resEl = document.getElementById("configResult");
  resEl.className = "cmd-result";
  tfTone(resEl, "");
  resEl.textContent = `Sending test message to channel '${ch}'...`;
  resEl.classList.remove("hidden");

  try {
    const res = await fetch("/api/v1/telemetry/test-push", {
      method: "POST",
      headers: tfJsonHeaders(),
      body: JSON.stringify({ channel: ch })
    });
    if (!res.ok) {
      resEl.className = "cmd-result is-bad";
      tfTone(resEl, "is-bad");
      resEl.textContent = `[TEST FAILED] HTTP ${res.status}: ${res.statusText || "request rejected"}`;
      return;
    }
    const d = await res.json();
    let shown = "";
    try {
      shown = JSON.stringify(d.results);
    } catch (_) {
      shown = "unreadable result";
    }
    if (shown.indexOf("Traceback") !== -1) {
      shown = "A channel reported an internal error. The server log has the detail.";
    }
    resEl.textContent = `[RESULT] ${shown}`;
  } catch (err) {
    resEl.className = "cmd-result is-bad";
    tfTone(resEl, "is-bad");
    resEl.textContent = `[TEST ERROR] ${err.message || "request failed"}`;
  }
}
