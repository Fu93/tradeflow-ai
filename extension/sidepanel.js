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

function paintMarketReference(ctx) {
  const root = document.getElementById("marketEvidence");
  if (root && window.TradeFlowMarket) window.TradeFlowMarket.paint(root, ctx || null);
}

function syncMarketChrome() {
  const root = document.getElementById("marketEvidence");
  if (!root || !window.TradeFlowMarket) return;
  const lang = document.documentElement.lang === "zh-Hant" ? "zh-Hant" : "en";
  root.dataset.lang = lang;
  window.TradeFlowMarket.paint(root, root._last || null);
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
const deltaChips = document.getElementById("delta-chips");
const valInbound = document.getElementById("val-inbound");
const rangeFlag = document.getElementById("range-flag");

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
let localDecisionKey = "";
let seenRoundKey = "";
let currentTabName = "decide";
let walkArmed = false;
let popoverReturn = null;
let healthCheckedAt = null;
let lastPaintAt = 0;
let skeletonShowTimer = 0;
let skeletonHideTimer = 0;
let skeletonShownAt = 0;
let toastTimer = 0;
const scrollMem = {};

const SOURCE_MEANING = {
  "amd-live": "A live AMD reading was requested and the health check says it is available.",
  offline: "Cached heuristic — lower confidence.",
  simulator: "This tab is the simulator, not a live supplier chat.",
  unreachable: "The TradeFlow backend could not be read.",
  unknown: "Source has not been checked yet.",
};

const SUGGESTED_BUTTON = {
  LEAN_IN: "btn-concede",
  FIRM_HOLD: "btn-hold",
  WALK_AWAY: "btn-abort",
};

function tfTone(el, tone) {
  if (!el) return;
  el.classList.remove("tone-ok", "tone-warn", "tone-bad", "tone-info", "tone-muted");
  if (tone) el.classList.add(tone);
}

function showSkeleton() {
  window.clearTimeout(skeletonHideTimer);
  if (loadingSkeleton && !loadingSkeleton.hidden) return;
  window.clearTimeout(skeletonShowTimer);
  skeletonShowTimer = window.setTimeout(() => {
    if (!loadingSkeleton) return;
    loadingSkeleton.hidden = false;
    loadingSkeleton.setAttribute("aria-hidden", "false");
    skeletonShownAt = Date.now();
    const deck = document.getElementById("deck");
    if (deck) deck.setAttribute("aria-busy", "true");
  }, 150);
}

function hideSkeleton() {
  window.clearTimeout(skeletonShowTimer);
  const wait = skeletonShownAt ? Math.max(0, 300 - (Date.now() - skeletonShownAt)) : 0;
  window.clearTimeout(skeletonHideTimer);
  skeletonHideTimer = window.setTimeout(() => {
    if (!loadingSkeleton) return;
    loadingSkeleton.hidden = true;
    loadingSkeleton.setAttribute("aria-hidden", "true");
    const deck = document.getElementById("deck");
    if (deck) deck.removeAttribute("aria-busy");
    skeletonShownAt = 0;
  }, wait);
}

function showTeaching() {
  if (teachingEmpty) teachingEmpty.hidden = false;
  document.body.classList.add("is-dormant");
}

function hideTeaching() {
  if (teachingEmpty) teachingEmpty.hidden = true;
  document.body.classList.remove("is-dormant");
}

function showError(message) {
  if (errorText) errorText.textContent = message || "The TradeFlow backend is unreachable.";
  if (errorBanner) errorBanner.hidden = false;
  document.body.classList.add("is-error");
  const note = document.getElementById("stale-note");
  if (note) {
    if (!lastPaintAt) {
      note.textContent = "";
    } else {
      const sec = Math.max(0, Math.round((Date.now() - lastPaintAt) / 1000));
      note.textContent = sec < 60
        ? `Last updated ${sec}s ago`
        : `Last updated ${Math.round(sec / 60)}m ago`;
    }
  }
  sourceForced = "unreachable";
  paintSourceBadge();
  paintGateAvailability(null);
}

function hideError() {
  if (errorBanner) errorBanner.hidden = true;
  document.body.classList.remove("is-error");
  sourceForced = null;
}

function setSignal(text, tone) {
  if (!valLastSignal) return;
  valLastSignal.textContent = text;
  valLastSignal.hidden = !text || text === "--";
  tfTone(valLastSignal, tone || "");
}

function showToast(text) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = text;
  toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 2400);
}

function markBlank(id, blank) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("is-blank", !!blank);
}

function statusTextOverflows() {
  if (!statusText) return false;
  const width = statusText.clientWidth;
  if (width < 1) return false;
  const probe = document.createElement("span");
  const cs = getComputedStyle(statusText);
  probe.textContent = statusText.textContent || "";
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  probe.style.left = "0";
  probe.style.top = "0";
  probe.style.width = `${width}px`;
  probe.style.font = cs.font;
  probe.style.lineHeight = cs.lineHeight;
  probe.style.letterSpacing = cs.letterSpacing;
  probe.style.wordBreak = cs.wordBreak;
  probe.style.overflowWrap = cs.overflowWrap;
  probe.style.whiteSpace = "normal";
  document.body.appendChild(probe);
  const full = probe.getBoundingClientRect().height;
  probe.remove();
  const line = parseFloat(cs.lineHeight) || 16;
  return full > line * 3 + 1;
}

function syncStatusClamp() {
  if (!statusText || !statusBadge) return;
  const more = document.getElementById("status-more");
  const full = (statusText.textContent || "").trim();
  statusText.title = full && full !== "--" ? full : "";
  if (!more) return;
  if (!full || full === "--") {
    more.hidden = true;
    more.setAttribute("aria-expanded", "false");
    statusBadge.classList.remove("is-open");
    return;
  }
  const overflow = statusTextOverflows();
  if (!overflow) {
    more.hidden = true;
    more.setAttribute("aria-expanded", "false");
    statusBadge.classList.remove("is-open");
    return;
  }
  more.hidden = false;
  const open = statusBadge.classList.contains("is-open");
  more.textContent = open ? "Show less" : "Show more";
  more.setAttribute("aria-expanded", open ? "true" : "false");
}

function mirrorActionText() {
  const full = approvalAction ? (approvalAction.textContent || "").trim() : "";
  if (approvalAction) approvalAction.title = full && full !== "--" ? full : "";
  const suggest = document.getElementById("approval-suggest");
  if (suggest) suggest.title = full && full !== "--" ? `Engine suggests: ${full}` : "";
  const decide = document.getElementById("decide-action");
  const row = document.getElementById("decide-suggestion");
  if (decide) decide.textContent = full || "--";
  if (row) row.hidden = !full || full === "--";
}

function tfStatusPill(state) {
  const name = tfStateName(state);
  if (statusText) statusText.textContent = name ? name.replace(/_/g, " ") : "--";
  if (!statusBadge) return;
  const open = statusBadge.classList.contains("is-open");
  statusBadge.className = "status-pill";
  if (open) statusBadge.classList.add("is-open");
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
  syncStatusClamp();
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
  document.body.dataset.source = kind;
  const meaning = document.getElementById("source-meaning");
  if (meaning) meaning.textContent = SOURCE_MEANING[kind] || "--";
  const checked = document.getElementById("source-checked");
  if (checked) checked.textContent = healthCheckedAt ? healthCheckedAt.toLocaleString() : "--";
  const endpoint = document.getElementById("source-endpoint");
  if (endpoint) endpoint.textContent = `${API_BASE_URL}/health`;
}

async function loadHealth() {
  try {
    const res = await fetch(`${API_BASE_URL}/health`, { headers: tfAuthHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    tfHealth = await res.json();
    tfHealthError = null;
    healthCheckedAt = new Date();
  } catch (err) {
    tfHealth = null;
    tfHealthError = err;
    healthCheckedAt = new Date();
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
  const meter = document.getElementById("risk-meter");
  const word = document.getElementById("risk-word");
  const tick = document.getElementById("risk-trip");
  const trip = tfTrip();
  if (tick) {
    if (trip === null) {
      tick.hidden = true;
    } else {
      tick.hidden = false;
      tick.style.left = `${Math.min(100, Math.max(0, trip * 100))}%`;
    }
  }
  if (risk === null) {
    valBreakdownRisk.textContent = "--";
    progressBreakdown.style.width = "0%";
    if (word) word.textContent = "--";
    if (meter) {
      meter.removeAttribute("aria-valuenow");
      meter.setAttribute("aria-valuetext", "Not provided by engine");
    }
    markBlank("risk-card", true);
    return;
  }
  const pct = risk * 100;
  valBreakdownRisk.textContent = `${pct.toFixed(1)}%`;
  progressBreakdown.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  const high = trip !== null && risk >= trip;
  if (high) {
    valBreakdownRisk.classList.add("is-high");
    progressBreakdown.classList.add("is-high");
  } else if (trip !== null) {
    valBreakdownRisk.classList.add("is-ok");
  }
  if (word) word.textContent = trip === null ? "Measured" : (high ? "High" : "Under trip");
  if (meter) {
    meter.setAttribute("aria-valuenow", String(Math.round(pct)));
    meter.setAttribute("aria-valuetext", `${word ? word.textContent : "Measured"}, ${pct.toFixed(1)} percent`);
  }
  markBlank("risk-card", false);
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

function tfWho(turn) {
  if (!turn || !turn.sender) return "";
  if (turn.sender === "opponent") return "Supplier";
  if (turn.sender === "buyer_human_approved" || turn.sender === "human") return "You (via human)";
  if (turn.sender === "buyer_agent") return "Engine";
  return String(turn.sender);
}

function turnRow(roundLabel, counter, supplier, risk, flags) {
  const counterText = counter === null ? "Counter: --" : `¥${counter.toFixed(2)} Counter`;
  const supplierText = supplier === null ? "--" : `¥${supplier.toFixed(2)}`;
  const riskText = risk === null ? "--" : `${(risk * 100).toFixed(1)}%`;
  const who = flags && flags.who ? tfEsc(flags.who) : "";
  const cls = `timeline-turn${flags && flags.current ? " is-current" : ""}`;
  return `
    <div class="${cls}">
      <div class="turn-head">
        <strong>R${tfEsc(roundLabel)}</strong>
        ${who ? `<span>${who}</span>` : ""}
        <span class="turn-price">${tfEsc(counterText)}</span>
      </div>
      <div class="turn-meta">Supplier: ${tfEsc(supplierText)} | Risk: ${tfEsc(riskText)}</div>
    </div>
  `;
}

function tfSignedPct(pct) {
  if (pct === null || Number.isNaN(pct)) return null;
  const tenths = Math.round(pct * 10);
  const body = `${Math.abs(tenths / 10).toFixed(1)}%`;
  if (tenths > 0) return `+${body}`;
  if (tenths < 0) return `−${body}`;
  return body;
}

function tfFavour(engine, inbound, ceiling) {
  if (engine === null) return "";
  if (ceiling !== null && engine > ceiling) return "is-bad";
  if (ceiling !== null && ceiling !== 0) {
    const gap = (ceiling - engine) / ceiling;
    if (gap >= 0 && gap <= (3 / 100)) return "is-warn";
  }
  if (inbound !== null && engine < inbound) return "is-good";
  if (inbound !== null && engine > inbound) return "is-bad";
  return "";
}

function makeChip(pct, label, favour) {
  const chip = document.createElement("span");
  chip.className = `chip${favour ? ` ${favour}` : ""}`;
  if (pct === null) {
    chip.textContent = `-- ${label}`;
    return chip;
  }
  const tenths = Math.round(pct * 10);
  if (tenths < 0) chip.classList.add("is-down");
  else if (tenths === 0) chip.classList.add("is-flat");
  const mark = document.createElement("span");
  mark.className = "chip-mark";
  mark.setAttribute("aria-hidden", "true");
  const text = document.createElement("b");
  text.textContent = `${tfSignedPct(pct)} ${label}`;
  const word = tenths < 0 ? "Down" : (tenths > 0 ? "Up" : "Flat");
  const lean = favour === "is-good" ? "favourable" : (favour === "is-bad" ? "unfavourable" : "neutral");
  chip.setAttribute("aria-label", `${word} ${Math.abs(tenths / 10).toFixed(1)} percent ${label}, ${lean}`);
  chip.title = chip.getAttribute("aria-label");
  chip.appendChild(mark);
  chip.appendChild(text);
  return chip;
}

function tfPreviousCounter(turns, current) {
  const prices = [];
  for (const turn of turns || []) {
    const n = tfNum(
      turn.buyer_counter_offer ?? (turn.sender === "buyer_agent" ? turn.parsed_price : null),
      null,
    );
    if (n !== null) prices.push(n);
  }
  if (!prices.length || current === null) return null;
  const last = prices[prices.length - 1];
  if (last === current && prices.length > 1) return prices[prices.length - 2];
  if (last !== current) return last;
  return null;
}

function paintDeltas(engine, inbound, ceiling, turns) {
  if (!deltaChips) return;
  deltaChips.textContent = "";
  const vsQuote = engine !== null && inbound !== null && inbound !== 0
    ? ((engine - inbound) / inbound) * 100
    : null;
  const prev = tfPreviousCounter(turns, engine);
  const vsPrev = engine !== null && prev !== null && prev !== 0
    ? ((engine - prev) / prev) * 100
    : null;
  deltaChips.appendChild(makeChip(vsQuote, "vs quote", vsQuote === null ? "" : tfFavour(engine, inbound, ceiling)));
  deltaChips.appendChild(makeChip(vsPrev, "vs last round", vsPrev === null ? "" : tfFavour(engine, prev, ceiling)));
}

function paintSuggested(mode) {
  ["btn-concede", "btn-hold", "btn-abort"].forEach((id) => {
    document.getElementById(id)?.classList.remove("is-suggested");
  });
  const id = SUGGESTED_BUTTON[mode];
  if (id) document.getElementById(id)?.classList.add("is-suggested");
}

function paintGateAvailability(enginePrice) {
  const unreachable = sourceForced === "unreachable" || !!tfHealthError;
  const chip = approvalState ? approvalState.textContent.trim() : "";
  const noPrice = enginePrice === null || enginePrice === undefined;
  const decided = chip === "Approved" || chip === "Rejected" || chip === "Superseded";
  const locked = unreachable || noPrice || decided || !!localDecisionKey;
  ["btn-concede", "btn-hold", "btn-abort"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = locked;
    btn.setAttribute("aria-disabled", locked ? "true" : "false");
  });
  const reason = document.getElementById("gate-reason");
  if (!reason) return;
  let text = "";
  if (unreachable) text = "Engine unreachable — Retry";
  else if (chip === "Superseded") text = "A newer quote arrived — review";
  else if (noPrice) text = "Engine has not computed a counter yet";
  else if (localDecisionKey && decided) text = "Decision recorded. Waiting for the next round.";
  reason.textContent = text;
  reason.hidden = !text;
}

function paintContext(spec) {
  const party = document.getElementById("ctx-party");
  const moq = document.getElementById("ctx-moq");
  const channel = document.getElementById("party-channel");
  const name = (spec && (spec.product_name || spec.supplier_id)) || "";
  const partyBtn = document.getElementById("btn-party");
  if (party) {
    party.textContent = name || "--";
    if (party.parentElement) party.parentElement.title = name || "Not provided by engine";
  }
  if (partyBtn) partyBtn.hidden = !name;
  if (moq) {
    const n = tfNum(spec && spec.moq, null);
    moq.textContent = n === null ? "MOQ --" : `MOQ ${n}`;
  }
  if (channel) channel.textContent = spec && spec.supplier_channel ? String(spec.supplier_channel) : "--";
}

function paintEvidence(data, session, supplierPrice) {
  const turns = session && Array.isArray(session.history) ? session.history : [];
  let opp = null;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i] && turns[i].sender === "opponent") { opp = turns[i]; break; }
  }
  const quote = document.getElementById("evidence-quote");
  if (quote) quote.textContent = (opp && opp.raw_text) ? String(opp.raw_text) : "--";
  const when = document.getElementById("evidence-when");
  if (when) {
    const raw = opp && opp.timestamp;
    const d = raw ? new Date(raw) : null;
    if (d && !Number.isNaN(d.getTime())) {
      when.textContent = d.toLocaleString();
      when.title = d.toISOString();
    } else {
      when.textContent = "--";
      when.title = "Not provided by engine";
    }
  }
  const unit = document.getElementById("evidence-unit");
  if (unit) unit.textContent = tfMoney(supplierPrice);
  const emoq = document.getElementById("evidence-moq");
  if (emoq) {
    const n = tfNum(session && session.spec && session.spec.moq, null);
    emoq.textContent = n === null ? "--" : String(n);
  }
  const meta = (opp && opp.metadata) || {};
  const pick = (elId, keys) => {
    const el = document.getElementById(elId);
    if (!el) return;
    let found = "";
    for (const key of keys) {
      if (meta[key] != null && String(meta[key]).trim() && typeof meta[key] !== "object") {
        found = String(meta[key]);
        break;
      }
    }
    el.textContent = found || "--";
  };
  pick("evidence-lead", ["lead_time", "lead_time_days", "delivery"]);
  pick("evidence-incoterm", ["incoterm", "incoterms"]);
  pick("evidence-payment", ["payment", "payment_terms"]);
  const terms = document.getElementById("terms-body");
  if (terms) {
    const mandate = session && session.spec && session.spec.corporate_mandate;
    if (mandate && typeof mandate === "object") {
      const bits = [];
      Object.keys(mandate).forEach((key) => {
        const value = mandate[key];
        if (value === null || value === undefined || typeof value === "object") return;
        bits.push(`${key}: ${value}`);
      });
      terms.textContent = bits.length ? bits.join(" · ") : "--";
    } else {
      terms.textContent = "--";
    }
  }
  const engine = document.getElementById("engine-detail");
  if (engine) engine.textContent = data && data.price_source ? String(data.price_source) : "--";
  const obs = document.getElementById("val-obs");
  if (obs) {
    if (!turns.length) obs.textContent = "--";
    else obs.textContent = String(turns.filter((t) => t && (t.posterior_mean != null || t.sender === "opponent")).length);
  }
}

function paintBeliefSpark(mean, std) {
  const svg = document.getElementById("belief-spark");
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (mean === null || std === null || std === 0) {
    svg.hidden = true;
    return;
  }
  svg.hidden = false;
  const ns = "http://www.w3.org/2000/svg";
  const bins = 24;
  const shade = document.createElementNS(ns, "rect");
  shade.setAttribute("class", "spark-shade");
  shade.setAttribute("x", "30");
  shade.setAttribute("y", "2");
  shade.setAttribute("width", "60");
  shade.setAttribute("height", "24");
  svg.appendChild(shade);
  let d = "";
  for (let i = 0; i < bins; i += 1) {
    const z = (i / (bins - 1)) * 4 - 2;
    const x = Math.round((i / (bins - 1)) * 120);
    const y = Math.round(26 - Math.exp((-z * z) / 2) * 22);
    d += `${i === 0 ? "M" : "L"}${x} ${y}`;
  }
  const path = document.createElementNS(ns, "path");
  path.setAttribute("class", "spark-line");
  path.setAttribute("d", d);
  svg.appendChild(path);
  const mid = document.createElementNS(ns, "line");
  mid.setAttribute("class", "spark-median");
  mid.setAttribute("x1", "60");
  mid.setAttribute("x2", "60");
  mid.setAttribute("y1", "2");
  mid.setAttribute("y2", "26");
  svg.appendChild(mid);
}

function paintRoundsSpark(turns) {
  const svg = document.getElementById("rounds-spark");
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const quotes = [];
  const counters = [];
  (turns || []).forEach((turn) => {
    const quote = tfNum(turn && turn.sender === "opponent" ? (turn.parsed_price ?? turn.supplier_offer) : null, null);
    const counter = tfNum(
      turn && (turn.buyer_counter_offer ?? (turn.sender === "buyer_agent" ? turn.parsed_price : null)),
      null,
    );
    if (quote !== null) quotes.push(quote);
    if (counter !== null) counters.push(counter);
  });
  const all = quotes.concat(counters);
  if (all.length < 2) {
    svg.hidden = true;
    return;
  }
  svg.hidden = false;
  const min = Math.min.apply(null, all);
  const max = Math.max.apply(null, all);
  const span = max - min || 1;
  const ns = "http://www.w3.org/2000/svg";
  const draw = (values, cls) => {
    if (values.length < 2) return;
    const path = document.createElementNS(ns, "path");
    const d = values.map((value, i) => {
      const x = Math.round((i / (values.length - 1)) * 360);
      const y = Math.round(48 - ((value - min) / span) * 40);
      return `${i ? "L" : "M"}${x} ${y}`;
    }).join("");
    path.setAttribute("class", cls);
    path.setAttribute("d", d);
    svg.appendChild(path);
  };
  draw(quotes, "spark-quote");
  draw(counters, "spark-counter");
}

function noteRoundChange(data, session, supplierPrice) {
  const key = tfRoundKey(data, session);
  if (seenRoundKey && key && key !== seenRoundKey) {
    if (localDecisionKey && key !== localDecisionKey) localDecisionKey = "";
    const dot = document.getElementById("evidence-dot");
    if (dot && currentTabName !== "evidence") dot.hidden = false;
    const summary = document.getElementById("live-summary");
    if (summary && supplierPrice !== null) summary.textContent = `New quote ${tfMoney(supplierPrice)} from supplier`;
  }
  if (key) seenRoundKey = key;
}

function paintDriver(action) {
  const driverEl = document.getElementById("risk-driver");
  if (!driverEl) return;
  const driver = action && (action.risk_driver || action.breakdown_driver);
  if (typeof driver === "string" && driver.trim()) {
    driverEl.hidden = false;
    driverEl.textContent = driver.trim();
  } else {
    driverEl.hidden = true;
    driverEl.textContent = "";
  }
}

function clearFigures() {
  valTargetPrice.textContent = "--";
  valReserveCeiling.textContent = "--";
  valPosteriorMean.textContent = "--";
  valPosteriorStd.textContent = "--";
  paintRisk(null);
  roundCounter.textContent = "Round — / —";
  if (approvalAction) approvalAction.textContent = "--";
  mirrorActionText();
  if (approvalPrice) approvalPrice.textContent = "--";
  if (approvalEvidence) approvalEvidence.textContent = "--";
  if (approvalState) {
    approvalState.textContent = "--";
    approvalState.className = "state-chip";
  }
  if (approvalSuperseded) approvalSuperseded.hidden = true;
  if (valSavings) valSavings.textContent = "--";
  if (savingsBasis) savingsBasis.textContent = "";
  if (valInbound) valInbound.textContent = "--";
  if (rangeFlag) rangeFlag.hidden = true;
  paintRange(null, null, null, null);
  paintDeltas(null, null, null, []);
  paintSuggested("");
  paintContext(null);
  paintEvidence(null, null, null);
  paintBeliefSpark(null, null);
  paintRoundsSpark([]);
  paintDriver(null);
  markBlank("approval-card", true);
  markBlank("belief-card", true);
  const count = document.getElementById("rounds-count");
  if (count) count.textContent = "";
  paintGateAvailability(null);
  gateMemory = { key: "", decision: "" };
  paintMarketReference(null);
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
  if (valInbound) {
    valInbound.textContent = tfMoney(supplierPrice);
    valInbound.title = supplierPrice === null ? "Not provided by engine" : "";
  }
  if (rangeFlag) rangeFlag.hidden = !(enginePrice !== null && ceiling !== null && enginePrice > ceiling);
  paintDeltas(enginePrice, supplierPrice, ceiling, turns);

  const savings = tfSavings(data, session);
  if (valSavings) {
    valSavings.textContent = savings ? savings.shown : "--";
    valSavings.title = savings ? "" : "Not provided by engine";
  }
  if (savingsBasis) savingsBasis.textContent = savings && savings.basis ? savings.basis : "";

  paintGate(data, session);
  if (approvalAction) approvalAction.textContent = tfActionLabel(data) || "--";
  mirrorActionText();
  if (approvalPrice) {
    approvalPrice.textContent = tfMoney(enginePrice);
    approvalPrice.title = enginePrice === null ? "Not provided by engine" : "Engine-computed";
  }
  if (approvalEvidence) approvalEvidence.textContent = tfEvidence(data, session) || "--";
  markBlank("approval-card", enginePrice === null);
  markBlank("belief-card", postMean === null);
  paintSuggested(action.tactical_mode);
  paintContext(spec);
  paintEvidence(data, session, supplierPrice);
  paintBeliefSpark(postMean, postStd);
  paintDriver(action);
  paintRoundsSpark(turns);
  noteRoundChange(data, session, supplierPrice);
  const roundsCount = document.getElementById("rounds-count");
  if (roundsCount) roundsCount.textContent = turns.length ? String(turns.length) : "";
  paintGateAvailability(enginePrice);
  paintMarketReference(spec.market_context || null);
  lastPaintAt = Date.now();

  if (turns.length) {
    const ordered = turns.slice().reverse();
    turnsTimeline.innerHTML = ordered.map((t, index) => {
      const tRisk = tfNum(t.p_break ?? t.breakdown_risk, null);
      const tCounter = tfNum(t.buyer_counter_offer ?? (t.sender === "buyer_agent" ? t.parsed_price : null), null);
      const tSupplier = tfNum(t.sender === "opponent" ? (t.parsed_price ?? t.supplier_offer) : (t.supplier_offer ?? null), null);
      const tRound = t.round_index ?? t.round_number ?? "—";
      return turnRow(tRound, tCounter, tSupplier, tRisk, { current: index === 0, who: tfWho(t) });
    }).join("");
  } else if (roundNo !== null || enginePrice !== null || supplierPrice !== null) {
    const counter = tfNum(data.recommended_offer_price ?? action.authorized_price ?? turn.buyer_counter_offer, null);
    const supplier = tfNum(data.effective_opp_price ?? action.counterpart_offered_price ?? turn.parsed_price, null);
    turnsTimeline.innerHTML = turnRow(roundNo === null ? "—" : roundNo, counter, supplier, risk, { current: true, who: "Engine" });
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
          setSignal(`Command dispatched (${msg.result?.action || "OK"})${statusSuffix}`, "tone-ok");
          localDecisionKey = gateMemory.key || "local";
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
          const session = (lastRoundPayload && lastRoundPayload.session) || {};
          paintGateAvailability(tfEnginePrice(lastRoundPayload || {}, session));
        } else if (
          msg.result?.status === "ignored" ||
          /ignored/i.test(String(msg.error || ""))
        ) {
          setSignal(`Command received — applies once the circuit breaker arms the session${statusSuffix}`, "tone-warn");
        } else {
          setSignal(`Command NOT applied${statusSuffix}: ${msg.error || "unknown error"}`, "tone-bad");
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
  const map = { "1": "btn-concede", "2": "btn-hold", "3": "btn-abort" };
  const btn = document.getElementById(map[cmd] || "");
  if (btn && btn.disabled) return;
  if (!currentSessionId) {
    setSignal("No active session to dispatch command.", "tone-warn");
    return;
  }

  if (sidepanelPort) {
    sidepanelPort.postMessage({
      type: "DISPATCH_OVERRIDE_COMMAND",
      payload: { sessionId: currentSessionId, command: cmd },
    });
    setSignal(`Dispatching command '${cmd}'...`, "tone-info");
  }
}

function selectTab(name) {
  const scroll = document.getElementById("scroll");
  if (scroll && currentTabName) scrollMem[currentTabName] = scroll.scrollTop;
  currentTabName = name;
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    const on = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) {
      panel.hidden = !on;
      if (on) panel.classList.add("panel-in");
    }
  });
  if (name === "evidence") {
    const dot = document.getElementById("evidence-dot");
    if (dot) dot.hidden = true;
  }
  if (scroll) scroll.scrollTop = scrollMem[name] || 0;
  try { chrome.storage.session.set({ tfTab: name }); } catch (err) { /* optional */ }
}

function closePopovers(restore) {
  ["menu-popover", "source-popover", "party-popover"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  ["btn-menu", "source-badge", "btn-party"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.setAttribute("aria-expanded", "false");
  });
  if (restore && popoverReturn && typeof popoverReturn.focus === "function") popoverReturn.focus();
  popoverReturn = null;
}

function togglePopover(id, opener) {
  const el = document.getElementById(id);
  if (!el || !opener) return;
  const willOpen = el.hidden;
  closePopovers(false);
  if (!willOpen) return;
  popoverReturn = opener;
  el.hidden = false;
  opener.setAttribute("aria-expanded", "true");
  const focusable = el.querySelector("button, [href], input");
  if (focusable) focusable.focus();
}

function armWalk() {
  const btn = document.getElementById("btn-abort");
  if (btn && btn.disabled) return;
  walkArmed = true;
  const buttons = document.getElementById("gate-buttons");
  const confirm = document.getElementById("gate-confirm");
  if (buttons) buttons.hidden = true;
  if (confirm) confirm.hidden = false;
  document.getElementById("btn-confirm-walk")?.focus();
}

function disarmWalk() {
  walkArmed = false;
  const buttons = document.getElementById("gate-buttons");
  const confirm = document.getElementById("gate-confirm");
  if (buttons) buttons.hidden = false;
  if (confirm) confirm.hidden = true;
}

function setFold(fold, open) {
  if (!fold) return;
  fold.classList.toggle("is-closed", !open);
  const toggle = fold.querySelector(".fold-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function applyTheme(mode) {
  if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
  const btn = document.getElementById("btn-theme");
  const label = mode === "dark" ? "Dark" : (mode === "light" ? "Light" : "System");
  if (btn) btn.textContent = `Appearance: ${label}`;
  try { chrome.storage.local.set({ tfTheme: mode || "system" }); } catch (err) { /* optional */ }
  syncMarketChrome();
}

function applyLang(lang) {
  const next = lang === "zh-Hant" ? "zh-Hant" : "en";
  document.documentElement.lang = next;
  const btn = document.getElementById("btn-lang");
  if (btn) btn.textContent = next === "zh-Hant" ? "Language: 繁中" : "Language: English";
  try { chrome.storage.local.set({ tfLang: next }); } catch (err) { /* optional */ }
  syncMarketChrome();
}

async function copyText(text, ok, fail) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(ok);
  } catch (err) {
    showToast(fail);
  }
}

function copyPrice() {
  const price = approvalPrice ? approvalPrice.textContent : "--";
  const why = approvalEvidence ? approvalEvidence.textContent : "";
  copyText(
    `${price}\n${why}\nyou paste and send; TradeFlow never sends.`,
    "Copied — paste into chat yourself",
    "Could not copy. Select the price and copy it yourself.",
  );
}

function bindChrome() {
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  });
  document.querySelector(".tabbar")?.addEventListener("keydown", (event) => {
    const tabs = Array.from(document.querySelectorAll("[data-tab]"));
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    let next = -1;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    tabs[next].focus();
    selectTab(tabs[next].dataset.tab);
  });
  document.querySelectorAll(".fold").forEach((fold) => {
    const toggle = fold.querySelector(".fold-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", () => {
      const open = fold.classList.contains("is-closed");
      setFold(fold, open);
      try { chrome.storage.local.set({ [`tfFold_${fold.id}`]: open }); } catch (err) { /* optional */ }
    });
  });
  document.getElementById("btn-menu")?.addEventListener("click", () => {
    togglePopover("menu-popover", document.getElementById("btn-menu"));
  });
  document.getElementById("source-badge")?.addEventListener("click", () => {
    togglePopover("source-popover", document.getElementById("source-badge"));
  });
  document.getElementById("btn-party")?.addEventListener("click", () => {
    togglePopover("party-popover", document.getElementById("btn-party"));
  });
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest(".popover, #btn-menu, #source-badge, #btn-party")) return;
    closePopovers(false);
  });
  document.getElementById("btn-copy-price")?.addEventListener("click", () => copyPrice());
  document.getElementById("btn-copy-session")?.addEventListener("click", () => {
    const id = sessionIdDisplay ? sessionIdDisplay.textContent : "";
    if (!id || id === "--" || id === "No active tab") {
      showToast("No session id yet.");
      return;
    }
    copyText(id, "Session id copied.", "Could not copy the session id.");
  });
  document.getElementById("btn-jump-telemetry")?.addEventListener("click", () => {
    closePopovers(false);
    selectTab("evidence");
    document.getElementById("telemetry")?.scrollIntoView({ block: "nearest" });
  });
  document.getElementById("btn-theme")?.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme || "system";
    const next = current === "system" ? "light" : (current === "light" ? "dark" : "system");
    applyTheme(next);
  });
  document.getElementById("btn-lang")?.addEventListener("click", () => {
    applyLang(document.documentElement.lang === "zh-Hant" ? "en" : "zh-Hant");
  });
  document.getElementById("btn-sandbox")?.addEventListener("click", () => {
    try {
      chrome.tabs.create({ url: chrome.runtime.getURL("mock_chat.html") });
    } catch (err) {
      showToast("Could not open the sample sandbox.");
    }
  });
  document.getElementById("btn-confirm-walk")?.addEventListener("click", () => {
    disarmWalk();
    dispatchCommand("3");
  });
  document.getElementById("btn-undo-walk")?.addEventListener("click", () => disarmWalk());
  document.addEventListener("keydown", (event) => {
    const tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (event.target && event.target.isContentEditable)) return;
    if (event.key === "Escape") {
      if (walkArmed) { disarmWalk(); return; }
      closePopovers(true);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && walkArmed) {
      event.preventDefault();
      disarmWalk();
      dispatchCommand("3");
      return;
    }
    if ((event.metaKey || event.ctrlKey) && (event.key === "c" || event.key === "C")) {
      const hero = document.getElementById("approval-card");
      const selected = window.getSelection ? window.getSelection().toString() : "";
      if (hero && hero.contains(document.activeElement) && !selected) {
        event.preventDefault();
        copyPrice();
      }
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Enter" && walkArmed) {
      event.preventDefault();
      disarmWalk();
      dispatchCommand("3");
      return;
    }
    if (walkArmed) return;
    if (event.key === "1") dispatchCommand("1");
    else if (event.key === "2") dispatchCommand("2");
    else if (event.key === "3") armWalk();
  });
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

document.getElementById("status-more")?.addEventListener("click", () => {
  if (!statusBadge) return;
  statusBadge.classList.toggle("is-open");
  syncStatusClamp();
});
if (statusText) {
  const statusWatch = new MutationObserver(() => syncStatusClamp());
  statusWatch.observe(statusText, { childList: true, characterData: true, subtree: true });
}
if (approvalAction) {
  const actionWatch = new MutationObserver(() => mirrorActionText());
  actionWatch.observe(approvalAction, { childList: true, characterData: true, subtree: true });
}
document.getElementById("btn-concede")?.addEventListener("click", () => dispatchCommand("1"));
document.getElementById("btn-hold")?.addEventListener("click", () => dispatchCommand("2"));
document.getElementById("btn-abort")?.addEventListener("click", () => armWalk());

chrome.tabs.onActivated.addListener(() => {
  syncWithActiveTab();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (tab.active && changeInfo.status === "complete") {
    syncWithActiveTab();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const marketRoot = document.getElementById("marketEvidence");
  if (marketRoot && window.TradeFlowMarket) {
    window.TradeFlowMarket.mount(marketRoot, {
      endpoint: API_BASE_URL + "/api/v1/market",
      headers: tfAuthHeaders,
    });
  }
  bindChrome();
  paintDeltas(null, null, null, []);
  paintGateAvailability(null);
  showSkeleton();
  Promise.all([loadHeuristics(), loadHealth()]).finally(() => {
    hideSkeleton();
    paintSourceBadge();
  });
  initPortConnection();
});
