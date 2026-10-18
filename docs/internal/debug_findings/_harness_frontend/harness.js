/**
 * Verification harness (one-off) — extension/content.js behaviour (P0-2, P0-3, P0-1).
 *
 * Loads the REAL extension/content.js inside a stub browser environment
 * (window/document/HTMLElement/customElements/MutationObserver/chrome + a fake
 * Port) and drives the three paths that the review found broken:
 *
 *   P0-1  the DOM branch's session_id must arrive at the port already legal
 *         (compared against the captured contract id + the backend whitelist).
 *   P0-3  captcha trip -> solved must NOT freeze the page: the recovery block has
 *         to run and a later quote has to be dispatched again.
 *   P0-2  SESSION_UPDATED payloads captured from the real backend must render the
 *         decision deck with the real numbers; a tripped payload must show
 *         CIRCUIT TRIPPED without cards; an unpriced (turn:null) payload must show
 *         an explicit placeholder instead of staying silent.
 *
 * Run:  node harness.js <repoRoot> [capture.json]
 */
const fs = require("fs");
const path = require("path");

const repo = process.argv[2] || process.cwd();
const capturePath = process.argv[3] || path.join(__dirname, "capture.json");
const capture = JSON.parse(fs.readFileSync(capturePath, "utf8"));
const source = fs.readFileSync(path.join(repo, "extension", "content.js"), "utf8");

// ---------------------------------------------------------------------------
// Minimal DOM / chrome stubs
// ---------------------------------------------------------------------------
let failures = 0;
function check(name, ok, extra) {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"} | ${name}${extra ? ` :: ${extra}` : ""}`);
}

class FakeHTMLElement {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1; // Node.ELEMENT_NODE — the observer's first filter
    this.children = [];
    this.classList = [];
    this.attributes = {};
    this.style = { setProperty() {}, getPropertyValue: () => "" };
    this.offsetParent = {};
    this.innerHTML = "";
    this.textContent = "";
    this.innerText = "";
    this.parentElement = null;
  }
  addEventListener() {}
  removeEventListener() {}
  setAttribute(k, v) { this.attributes[k] = v; }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; }
  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    if (typeof child.connectedCallback === "function") child.connectedCallback();
    return child;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  attachShadow() { this.shadowRoot = new ShadowRootStub(); return this.shadowRoot; }
}

class ShadowRootStub {
  constructor() { this._els = new Map(); this._html = ""; }
  set innerHTML(v) { this._html = v; this._els.clear(); }
  get innerHTML() { return this._html; }
  getElementById(id) { return this._stub(id); }
  querySelector(sel) { return this._stub(sel); }
  querySelectorAll() { return []; }
  _stub(key) {
    if (!this._els.has(key)) this._els.set(key, new FakeHTMLElement("div"));
    return this._els.get(key);
  }
}

const customRegistry = {};
const createdOverlays = [];
const customElementsStub = {
  define(name, cls) { customRegistry[String(name).toLowerCase()] = cls; },
  get(name) { return customRegistry[String(name).toLowerCase()]; },
};

const messagesBox = new FakeHTMLElement("div");
const supplierNameEl = new FakeHTMLElement("div");
supplierNameEl.innerText = capture.ids.mockName;
const captchaEl = new FakeHTMLElement("div");
let captchaPresent = false;

const CONTAINER_SELECTOR = ".chat-messages, .im-message-box, #messages-box, .chat-list-left, #chatMessages, .messages-container";
const CAPTCHA_TRIGGERS = ["#nc_1_n1z", "#baxia-dialog-content"];

const documentStub = {
  readyState: "complete",
  title: "1688 Supplier Chat",
  body: new FakeHTMLElement("body"),
  addEventListener() {},
  querySelector(sel) {
    const s = String(sel);
    if (s.includes("supplier-name")) return supplierNameEl;
    if (s === CONTAINER_SELECTOR) return messagesBox;
    if (CAPTCHA_TRIGGERS.includes(s)) return captchaPresent ? captchaEl : null;
    return null;
  },
  querySelectorAll() { return []; },
  createElement(tag) {
    const cls = customRegistry[String(tag).toLowerCase()];
    if (cls) { const el = new cls(); createdOverlays.push(el); return el; }
    return new FakeHTMLElement(tag);
  },
  getElementById() { return null; },
};

const observers = [];
class MutationObserverStub {
  constructor(cb) { this.cb = cb; this.target = null; this.connected = true; observers.push(this); }
  observe(target, opts) { this.target = target; this.opts = opts; }
  disconnect() { this.connected = false; }
  takeRecords() { return []; }
}

const port = {
  name: "tf-content",
  listeners: [],
  sent: [],
  postMessage(msg) { port.sent.push(msg); },
  onMessage: { addListener(fn) { port.listeners.push(fn); } },
  onDisconnect: { addListener() {} },
  disconnect() {},
};
const chromeStub = { runtime: { connect: () => port, onMessage: { addListener() {} }, lastError: null } };
const navigatorStub = { clipboard: { writeText: async () => {} } };

const windowStub = {
  location: { href: "file:///C:/Users/nn201/Desktop/tradeflow-ai/extension/mock_chat.html", hostname: "", pathname: "/C:/Users/nn201/Desktop/tradeflow-ai/extension/mock_chat.html", search: "" },
  addEventListener() {},
};

// ---------------------------------------------------------------------------
// Boot the real content script
// ---------------------------------------------------------------------------
const runner = new Function(
  "window", "document", "chrome", "navigator", "Node", "HTMLElement", "customElements", "MutationObserver",
  source
);
runner(windowStub, documentStub, chromeStub, navigatorStub, { ELEMENT_NODE: 1 }, FakeHTMLElement, customElementsStub, MutationObserverStub);

const overlay = createdOverlays[0];
const shadow = overlay ? overlay.shadowRoot : null;
const badge = () => (shadow ? shadow.getElementById("tf-badge").textContent : "<no overlay>");
const deckHtml = () => (shadow ? shadow.getElementById("tf-deck").innerHTML : "");
const subText = () => (shadow ? shadow.getElementById("tf-subtext").textContent : "");

const BACKEND_RE = /^[A-Za-z0-9._-]{1,128}$/;
const messagesOf = (type) => port.sent.filter((m) => m.type === type);

// --- P0-1: what the DOM branch actually registered --------------------------
const registered = messagesOf("REGISTER_SESSION")[0];
check("P0-1 overlay created and REGISTER_SESSION dispatched", !!registered, JSON.stringify(registered && registered.payload));
if (registered) {
  const sid = registered.payload.sessionId;
  check("P0-1 DOM-branch session_id is backend-legal", BACKEND_RE.test(sid), sid);
  check("P0-1 matches the id computed from the same source text", sid === capture.ids.mockNameId, `${sid} vs ${capture.ids.mockNameId}`);
}

// --- helper: feed an inbound chat bubble through the chat observer ----------
const chatObserver = () => observers.find((o) => o.target === messagesBox);
function simulateQuote(text) {
  const obs = chatObserver();
  if (!obs) throw new Error("chat observer was never attached to the message box");
  const node = new FakeHTMLElement("div");
  node.classList.push("msg-bubble", "msg-received");
  node.innerText = text;
  node.textContent = text;
  obs.cb([{ type: "childList", addedNodes: [node] }], obs);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  check("observer attached to the chat container", !!chatObserver());

  // --- P0-3: captcha trip then recovery -------------------------------------
  captchaPresent = true;
  simulateQuote("您好！初次配合給您的單價報價為 ¥15.60 / 件。");
  await sleep(500);
  check("P0-3 captcha challenge freezes the radar", badge() === "CAPTCHA DETECTED", `badge=${badge()}`);
  check("P0-3 no offer dispatched while the challenge is up", messagesOf("SUBMIT_OFFER").length === 0);

  captchaPresent = false; // operator solves the slider
  simulateQuote("老闆您好，最新讓步報價單價為 ¥14.80 元/件，請審閱。");
  await sleep(500);
  const offers = messagesOf("SUBMIT_OFFER");
  check("P0-3 quote dispatched again after the slider is solved (no permanent freeze)", offers.length === 1, `offers=${offers.length}`);
  check("P0-3 radar status left CAPTCHA DETECTED", badge() !== "CAPTCHA DETECTED", `badge=${badge()}`);
  check("P0-3 session re-registered after recovery", messagesOf("REGISTER_SESSION").length === 2);
  if (offers.length) {
    check("P0-3 dispatched offer carries the parsed price", offers[0].payload.offer === 14.8, JSON.stringify(offers[0].payload));
  }

  // --- P0-2: deck rendering from REAL captured backend payloads --------------
  const low = capture.responses.copilot_lowrisk_r1;
  const high = capture.responses.copilot_highrisk_r2;
  const trip = capture.responses.copilot_ceiling_breach;

  check("P0-2 captured copilot payload really has turn === null (old field path)", low && low.turn === null, `turn=${JSON.stringify(low && low.turn)}`);

  port.listeners.forEach((fn) => fn({ type: "SESSION_UPDATED", data: low }));
  const lowDeck = deckHtml();
  check("P0-2 deck renders on a copilot payload", badge() === "COPILOT CARDS READY", `badge=${badge()}`);
  check("P0-2 deck shows the real supplier price", lowDeck.includes(Number(low.effective_opp_price).toFixed(2)), `expect ${low.effective_opp_price}`);
  check("P0-2 deck shows the real counter price", lowDeck.includes(Number(low.recommended_offer_price).toFixed(2)), `expect ${low.recommended_offer_price}`);
  check("P0-2 deck shows the real deal probability", lowDeck.includes(String(Math.round(low.bayesian_belief_state.deal_probability * 100))), `expect ${low.bayesian_belief_state.deal_probability}`);
  check("P0-2 HUD round comes from session.current_round", subText().includes(`Round ${low.session.current_round}`), `sub=${subText()}`);

  port.listeners.forEach((fn) => fn({ type: "SESSION_UPDATED", data: high }));
  const highDeck = deckHtml();
  const highSub = subText();
  check("P0-2 second payload re-renders with its own numbers", highDeck.includes(Number(high.effective_opp_price).toFixed(2)) && highDeck.includes(Number(high.recommended_offer_price).toFixed(2)), `supplier=${high.effective_opp_price} counter=${high.recommended_offer_price}`);

  if (trip && trip.circuit_tripped === true) {
    port.listeners.forEach((fn) => fn({ type: "SESSION_UPDATED", data: trip }));
    check("P0-2 tripped payload shows CIRCUIT TRIPPED", badge() === "CIRCUIT TRIPPED", `badge=${badge()}`);
    check("P0-2 tripped payload does not render new cards", deckHtml() === highDeck);
    const risk = (trip.action_object || {}).breakdown_risk;
    check("P0-2 tripped status quotes the real risk value", risk == null || subText().includes((risk * 100).toFixed(1)), `sub=${subText()}`);
  } else {
    check("P0-2 tripped payload captured (CEILING_BREACH probe)", false, JSON.stringify(trip && trip.circuit_tripped));
  }

  // --- P0-2: unpriced payload must show a placeholder, never silence ---------
  port.listeners.forEach((fn) =>
    fn({ type: "SESSION_UPDATED", data: { session: { spec: {}, state: "AWAITING_HUMAN_DECISION", current_round: 3 }, action_object: null, turn: null, effective_opp_price: null, recommended_offer_price: null } })
  );
  check("P0-2 unpriced payload shows an explicit placeholder", badge() === "AWAITING PRICED QUOTE", `badge=${badge()}`);
  const placeholderDeck = deckHtml();
  check("P0-2 placeholder does not replace the deck with fabricated numbers", placeholderDeck === highDeck);

  // --- meta-only push must not disturb the HUD ------------------------------
  port.listeners.forEach((fn) => fn({ type: "SESSION_UPDATED", data: { sessionMeta: { sessionId: "meta_only_probe" } } }));
  check("meta-only REGISTER echo is ignored", badge() === "AWAITING PRICED QUOTE", `badge=${badge()}`);

  console.log(`\n--- evidence ---`);
  console.log(`captured ids: ${capture.ids.mockNameId} (fixed) / ${capture.ids.legacyCjkId} (legacy, 422)`);
  const sub = highDeck.indexOf("Target Counter-Offer");
  console.log(`high-risk deck status line: ${highSub}`);
  console.log(`deck html length after cards: ${highDeck.length}`);
  console.log(`failures=${failures}`);
  process.exit(failures ? 1 : 0);
})().catch((err) => {
  console.error("HARNESS ERROR:", err);
  process.exit(2);
});
