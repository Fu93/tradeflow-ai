/**
 * TradeFlow Copilot — side panel.
 *
 * The panel reads engine and session fields. It does not invent prices, savings,
 * or an AMD reading. Missing numbers render as "--". Commands 1 / 2 / 3 are the
 * human decision controls; this page never fills or sends a chat message.
 */

const API_BASE_URL = "http://localhost:8000";
let tfHeuristics = null;

function tfTrip() {
  const n = tfHeuristics && Number(tfHeuristics.breakdown_trip_threshold);
  return Number.isFinite(n) ? n : null;
}

async function loadHeuristics() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/heuristics`, { headers: tfAuthHeaders() });
    if (res.ok) tfHeuristics = await res.json();
  } catch (err) {
    tfHeuristics = null;
  }
  const note = document.getElementById("riskFineprint");
  const trip = tfTrip();
  if (note && trip !== null) {
    note.textContent = `At ${Math.round(trip * 100)}% the engine stops proposing and waits for you. The bar shows the measured probability, not a target.`;
  }
}

// ---------------------------------------------------------------------------
// Security helpers (H3): outbound auth + HTML escaping.
// ---------------------------------------------------------------------------
const TF_API_KEY = localStorage.getItem("tfApiKey") || "";

function tfAuthHeaders() {
  const headers = {};
  if (TF_API_KEY) headers["X-API-Key"] = TF_API_KEY;
  return headers;
}

function tfEsc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Missing values return the fallback. 0 is a real measurement.
function tfNum(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function tfMoney(value) {
  const n = tfNum(value, null);
  return n === null ? "--" : `¥${n.toFixed(2)}`;
}

function tfStateName(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  if (typeof value === "object" && value.value) return String(value.value);
  return String(value);
}

const MODE_LABELS = {
  LEAN_IN: "Lean in",
  FIRM_HOLD: "Hold firm",
  WALK_AWAY: "Walk away",
  LOGROLL: "Terms swap",
  CROSS_ANCHOR: "Cross anchor",
  SIDELINE: "Sideline",
};

const SAVINGS_FIELDS = [
  ["total_savings_cny", "total_savings_baseline"],
  ["savings_vs_first_quote_cny", "savings_vs_first_quote_pct"],
  ["savings_vs_target_cny", "savings_vs_target_pct"],
  ["savings_vs_ceiling_cny", "savings_vs_ceiling_pct"],
  ["total_savings_pct", "total_savings_baseline"],
  ["savings_pct", "savings_pct_baseline"],
];

// DOM
const statusBadge = document.getElementById("status-badge");
const statusText = document.getElementById("status-text");
const sourceBadge = document.getElementById("source-badge");
const sourceBadgeText = document.getElementById("source-badge-text");
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
const btnRetry = document.getElementById("btn-retry");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");
const loadingSkeleton = document.getElementById("loading-skeleton");
const teachingEmpty = document.getElementById("teaching-empty");
const approvalState = document.getElementById("approval-state");
const approvalAction = document.getElementById("approval-action");
const approvalPrice = document.getElementById("approval-price");
const approvalEvidence = document.getElementById("approval-evidence");
const approvalSuperseded = document.getElementById("approval-superseded");
const priceRange = document.getElementById("price-range");
const rangeEngine = document.getElementById("range-engine");
const rangeSupplier = document.getElementById("range-supplier");
const rangeTarget = document.getElementById("range-target");
const rangeReserve = document.getElementById("range-reserve");
const valSavings = document.getElementById("val-savings");
const savingsBasis = document.getElementById("savings-basis");

// State
let currentTabId = null;
let currentSessionId = null;
let sidepanelPort = null;
let hasRenderedLiveData = false;
let tfHealth = null;
let tfHealthError = null;
let tabMeta = { url: "", mode: "", platform: "" };
let lastRoundPayload = null;
let gateMemory = { key: "", decision: "" };
let sourceForced = null;

function tfTone(el, tone) {
  if (!el) return;
  el.classList.remove("tone-ok", "tone-warn", "tone-bad", "tone-info", "tone-muted");
  if (tone) el.classList.add(tone);
}

function showSkeleton() {
  if (!loadingSkeleton) return;
  loadingSkeleton.hidden = false;
  loadingSkeleton.setAttribute("aria-hidden", "false");
  const deck = document.getElementById("deck");
  if (deck) deck.setAttribute("aria-busy", "true");
}

function hideSkeleton() {
  if (!loadingSkeleton) return;
  loadingSkeleton.hidden = true;
  loadingSkeleton.setAttribute("aria-hidden", "true");
  const deck = document.getElementById("deck");
  if (deck) deck.removeAttribute("aria-busy");
}

function showTeaching() {
  if (teachingEmpty) teachingEmpty.hidden = false;
}

function hideTeaching() {
  if (teachingEmpty) teachingEmpty.hidden = true;
}

function showError(message) {
  if (errorText) errorText.textContent = message || "The TradeFlow backend is unreachable.";
  if (errorBanner) errorBanner.hidden = false;
  sourceForced = "unreachable";
  paintSourceBadge();
}

function hideError() {
  if (errorBanner) errorBanner.hidden = true;
  sourceForced = null;
}

function tfStatusPill(state) {
  const name = tfStateName(state);
  if (statusText) statusText.textContent = name ? name.replace(/_/g, " ") : "--";
  if (!statusBadge) return;
  statusBadge.className = "status-pill";
  if (name === "CIRCUIT_TRIPPED_ESCALATED" || name === "WALK_AWAY") {
    statusBadge.classList.add("is-trip", "tripped");
  } else if (name === "DEAL_CLOSED" || name === "HUMAN_APPROVED_APPLIED") {
    statusBadge.classList.add("is-done");
  } else if (name === "AWAITING_HUMAN_DECISION" || name === "AWAITING_REMOTE_AUTH") {
    statusBadge.classList.add("is-wait");
  } else if (name === "STANDBY" || name === "RADAR LISTENING" || !name) {
    statusBadge.classList.add("is-idle");
  } else {
    statusBadge.classList.add("is-live");
  }
}

function tfReadingStatus(round) {
  if (!round || typeof round !== "object") return "";
  if (typeof round.quote_status === "string") return round.quote_status;
  if (round.quote_read && typeof round.quote_read.status === "string") return round.quote_read.status;
  if (round.amd_reading && typeof round.amd_reading.status === "string") return round.amd_reading.status;
  return "";
}

function tfIsSimulator(round) {
  const url = String(tabMeta.url || "");
  const mode = String(tabMeta.mode || "");
  const channel = String(
    (round && round.session && round.session.spec && round.session.spec.supplier_channel) || ""
  );
  if (round && round.simulated_sample === true) return true;
  if (/mock_chat\.html/i.test(url)) return true;
  if (mode === "autopilot_simulator" || mode === "simulator") return true;
  if (/simulat/i.test(channel)) return true;
  return false;
}

function tfSourceKind() {
  if (sourceForced === "unreachable" || tfHealthError) return "unreachable";
  if (!tfHealth) return "unknown";
  const round = lastRoundPayload || {};
  const amd = round.amd_live || {};
  const reading = tfReadingStatus(round);
  const failed = reading === "offline_fallback" || reading === "timeout" || reading === "failed";
  const available = !!(tfHealth.llm && tfHealth.llm.amd_live_available === true);
  const quote = round.quote_read && typeof round.quote_read === "object" ? round.quote_read : null;
  const display = typeof (quote && quote.display) === "string"
    ? quote.display
    : (typeof round.display === "string" ? round.display : "");
  const liveLine = display.indexOf("AMD GPU Cloud") === 0;
  const liveRequested = amd.requested === true;
  if (amd.decides_price === true) return tfIsSimulator(round) ? "simulator" : "offline";
  if ((liveRequested && available && !failed) || (reading === "ok" && liveLine)) return "amd-live";
  if (tfIsSimulator(round)) return "simulator";
  return "offline";
}

const SOURCE_LABELS = {
  "amd-live": "AMD live",
  offline: "Offline fallback",
  simulator: "Simulator",
  unreachable: "Unreachable",
  unknown: "--",
};

function paintSourceBadge() {
  if (!sourceBadge || !sourceBadgeText) return;
  const kind = tfSourceKind();
  sourceBadge.className = "source-badge";
  if (kind === "amd-live") sourceBadge.classList.add("is-amd");
  else if (kind === "offline") sourceBadge.classList.add("is-offline");
  else if (kind === "simulator") sourceBadge.classList.add("is-sim");
  else if (kind === "unreachable") sourceBadge.classList.add("is-down");
  else sourceBadge.classList.add("is-unknown");
  sourceBadgeText.textContent = SOURCE_LABELS[kind] || "--";
}

async function loadHealth() {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, { headers: tfAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    tfHealth = await res.json();
    tfHealthError = null;
  } catch (err) {
    tfHealth = null;
    tfHealthError = err;
  }
  paintSourceBadge();
}

function tfRoundKey(data, session) {
  const ao = data && data.action_object;
  if (ao && ao.source_round_id) return String(ao.source_round_id);
  if (session && (session.version != null || session.current_round != null)) {
    return `r${session.current_round ?? ""}-v${session.version ?? ""}`;
  }
  return "";
}

function tfHistoryDecision(session) {
  const turns = session && Array.isArray(session.history) ? session.history : [];
  let decision = null;
  let opponentAfter = false;
  for (const turn of turns) {
    const action = turn && turn.metadata && turn.metadata.action;
    if (turn && (turn.sender === "buyer_human_approved" || action === "approve" || action === "modify" || action === "override")) {
      decision = "approved";
      opponentAfter = false;
    } else if (turn && (action === "reject" || turn.sender === "reject")) {
      decision = "rejected";
      opponentAfter = false;
    } else if (decision && turn && turn.sender === "opponent") {
      opponentAfter = true;
    }
  }
  if (decision && opponentAfter) return "superseded";
  return decision;
}

function tfGateState(data, session) {
  const state = tfStateName((session && session.state) || (data && data.autopilot_state));
  const pending = !!(data && (
    data.pending_human_approval === true
    || data.awaiting_human_approval === true
    || state === "AWAITING_HUMAN_DECISION"
    || state === "CIRCUIT_TRIPPED_ESCALATED"
    || state === "AWAITING_REMOTE_AUTH"
  ));
  const key = tfRoundKey(data, session);
  const hist = tfHistoryDecision(session);

  if (state === "WALK_AWAY" || (data && data.status === "rejected")) {
    gateMemory = { key: key || gateMemory.key, decision: "rejected" };
    return { chip: "Rejected", superseded: false };
  }
  if (
    state === "HUMAN_APPROVED_APPLIED"
    || state === "DEAL_CLOSED"
    || (data && data.status === "approved" && !pending)
  ) {
    gateMemory = { key: key || gateMemory.key, decision: "approved" };
    return { chip: "Approved", superseded: false };
  }
  if (hist === "rejected" && !pending) {
    gateMemory = { key: key || gateMemory.key, decision: "rejected" };
    return { chip: "Rejected", superseded: false };
  }
  if (hist === "approved" && !pending) {
    gateMemory = { key: key || gateMemory.key, decision: "approved" };
    return { chip: "Approved", superseded: false };
  }
  if (hist === "superseded" && !pending) {
    gateMemory = { key: key || gateMemory.key, decision: "superseded" };
    return { chip: "Superseded", superseded: true };
  }
  if (pending) {
    const replaced = !!(gateMemory.key && key && gateMemory.key !== key);
    gateMemory = { key: key || gateMemory.key, decision: gateMemory.decision === "superseded" ? "" : gateMemory.decision };
    return { chip: "Pending", superseded: replaced || hist === "superseded" };
  }
  if (gateMemory.key && key && gateMemory.key !== key && gateMemory.decision) {
    return { chip: "Superseded", superseded: true };
  }
  if (gateMemory.key && key && gateMemory.key !== key) {
    gateMemory = { key, decision: "superseded" };
    return { chip: "Superseded", superseded: true };
  }
  return { chip: "--", superseded: false };
}

function paintGate(data, session) {
  const gate = tfGateState(data, session);
  if (approvalState) {
    approvalState.textContent = gate.chip;
    approvalState.className = "state-chip";
    const tone = {
      Pending: "is-pending",
      Approved: "is-approved",
      Rejected: "is-rejected",
      Superseded: "is-superseded",
    }[gate.chip];
    if (tone) approvalState.classList.add(tone);
  }
  if (approvalSuperseded) {
    const showNote = gate.superseded && gate.chip !== "Superseded";
    approvalSuperseded.hidden = !showNote;
    if (showNote) approvalSuperseded.textContent = "Superseded";
  }
}

function tfActionLabel(data) {
  const mode = data && data.action_object && data.action_object.tactical_mode;
  if (mode && MODE_LABELS[mode]) return MODE_LABELS[mode];
  if (typeof mode === "string" && mode.trim()) return mode.replace(/_/g, " ");
  if (data && data.inbound_intent) return String(data.inbound_intent).replace(/_/g, " ");
  return null;
}

function tfEnginePrice(data, session) {
  if (data && data.amd_live && data.amd_live.decides_price === true) return null;
  const candidates = [
    data && data.recommended_offer_price,
    data && data.action_object && data.action_object.authorized_price,
    data && data.proposal && data.proposal.opening_offer_price,
    data && data.recommendation && data.recommendation.opening_offer_price,
    session && session.latest_our_offer,
  ];
  const turns = session && Array.isArray(session.history) ? session.history : [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const meta = turns[i] && turns[i].metadata;
    if (meta && meta.recommended_offer_price != null) {
      candidates.push(meta.recommended_offer_price);
      break;
    }
  }
  for (const candidate of candidates) {
    const n = tfNum(candidate, null);
    if (n !== null) return n;
  }
  return null;
}

function tfEvidence(data, session) {
  const parts = [];
  if (data && data.trip_reason) parts.push(String(data.trip_reason));
  if (session && session.trip_reason && parts.indexOf(String(session.trip_reason)) === -1) {
    parts.push(String(session.trip_reason));
  }
  if (data && data.inbound_intent) parts.push(`Intent: ${data.inbound_intent}`);
  else if (data && data.action_object && data.action_object.counterpart_intent) {
    parts.push(`Intent: ${data.action_object.counterpart_intent}`);
  }
  const turns = session && Array.isArray(session.history) ? session.history : [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i] && turns[i].sender === "opponent" && turns[i].raw_text) {
      parts.push(String(turns[i].raw_text));
      break;
    }
  }
  if (data && data.price_source) parts.push(`Price source: ${data.price_source}`);
  if (!parts.length) return null;
  return parts.join(" · ");
}

function tfLookupSavings(bag) {
  if (!bag || typeof bag !== "object") return null;
  for (const [amountKey, basisKey] of SAVINGS_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(bag, amountKey)) continue;
    const amount = tfNum(bag[amountKey], null);
    if (amount === null) continue;
    let shown;
    if (/_pct$/.test(amountKey) || amountKey === "savings_pct") {
      shown = `${(amount * 100).toFixed(1)}%`;
    } else {
      shown = tfMoney(amount);
    }
    const basisRaw = bag[basisKey];
    const basis = typeof basisRaw === "string" && basisRaw.trim() ? basisRaw.trim() : "";
    return { shown, basis: basis || amountKey.replace(/_/g, " ") };
  }
  return null;
}

function tfSavings(data, session) {
  const bags = [
    data,
    data && data.recommendation,
    data && data.proposal,
    data && data.action_object,
  ];
  const turns = session && Array.isArray(session.history) ? session.history : [];
  if (turns.length) bags.push(turns[turns.length - 1] && turns[turns.length - 1].metadata);
  for (const bag of bags) {
    const found = tfLookupSavings(bag);
    if (found) return found;
  }
  return null;
}

function paintRange(target, ceiling, enginePrice, supplierPrice) {
  if (rangeTarget) rangeTarget.textContent = tfMoney(target);
  if (rangeReserve) rangeReserve.textContent = tfMoney(ceiling);
  const t = tfNum(target, null);
  const c = tfNum(ceiling, null);
  const span = t !== null && c !== null ? c - t : null;
  const place = (el, price) => {
    if (!el) return;
    const n = tfNum(price, null);
    if (n === null || span === null || span === 0) {
      el.hidden = true;
      return;
    }
    const pct = Math.min(100, Math.max(0, ((n - t) / span) * 100));
    el.hidden = false;
    el.style.left = `${pct}%`;
  };
  place(rangeEngine, enginePrice);
  place(rangeSupplier, supplierPrice);
  if (priceRange) {
    const labelTarget = tfMoney(target);
    const labelReserve = tfMoney(ceiling);
    priceRange.setAttribute(
      "aria-label",
      labelTarget === "--" && labelReserve === "--"
        ? "Target to reserve range, values unavailable"
        : `Target to reserve range, target ${labelTarget}, reserve ${labelReserve}`
    );
  }
}

function paintRisk(risk) {
  valBreakdownRisk.classList.remove("is-high", "is-ok");
  progressBreakdown.classList.remove("is-high");
  if (risk === null) {
    valBreakdownRisk.textContent = "--";
    progressBreakdown.style.width = "0%";
    return;
  }
  valBreakdownRisk.textContent = `${(risk * 100).toFixed(1)}%`;
  progressBreakdown.style.width = `${Math.min(100, Math.max(0, risk * 100))}%`;
  const trip = tfTrip();
  if (trip !== null && risk >= trip) {
    valBreakdownRisk.classList.add("is-high");
    progressBreakdown.classList.add("is-high");
  } else if (trip !== null) {
    valBreakdownRisk.classList.add("is-ok");
  }
}

function paintRoundCounter(roundNo, maxRounds, turnCount) {
  const shown = roundNo === null ? turnCount : roundNo;
  if (shown === null || shown === undefined) {
    roundCounter.textContent = "Round — / —";
    return;
  }
  if (maxRounds !== null && shown > maxRounds) {
    roundCounter.textContent = `${shown} turns (limit ${maxRounds})`;
    return;
  }
  roundCounter.textContent = `Round ${shown} / ${maxRounds === null ? "—" : maxRounds}`;
}

function turnRow(roundLabel, counter, supplier, risk) {
  const counterText = counter === null ? "Counter: --" : `¥${counter.toFixed(2)} Counter`;
  const supplierText = supplier === null ? "--" : `¥${supplier.toFixed(2)}`;
  const riskText = risk === null ? "--" : `${(risk * 100).toFixed(1)}%`;
  return `
    <div class="timeline-turn">
      <div class="turn-head">
        <strong>Round ${tfEsc(roundLabel)}</strong>
        <span class="turn-price">${tfEsc(counterText)}</span>
      </div>
      <div class="turn-meta">Supplier: ${tfEsc(supplierText)} | Risk: ${tfEsc(riskText)}</div>
    </div>
  `;
}

function clearFigures() {
  valTargetPrice.textContent = "--";
  valReserveCeiling.textContent = "--";
  valPosteriorMean.textContent = "--";
  valPosteriorStd.textContent = "--";
  paintRisk(null);
  roundCounter.textContent = "Round — / —";
  if (approvalAction) approvalAction.textContent = "--";
  if (approvalPrice) approvalPrice.textContent = "--";
  if (approvalEvidence) approvalEvidence.textContent = "--";
  if (approvalState) {
    approvalState.textContent = "--";
    approvalState.className = "state-chip";
  }
  if (approvalSuperseded) approvalSuperseded.hidden = true;
  if (valSavings) valSavings.textContent = "--";
  if (savingsBasis) savingsBasis.textContent = "";
  paintRange(null, null, null, null);
  gateMemory = { key: "", decision: "" };
}

function paintAll(data, session) {
  data = data || {};
  session = session || data.session || {};
  const spec = session.spec || {};
  const turn = data.turn || {};
  const belief = data.bayesian_belief_state || {};
  const action = data.action_object || {};
  const stateName = tfStateName(session.state || data.autopilot_state || "");
  tfStatusPill(stateName);

  const target = tfNum(spec.target_price ?? session.target_price, null);
  const ceiling = tfNum(spec.ceiling_price ?? session.walk_away_ceiling ?? session.ceiling_price, null);
  valTargetPrice.textContent = tfMoney(target);
  valReserveCeiling.textContent = tfMoney(ceiling);

  const turns = Array.isArray(session.history) ? session.history : [];
  const lastTurn = turns.length ? turns[turns.length - 1] : null;
  const postMean = tfNum(
    belief.posterior_mean ?? lastTurn?.posterior_mean ?? action.posterior_reserve_mean,
    null,
  );
  const postStd = tfNum(
    belief.posterior_std ?? lastTurn?.posterior_std ?? action.posterior_reserve_std,
    null,
  );
  valPosteriorMean.textContent = postMean === null ? "--" : postMean.toFixed(3);
  valPosteriorStd.textContent = postStd === null ? "--" : postStd.toFixed(3);

  let risk = tfNum(
    action.breakdown_risk ?? turn.p_break ?? turn.breakdown_risk ?? data.breakdown_risk,
    null,
  );
  if (risk === null && turns.length) {
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const v = tfNum(turns[i].p_break ?? turns[i].breakdown_risk, null);
      if (v !== null) { risk = v; break; }
    }
  }
  paintRisk(risk);

  const maxRounds = tfNum(spec.max_rounds, null);
  const roundNo = tfNum(turn.round_index ?? session.current_round, null);
  paintRoundCounter(roundNo, maxRounds, turns.length ? turns.length : null);

  const enginePrice = tfEnginePrice(data, session);
  const supplierPrice = tfNum(
    data.effective_opp_price ?? action.counterpart_offered_price ?? turn.parsed_price ?? session.latest_opponent_offer,
    null,
  );
  paintRange(target, ceiling, enginePrice, supplierPrice);

  const savings = tfSavings(data, session);
  if (valSavings) valSavings.textContent = savings ? savings.shown : "--";
  if (savingsBasis) savingsBasis.textContent = savings && savings.basis ? savings.basis : "";

  paintGate(data, session);
  if (approvalAction) approvalAction.textContent = tfActionLabel(data) || "--";
  if (approvalPrice) approvalPrice.textContent = tfMoney(enginePrice);
  if (approvalEvidence) approvalEvidence.textContent = tfEvidence(data, session) || "--";

  if (turns.length) {
    turnsTimeline.innerHTML = turns.slice().reverse().map((t) => {
      const tRisk = tfNum(t.p_break ?? t.breakdown_risk, null);
      const tCounter = tfNum(t.buyer_counter_offer ?? (t.sender === "buyer_agent" ? t.parsed_price : null), null);
      const tSupplier = tfNum(t.sender === "opponent" ? (t.parsed_price ?? t.supplier_offer) : (t.supplier_offer ?? null), null);
      const tRound = t.round_index ?? t.round_number ?? "—";
      return turnRow(tRound, tCounter, tSupplier, tRisk);
    }).join("");
  } else if (roundNo !== null || enginePrice !== null || supplierPrice !== null) {
    const counter = tfNum(data.recommended_offer_price ?? action.authorized_price ?? turn.buyer_counter_offer, null);
    const supplier = tfNum(data.effective_opp_price ?? action.counterpart_offered_price ?? turn.parsed_price, null);
    turnsTimeline.innerHTML = turnRow(roundNo === null ? "—" : roundNo, counter, supplier, risk);
  } else {
    turnsTimeline.innerHTML = `<p class="empty">No rounds yet.</p>`;
  }
}

// Establish persistent Port to Background Worker
async function initPortConnection() {
  try {
    const currentWindow = await chrome.windows.getCurrent();
    const windowId = currentWindow?.id || 0;

    sidepanelPort = chrome.runtime.connect({ name: `tf-sidepanel-${windowId}` });

    sidepanelPort.onMessage.addListener((msg) => {
      if (msg.type === "SESSION_STATE_PUSH") {
        if (!msg.tabId || msg.tabId === currentTabId) {
          handleSessionPush(msg.session);
        }
      }

      if (msg.type === "COMMAND_RESULT") {
        const statusSuffix = msg.httpStatus ? ` [HTTP ${msg.httpStatus}]` : "";
        if (msg.success) {
          valLastSignal.textContent = `Command dispatched (${msg.result?.action || "OK"})${statusSuffix}`;
          tfTone(valLastSignal, "tone-ok");
          if (msg.result && msg.result.action === "walk_away") {
            gateMemory.decision = "rejected";
            if (approvalState) {
              approvalState.textContent = "Rejected";
              approvalState.className = "state-chip is-rejected";
            }
          } else if (msg.result && (msg.result.action === "concede_step" || msg.result.action === "hold_firm")) {
            gateMemory.decision = "approved";
            if (approvalState) {
              approvalState.textContent = "Approved";
              approvalState.className = "state-chip is-approved";
            }
          }
        } else if (
          msg.result?.status === "ignored" ||
          /ignored/i.test(String(msg.error || ""))
        ) {
          valLastSignal.textContent = `Command received — applies once the circuit breaker arms the session${statusSuffix}`;
          tfTone(valLastSignal, "tone-warn");
        } else {
          valLastSignal.textContent = `Command NOT applied${statusSuffix}: ${msg.error || "unknown error"}`;
          tfTone(valLastSignal, "tone-bad");
        }
      }
    });

    sidepanelPort.onDisconnect.addListener(() => {
      setTimeout(initPortConnection, 2000);
    });

    syncWithActiveTab();
  } catch (err) {
    showError("The side panel lost its connection to the extension.");
  }
}

async function syncWithActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) return;
    currentTabId = tab.id;
    if (isDealUrl(tab.url)) tabMeta.url = tab.url;
    if (sidepanelPort) {
      sidepanelPort.postMessage({ type: "SYNC_ACTIVE_TAB", tabId: currentTabId });
    }
  } catch (err) {
    /* The tab query can fail while the panel is opening. The next activation retries. */
  }
}

function isDealUrl(url) {
  return !!url && !/^chrome-extension:/i.test(url) && !/sidepanel\.html/i.test(url);
}

function rememberTabMeta(payload) {
  if (!payload || typeof payload !== "object") return;
  if (isDealUrl(payload.url)) tabMeta.url = String(payload.url);
  if (payload.mode) tabMeta.mode = String(payload.mode);
  if (payload.platform) tabMeta.platform = String(payload.platform);
}

function handleSessionPush(payload) {
  if (!payload) {
    if (!hasRenderedLiveData) renderDormantView();
    return;
  }
  rememberTabMeta(payload);
  const nestedRound = payload.latestData && typeof payload.latestData === "object" ? payload.latestData : null;
  const isRound = !!(payload.session && (
    payload.action_object || payload.pending_human_approval != null || payload.autopilot_state
  ));
  const sessionId = payload.sessionId
    || (nestedRound && nestedRound.session && nestedRound.session.spec && nestedRound.session.spec.session_id)
    || (payload.session && payload.session.spec && payload.session.spec.session_id)
    || payload.session_id
    || null;

  if (!sessionId) {
    if (!hasRenderedLiveData) renderDormantView();
    else paintSourceBadge();
    return;
  }

  if (sessionId !== currentSessionId) {
    gateMemory = { key: "", decision: "" };
  }
  currentSessionId = sessionId;
  sessionIdDisplay.textContent = sessionId;
  hideTeaching();

  if (nestedRound) renderEngineData(nestedRound);
  else if (isRound) renderEngineData(payload);
  else fetchSessionFromBackend(sessionId);
}

async function fetchSessionFromBackend(sessionId) {
  showSkeleton();
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      headers: tfAuthHeaders(),
    });
    if (!res.ok) {
      hideSkeleton();
      if (res.status === 404) {
        hideError();
        renderStandbyView(sessionId);
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const session = await res.json();
    hideError();
    renderSession(session);
  } catch (err) {
    hideSkeleton();
    showError(`Could not load the session. ${err && err.message ? err.message : "The TradeFlow backend is unreachable."}`);
  }
}

function renderDormantView() {
  currentSessionId = null;
  gateMemory = { key: "", decision: "" };
  sessionIdDisplay.textContent = "No active tab";
  tfStatusPill("STANDBY");
  clearFigures();
  turnsTimeline.innerHTML = `<p class="empty">No rounds yet.</p>`;
  showTeaching();
  paintSourceBadge();
}

function renderStandbyView(sessionId) {
  sessionIdDisplay.textContent = sessionId || "--";
  tfStatusPill("RADAR LISTENING");
  clearFigures();
  turnsTimeline.innerHTML = `<p class="empty">Awaiting a supplier quote in this chat.</p>`;
  hideTeaching();
  paintSourceBadge();
}

function renderEngineData(data) {
  if (!data) return;
  hasRenderedLiveData = true;
  lastRoundPayload = data;
  hideTeaching();
  hideSkeleton();
  sourceForced = null;
  const session = data.session || {};
  const sid = session.spec && session.spec.session_id;
  if (sid) {
    currentSessionId = sid;
    sessionIdDisplay.textContent = sid;
  }
  paintAll(data, session);
  paintSourceBadge();
}

function renderSession(session) {
  if (!session) return;
  hideSkeleton();
  sourceForced = null;
  const spec = session.spec || {};
  const sid = spec.session_id ?? session.session_id ?? "";
  if (sid) {
    sessionIdDisplay.textContent = sid;
    currentSessionId = sid;
  }
  const stateName = tfStateName(session.state);
  const data = {
    session,
    autopilot_state: stateName,
    pending_human_approval: (
      stateName === "AWAITING_HUMAN_DECISION"
      || stateName === "CIRCUIT_TRIPPED_ESCALATED"
      || stateName === "AWAITING_REMOTE_AUTH"
    ),
    trip_reason: session.trip_reason || "",
  };
  const sameSession = lastRoundPayload
    && lastRoundPayload.session
    && lastRoundPayload.session.spec
    && lastRoundPayload.session.spec.session_id === sid;
  if (sameSession) {
    data.action_object = lastRoundPayload.action_object;
    data.recommended_offer_price = lastRoundPayload.recommended_offer_price;
    data.effective_opp_price = lastRoundPayload.effective_opp_price;
    data.inbound_intent = lastRoundPayload.inbound_intent;
    data.amd_live = lastRoundPayload.amd_live;
    data.recommendation = lastRoundPayload.recommendation;
    data.proposal = lastRoundPayload.proposal;
    data.price_source = lastRoundPayload.price_source;
    data.quote_read = lastRoundPayload.quote_read;
    data.quote_status = lastRoundPayload.quote_status;
    data.simulated_sample = lastRoundPayload.simulated_sample;
  }
  if (Array.isArray(session.history) && session.history.length) hasRenderedLiveData = true;
  hideTeaching();
  paintAll(data, session);
  paintSourceBadge();
}

function dispatchCommand(cmd) {
  if (!currentSessionId) {
    valLastSignal.textContent = "No active session to dispatch command.";
    tfTone(valLastSignal, "tone-warn");
    return;
  }

  if (sidepanelPort) {
    sidepanelPort.postMessage({
      type: "DISPATCH_OVERRIDE_COMMAND",
      payload: { sessionId: currentSessionId, command: cmd },
    });
    valLastSignal.textContent = `Dispatching command '${cmd}'...`;
    tfTone(valLastSignal, "tone-info");
  }
}

btnRefresh?.addEventListener("click", () => {
  hideError();
  loadHealth();
  if (currentSessionId) fetchSessionFromBackend(currentSessionId);
  else syncWithActiveTab();
});

btnRetry?.addEventListener("click", () => {
  hideError();
  loadHealth().then(() => {
    if (currentSessionId) fetchSessionFromBackend(currentSessionId);
    else syncWithActiveTab();
  });
});

document.getElementById("btn-concede")?.addEventListener("click", () => dispatchCommand("1"));
document.getElementById("btn-hold")?.addEventListener("click", () => dispatchCommand("2"));
document.getElementById("btn-abort")?.addEventListener("click", () => dispatchCommand("3"));

chrome.tabs.onActivated.addListener(() => {
  syncWithActiveTab();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tab.active && changeInfo.status === "complete") {
    syncWithActiveTab();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  showSkeleton();
  Promise.all([loadHeuristics(), loadHealth()]).finally(() => {
    hideSkeleton();
    paintSourceBadge();
  });
  initPortConnection();
});
