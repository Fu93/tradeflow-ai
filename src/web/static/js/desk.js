(function () {
  const POLL_MS = 2000;
  const reviewChoice = new Map();
  // Typed prices in progress, by waiting quote, so a poll's redraw keeps them.
  const typedDrafts = new Map();
  let lastReviewSig = "";
  // Supplier cards whose "How these figures were worked out" section is open.
  const workingsOpen = new Set();
  // Web evidence (Tavily) per supplier. The desk redraws every card on each poll, so a
  // result lives here, keyed by supplier id, and is painted into whichever card is on
  // screen. It is reference text: the counter is computed on the server from confirmed
  // quotes only and never reads it.
  const evidence = new Map();
  let dealProduct = "";
  let dealCurrency = "CNY";

  function apiKey() {
    const field = document.getElementById("api-key");
    const typed = field && field.value.trim();
    if (typed) {
      sessionStorage.setItem("tfApiKey", typed);
      return typed;
    }
    return sessionStorage.getItem("tfApiKey") || localStorage.getItem("tfApiKey") || "";
  }

  function headers(json) {
    const value = {};
    if (json) value["Content-Type"] = "application/json";
    const key = apiKey();
    if (key) value["X-API-Key"] = key;
    return value;
  }

  function money(value, currency) {
    if (value == null || value !== value) return "no data";
    const negative = value < 0;
    const scaled = Math.round(Math.abs(value) * 100);
    const whole = Math.floor(scaled / 100);
    const frac = String(scaled % 100).padStart(2, "0");
    const text = (negative ? "-" : "") + String(whole) + "." + frac;
    return currency ? text + " " + currency : text;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function line(parent, text) {
    const p = document.createElement("p");
    p.textContent = text;
    parent.appendChild(p);
    return p;
  }

  // The supplier's stated quote validity. It is shown, never used to rank or
  // price: the send guard re-derives the counter from the chain, and a figure
  // that depended on today's date would not re-derive.
  function localDay(now) {
    const pad = (n) => String(n).padStart(2, "0");
    return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
  }

  function daysSince(iso, now) {
    const then = Date.parse(iso || "");
    if (Number.isNaN(then)) return null;
    return Math.max(0, Math.floor((now.getTime() - then) / 86400000));
  }

  function validityText(supplier, now) {
    const v = supplier.quote_validity;
    const quoted = (spans) => spans.map((span) => "\u201c" + span + "\u201d").join(" / ");
    if (!v) {
      const age = daysSince(supplier.quote_received_at, now);
      return {
        warn: false,
        text: "No validity stated" + (
          age == null ? "." : age === 0 ? "; received today." : "; received " + age + (age === 1 ? " day" : " days") + " ago."
        ),
      };
    }
    if (v.status === "conflict") {
      return { warn: true, text: "The message gives more than one validity: " + quoted(v.spans || []) + ". Check which one stands." };
    }
    if (v.status === "working_days") {
      return { warn: false, text: "Validity is in working days: " + quoted([v.span]) + ". Check the end date with the supplier." };
    }
    const from = v.counted_from === "received" ? ", counted from when the desk received the message" : "";
    if (localDay(now) > v.valid_until) {
      return {
        warn: true,
        text: "Quote expired on " + v.valid_until + " (" + quoted([v.span]) + from + "). " +
          "Ask the supplier to confirm the price still stands before you send. It is still ranked.",
      };
    }
    return { warn: false, text: "Valid until " + v.valid_until + " (" + quoted([v.span]) + from + ")." };
  }

  function detailMessage(body) {
    if (!body) return "Request failed";
    if (typeof body.detail === "string") return body.detail.replace(/^Value error,\s*/, "");
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => {
        const msg = String(item.msg || "invalid").replace(/^Value error,\s*/, "");
        // Name the field: "reserve prior mean: Input should be less than 1".
        const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body") : [];
        const field = loc.length ? String(loc[loc.length - 1]).replace(/_/g, " ") : "";
        return field && !/^\d+$/.test(field) ? field + ": " + msg : msg;
      }).join(" ");
    }
    return "Request failed";
  }

  async function request(path, options) {
    const response = await fetch(path, options);
    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }
    if (!response.ok) {
      const error = new Error(detailMessage(body));
      error.status = response.status;
      throw error;
    }
    return body;
  }

  const form = document.getElementById("desk-form");
  const formError = document.getElementById("form-error");
  // The form (and its error line) is hidden once a deal is open; errors from
  // the board's own buttons go here instead.
  const boardError = document.getElementById("board-error");
  const board = document.getElementById("board");
  let dealId = sessionStorage.getItem("tfDeskDeal") || "";
  let timer = 0;

  function suppliersFrom(text) {
    const rows = text.split("\n").map((item) => item.trim()).filter(Boolean);
    return rows.map((row) => {
      const comma = row.lastIndexOf(",");
      if (comma < 1) return null;
      const contact = row.slice(comma + 1).trim();
      const name = row.slice(0, comma).trim();
      if (!contact) return null;
      // "wechat:老王" or "whatsapp:+86 138…" is a chat contact; its drafts are
      // copied and sent by hand. Anything else must be one email address.
      const chat = /^(wechat|weixin|微信|whatsapp|chat)\s*[:：]\s*(.+)$/i.exec(contact);
      if (chat) {
        const kind = chat[1].toLowerCase();
        const channel = kind === "whatsapp" ? "whatsapp" : (kind === "chat" ? "chat" : "wechat");
        return { name: name, address: chat[2].trim(), channel: channel };
      }
      if (contact.indexOf("@") < 1) return null;
      return { name: name, address: contact, channel: "email" };
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.textContent = "";
    const suppliers = suppliersFrom(document.getElementById("suppliers").value);
    if (suppliers.some((item) => !item || !item.name || !item.address)) {
      formError.textContent = "Each line needs a name and an email, or a chat contact such as wechat:laowang.";
      return;
    }
    const payload = {
      product: document.getElementById("product").value.trim(),
      quantity: Number(document.getElementById("quantity").value),
      target_price: Number(document.getElementById("target").value),
      ceiling_price: Number(document.getElementById("ceiling").value),
      currency: document.getElementById("deal-currency").value || "CNY",
      suppliers: suppliers,
    };
    const risk = document.getElementById("risk").value.trim();
    if (risk) payload.risk_tolerance = Number(risk);
    try {
      const snapshot = await request("/api/v1/desk/deals", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify(payload),
      });
      dealId = snapshot.deal.id;
      sessionStorage.setItem("tfDeskDeal", dealId);
      form.hidden = true;
      show(snapshot);
      start();
    } catch (err) {
      formError.textContent = err.message;
    }
  });

  // Leave this deal (it stays in the desk database) and show the form again.
  document.getElementById("new-deal").addEventListener("click", () => {
    if (timer) window.clearInterval(timer);
    timer = 0;
    dealId = "";
    sessionStorage.removeItem("tfDeskDeal");
    reviewChoice.clear();
    workingsOpen.clear();
    board.hidden = true;
    formError.textContent = "";
    form.hidden = false;
    form.scrollIntoView({ block: "start" });
  });

  function start() {
    if (timer) window.clearInterval(timer);
    timer = window.setInterval(refresh, POLL_MS);
  }

  async function refresh() {
    if (!dealId) return;
    try {
      const snapshot = await request("/api/v1/desk/deals/" + dealId, { headers: headers(false) });
      show(snapshot);
    } catch (err) {
      formError.textContent = err.status === 404
        ? "This deal is not in the desk database. A fresh disk does not keep the previous deal. Open a new one."
        : err.status === 401
          ? "The desk needs the API key configured on this server."
          : err.message;
      if (err.status !== 404 && err.status !== 401) boardError.textContent = formError.textContent;
      if (err.status === 401) form.hidden = false;
      if (err.status === 404) {
        window.clearInterval(timer);
        timer = 0;
        form.hidden = false;
      }
    }
  }

  function hazardText(supplier) {
    if (supplier.hazard == null || supplier.hazard !== supplier.hazard) return "no data";
    const atDefault = (Math.round(Number(supplier.hazard) * 1000) / 10).toFixed(1);
    const atPush = supplier.hazard_upper == null
      ? "no data"
      : (Math.round(Number(supplier.hazard_upper) * 1000) / 10).toFixed(1) + "%";
    return "incremental " + atDefault + "% at the default counter, " + atPush + " at the push-lower counter";
  }

  function show(snapshot) {
    board.hidden = false;
    const deal = snapshot.deal || {};
    dealProduct = String(deal.product || "");
    dealCurrency = String(deal.currency || "CNY");
    document.getElementById("deal-line").textContent =
      (deal.product || "Deal") + " · " + (deal.id || "");
    document.getElementById("basis-line").textContent =
      "Ranking basis: " + (snapshot.ranking_basis || "unit_price") + ". " + (snapshot.kappa_note || "");
    const auditLine = document.getElementById("audit-line");
    auditLine.textContent = snapshot.audit || "";
    if (snapshot.anchor_pending) {
      const pending = "anchor did not record this send; re-anchor, then approve this draft again";
      auditLine.textContent = auditLine.textContent ? auditLine.textContent + " " + pending : pending;
    }
    const shadow = snapshot.shadow_gate || {};
    document.getElementById("shadow-line").textContent =
      "Shadow accepts later confirmed unchanged: "
      + String(shadow.confirmed_unchanged || 0)
      + " of " + String(shadow.resolved_shadow_accepts || 0)
      + ", toward " + String(shadow.target || "")
      + ". Auto-accept is off.";
    const headline = snapshot.headline || {};
    const headlineLine = document.getElementById("headline-line");
    if (headline.simulated) {
      headlineLine.textContent = "Headline: simulated. Not counted as a real closed deal.";
    } else if (headline.counted && headline.saving != null) {
      headlineLine.textContent = "Realised landed saving versus the best first confirmed quote: " + money(headline.saving, dealCurrency);
    } else {
      headlineLine.textContent = "Headline: no real closed deal yet.";
    }
    document.getElementById("heuristic-line").textContent = snapshot.heuristic || "";
    document.getElementById("hazard-line").textContent = snapshot.hazard_note || "";
    fillAssumptions(snapshot);
    const cvar = document.getElementById("cvar-line");
    if (snapshot.mc && snapshot.mc.cvar_cost != null) {
      let text = "CVaR " + money(snapshot.mc.cvar_cost, dealCurrency);
      if (snapshot.mc.tail_saturated_at_ceiling) text += " — tail saturated at ceiling";
      cvar.textContent = text;
    } else {
      cvar.textContent = "";
    }
    renderCards(snapshot.suppliers || []);
    const reviewItems = snapshot.review || [];
    const reviewSig = JSON.stringify(reviewItems.map((item) => [
      item.id, Boolean((item.provenance || {}).extractor_pending), (item.provenance || {}).suggested_token || "",
    ]));
    const reviewRoot = document.getElementById("review");
    const typing = reviewRoot.contains(document.activeElement) &&
      ["INPUT", "SELECT", "TEXTAREA"].indexOf(document.activeElement.tagName) >= 0;
    // Someone is typing in a waiting quote and nothing there changed: keep the
    // fields (and the caret) instead of redrawing them under the cursor.
    if (!(typing && reviewSig === lastReviewSig)) {
      renderReview(reviewItems, snapshot.suppliers || []);
      lastReviewSig = reviewSig;
    }
    renderEvents(snapshot.events || []);
    renderDrafts(snapshot.drafts || [], snapshot.suppliers || []);
    renderPasteSuppliers(snapshot.suppliers || []);
    board.dataset.deskSeq = String(Number(board.dataset.deskSeq || "0") + 1);
  }

  function rateText(value) {
    if (value == null || value !== value) return "no data";
    return String(Math.round(value * 1000) / 10) + "%";
  }

  function fillOnce(id, value) {
    const field = document.getElementById(id);
    if (!field || field.dataset.filled === "yes" || value == null) return;
    field.value = String(value);
    field.dataset.filled = "yes";
  }

  function fillAssumptions(snapshot) {
    const box = snapshot.hazard_assumptions || {};
    const deal = snapshot.deal || {};
    fillOnce("hazard-intercept", box.intercept);
    fillOnce("hazard-tau", box.tau);
    fillOnce("hazard-quote-gap", box.quote_gap);
    fillOnce("hazard-reserve-gap", box.reserve_gap);
    fillOnce("obs-noise", deal.bayes_obs_noise);
    fillOnce("sanity-multiple", deal.unit_sanity_multiple);
    fillOnce("reserve-prior", deal.reserve_prior_mean);
    fillOnce("reserve-std", deal.reserve_prior_std);
    fillOnce("expected-markup", deal.expected_markup);
    fillOnce("search-cost", deal.search_cost);
    const note = document.getElementById("hazard-sensitivity");
    if (note) {
      note.textContent = "Transferred baseline walk-away " + rateText(box.baseline_rate)
        + " (spread " + rateText(box.baseline_low) + " to " + rateText(box.baseline_high)
        + "). Source: " + (box.source || "ebay_seller_ended")
        + ". A reserve-gap term is not added. Recorded outcomes: " + String(box.recorded_outcomes || 0)
        + ". They update this supplier's effect only.";
    }
  }

  function outcomeButton(label, outcome, supplierId) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", async () => {
      boardError.textContent = "";
      try {
        const snapshot = await request(
          "/api/v1/desk/deals/" + dealId + "/suppliers/" + supplierId + "/outcome",
          {
            method: "POST",
            headers: headers(true),
            body: JSON.stringify({ outcome: outcome }),
          }
        );
        show(snapshot);
      } catch (err) {
        boardError.textContent = err.message;
      }
    });
    return button;
  }

  const assumptions = document.getElementById("assumptions");
  if (assumptions) {
    assumptions.addEventListener("submit", async (event) => {
      event.preventDefault();
      boardError.textContent = "";
      const payload = {};
      const take = (id, key) => {
        const field = document.getElementById(id);
        if (!field || field.value.trim() === "") return;
        payload[key] = Number(field.value);
      };
      take("hazard-intercept", "hazard_intercept");
      take("hazard-tau", "hazard_tau");
      take("hazard-quote-gap", "hazard_quote_gap");
      take("hazard-reserve-gap", "hazard_reserve_gap");
      take("obs-noise", "bayes_obs_noise");
      take("sanity-multiple", "unit_sanity_multiple");
      take("reserve-prior", "reserve_prior_mean");
      take("reserve-std", "reserve_prior_std");
      take("expected-markup", "expected_markup");
      take("search-cost", "search_cost");
      const include = document.getElementById("include-unverified");
      if (include) payload.include_unverified = include.checked;
      try {
        const snapshot = await request("/api/v1/desk/deals/" + dealId + "/assumptions", {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify(payload),
        });
        show(snapshot);
      } catch (err) {
        boardError.textContent = err.message;
      }
    });
  }

  // Minimum order and lead time, with the words they came from. Shown only:
  // they never rank or price. An MOQ above the order is flagged.
  function termsLines(card, supplier) {
    const quoted = (spans) => spans.map((span) => "\u201c" + span + "\u201d").join(" / ");
    const moq = supplier.supplier_moq;
    if (moq && moq.status === "conflict") {
      line(card, "Minimum order: the message gives more than one (" + quoted(moq.spans || []) + ").").className =
        "desk-validity desk-warn";
    } else if (moq) {
      const amount = Number(moq.quantity).toLocaleString();
      const text = supplier.moq_above_order
        ? "Minimum order " + amount + " (" + quoted([moq.span]) + ") is above your order. It is still ranked."
        : "Minimum order: " + amount + " (" + quoted([moq.span]) + ").";
      line(card, text).className = supplier.moq_above_order ? "desk-validity desk-warn" : "desk-validity";
    }
    const lead = supplier.supplier_lead_time;
    if (lead && lead.status === "conflict") {
      line(card, "Lead time: the message gives more than one (" + quoted(lead.spans || []) + ").").className =
        "desk-validity desk-warn";
    } else if (lead && lead.status === "working_days") {
      line(card, "Lead time in working days: " + quoted([lead.span]) + ".").className = "desk-validity";
    } else if (lead) {
      line(card, "Lead time: " + lead.days + " days (" + quoted([lead.span]) + ").").className = "desk-validity";
    }
  }

  function renderCards(suppliers) {
    const root = document.getElementById("cards");
    clear(root);
    const ranked = suppliers.slice().sort((a, b) => {
      const ar = a.rank == null ? 99 : a.rank;
      const br = b.rank == null ? 99 : b.rank;
      return ar - br;
    });
    ranked.forEach((supplier) => {
      const card = document.createElement("article");
      card.className = "desk-card";
      card.dataset.supplier = supplier.id || "";
      const title = document.createElement("h2");
      const rank = supplier.rank == null ? "—" : String(supplier.rank);
      title.textContent = rank + ". " + (supplier.name || "Supplier");
      card.appendChild(title);
      const quote = supplier.latest_quote == null
        ? "no quote"
        : money(supplier.latest_quote, supplier.currency || dealCurrency);
      const origin = supplier.origin || "live";
      // A live record is the normal case; only say so when it is not.
      if (origin !== "live") line(card, "Record: " + origin);
      line(card, "Latest quote: " + quote + (supplier.quote_typed_words
        ? " (typed by you from \u201c" + supplier.quote_typed_words + "\u201d)" : ""));
      if (supplier.latest_quote != null) {
        const validity = validityText(supplier, new Date());
        const validityLine = line(card, validity.text);
        validityLine.className = validity.warn ? "desk-validity desk-warn" : "desk-validity";
        termsLines(card, supplier);
      }
      if (supplier.quote_confirmed === false) {
        line(card, "This quote is not confirmed.");
      }
      if (supplier.landed_status === "complete" && supplier.landed_cost != null) {
        line(card, "Landed cost: " + money(supplier.landed_cost, dealCurrency));
      }
      const priorMean = supplier.d_prior_mean == null ? "labelled policy" : String(supplier.d_prior_mean);
      const priorStrength = supplier.d_prior_strength == null ? "labelled policy" : String(supplier.d_prior_strength);
      if (supplier.needs_keep_confirmation) {
        line(card, "This supplier was not on the chain before migration. Confirm to keep them.");
        const keptAddress = document.createElement("input");
        keptAddress.type = "text";
        keptAddress.placeholder = "Address to keep";
        const keep = document.createElement("button");
        keep.type = "button";
        keep.textContent = "Keep supplier";
        keep.addEventListener("click", async () => {
          boardError.textContent = "";
          try {
            const snapshot = await request(
              "/api/v1/desk/deals/" + dealId + "/suppliers/" + supplier.id + "/keep",
              {
                method: "POST",
                headers: headers(true),
                body: JSON.stringify({ human_confirmed: true, address: keptAddress.value }),
              }
            );
            show(snapshot);
          } catch (err) {
            boardError.textContent = err.message;
          }
        });
        card.appendChild(keptAddress);
        card.appendChild(keep);
      }
      const half = supplier.sensitivity_half == null ? "withheld" : money(supplier.sensitivity_half, dealCurrency);
      const doubled = supplier.sensitivity_double == null ? "withheld" : money(supplier.sensitivity_double, dealCurrency);
      const defaultFigure = supplier.default_counter != null ? supplier.default_counter : supplier.counter;
      const counter = defaultFigure == null ? "withheld" : money(defaultFigure, dealCurrency);
      const push = supplier.push_counter == null ? "withheld" : money(supplier.push_counter, dealCurrency);
      const pushing = supplier.counter_relation === "push_lower";
      const relation = supplier.counter_relation === "at_ask"
        ? "This supplier is the cheapest confirmed quote, so the default is their ask."
        : supplier.counter_relation === "match_rival"
          ? "The default matches the best other confirmed landed quote."
          : "";
      line(card, "Default counter: " + counter);
      if (relation) line(card, relation);
      line(card, "Push lower: " + push + " (the solver's price with the highest expected surplus inside your risk cap)");
      if (supplier.push_note) line(card, supplier.push_note);
      if (supplier.counter != null) {
        const carries = line(card, "The draft carries: " + money(supplier.counter, dealCurrency) +
          (pushing ? " (push lower, chosen by you)" : " (the default)"));
        carries.className = "desk-carries";
        const choose = document.createElement("button");
        choose.type = "button";
        const next = supplier.offer_mode === "push" ? "default" : "push";
        choose.textContent = next === "push" ? "Use push lower in the draft" : "Use the default in the draft";
        choose.disabled = next === "push" && supplier.push_counter == null;
        // The default for the cheapest supplier is their own ask, so the draft
        // would simply accept it; push lower is the figure to negotiate with.
        if (next === "push" && supplier.counter_relation === "at_ask" && !choose.disabled) {
          choose.classList.add("desk-emphasis");
          choose.title = "The default is this supplier's own ask. Push lower is the figure to negotiate with.";
        }
        choose.addEventListener("click", async () => {
          boardError.textContent = "";
          choose.disabled = true;
          try {
            const snapshot = await request(
              "/api/v1/desk/deals/" + dealId + "/suppliers/" + supplier.id + "/offer-mode",
              {
                method: "POST",
                headers: headers(true),
                body: JSON.stringify({ mode: next, human_confirmed: true }),
              }
            );
            show(snapshot);
          } catch (err) {
            boardError.textContent = err.message;
            choose.disabled = false;
          }
        });
        const chooseRow = document.createElement("div");
        chooseRow.className = "desk-actions";
        chooseRow.appendChild(choose);
        card.appendChild(chooseRow);
      }
      line(card, "Walk-away hazard: " + hazardText(supplier));
      // The model's working, for whoever wants it. Closed by default; stays
      // open across the redraw on every poll once a person opens it.
      const workings = document.createElement("details");
      workings.className = "desk-workings";
      workings.open = workingsOpen.has(supplier.id);
      workings.addEventListener("toggle", () => {
        if (workings.open) workingsOpen.add(supplier.id);
        else workingsOpen.delete(supplier.id);
      });
      const summary = document.createElement("summary");
      summary.textContent = "How these figures were worked out";
      workings.appendChild(summary);
      if (origin === "live") line(workings, "Record: live");
      line(workings, "D prior (user policy): mean " + priorMean + ", strength " + priorStrength);
      line(workings, "Reserve: " + (supplier.reserve_note || supplier.belief_label || "no data"));
      line(workings, "At half the prior strength: " + half);
      line(workings, "At double the prior strength: " + doubled);
      card.appendChild(workings);
      if (supplier.excluded_from_decision) {
        line(card, "Excluded from the decision until you include unverified quotes.");
      }
      const actions = document.createElement("div");
      actions.className = "desk-actions";
      actions.appendChild(outcomeButton("Record continue", "continue", supplier.id));
      actions.appendChild(outcomeButton("Record walk-away", "walk_away", supplier.id));
      card.appendChild(actions);
      card.appendChild(evidenceBlock(supplier));
      root.appendChild(card);
    });
  }

  function paintEvidence(wrap, entry) {
    const lib = window.TradeFlowWebEvidence;
    const button = wrap.querySelector("button");
    const parts = {
      list: wrap.querySelector("ul"),
      state: wrap.querySelector(".desk-evidence-state"),
      prov: wrap.querySelector(".desk-evidence-prov"),
    };
    if (!lib) {
      parts.state.textContent = "Web evidence is not loaded.";
      button.disabled = true;
      return;
    }
    button.disabled = !!(entry && entry.loading);
    if (entry && entry.loading) lib.loading(parts);
    else lib.paintInto(parts, entry.payload);
  }

  function repaintEvidence(supplierId) {
    document.querySelectorAll(".desk-evidence").forEach((wrap) => {
      if (wrap.dataset.evidence === supplierId) paintEvidence(wrap, evidence.get(supplierId));
    });
  }

  function evidenceBlock(supplier) {
    const id = String(supplier.id || "");
    const wrap = document.createElement("div");
    wrap.className = "desk-evidence";
    wrap.dataset.evidence = id;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Check supplier on the web";
    const state = document.createElement("p");
    state.className = "desk-evidence-state";
    state.setAttribute("role", "status");
    const list = document.createElement("ul");
    list.className = "web-evidence-list";
    const prov = document.createElement("p");
    prov.className = "desk-evidence-prov";
    wrap.appendChild(button);
    wrap.appendChild(state);
    wrap.appendChild(list);
    wrap.appendChild(prov);
    button.addEventListener("click", async () => {
      const lib = window.TradeFlowWebEvidence;
      if (!lib) return;
      evidence.set(id, { loading: true, payload: null });
      repaintEvidence(id);
      const payload = await lib.lookup({
        kind: "supplier",
        product: dealProduct,
        supplier: String(supplier.name || ""),
        headers: () => headers(false),
      });
      evidence.set(id, { loading: false, payload: payload });
      repaintEvidence(id);
    });
    if (evidence.has(id)) paintEvidence(wrap, evidence.get(id));
    return wrap;
  }

  function formatWhen(value) {
    const text = String(value || "");
    const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    if (!match) return text;
    return match[1] + " " + match[2] + " UTC";
  }

  // What the model did with a message: which model, how long, and the spans it
  // labelled. Spans are verbatim supplier text; no amount comes from the model.
  const SPAN_LABELS = {
    own_unit_offer: "Supplier's offer",
    rival_price: "Rival's price",
    our_price_echo: "Your figure, echoed",
    refusal: "Refusal",
    question: "Question",
    discount_delta: "Discount",
    fee: "Fee",
    deposit: "Deposit",
    tier_table: "Tier table",
    instruction: "Instruction (ignored)",
    other: "Other",
  };

  function readerSummary(provenance) {
    if (provenance.extractor_pending) return "The model is reading this message\u2026";
    if (provenance.extractor_error === "extractor_daily_cap") {
      return "Today's model budget on this server is spent. Read by the rule reader only.";
    }
    if (provenance.extractor_error === "extractor_paused") {
      return "Model calls are paused: the model service refused this server's key or its credit is spent. " +
        "Read by the rule reader only.";
    }
    if (provenance.extractor_error === "extractor_account") {
      const refused = (provenance.extractor_attempts || []).map((a) => a && a.status).filter(Boolean).pop() || "";
      return "The model service refused this server's key or its credit is spent" +
        (refused ? " (" + refused.replace("http_", "HTTP ") + ")" : "") +
        ". Model calls pause for a while; read by the rule reader only.";
    }
    const attempts = provenance.extractor_attempts || [];
    let used = null;
    attempts.forEach((attempt) => { if (attempt && attempt.status === "ok") used = attempt; });
    if (!attempts.length) return "Read by the rule reader only.";
    if (!used) return "The model did not answer (" + String(attempts[attempts.length - 1].status || "failed") + "). Rule reader only.";
    const seconds = Number(used.latency_s);
    const took = Number.isFinite(seconds) ? " in " + seconds.toFixed(1) + " s" : "";
    const where = used.provider === "nebius" ? "Nebius Token Factory" : String(used.provider || "");
    const tries = attempts.length > 1 ? " after " + String(attempts.length - 1) + " failed attempt" + (attempts.length > 2 ? "s" : "") : "";
    return "Read by " + String(used.model || "a model") + " on " + where + took + tries + ".";
  }

  // The reader's review reasons, in words a buyer reads at a glance. The
  // original reason stays in the line's tooltip and in the record.
  const PLAIN_REASONS = {
    "price range": "The message gives a price range, not one price. Pick the figure the supplier will hold, or ask them.",
    "more than one price": "The message has more than one price (a rival's, or yours repeated back). Pick the supplier's own offer.",
    "no currency-anchored price": "No price in the message names a currency. Check the figure is a unit price before you pick it.",
    "no usable amount in the deal currency": "The reader found no usable unit price. Pick a figure only if it is the supplier's unit price.",
    "the reader failed closed": "The reader could not read this message safely. Pick the figure yourself.",
    "confirm the prefilled amount": "One price found. Check it and confirm.",
    "shadow gate would accept; confirm the prefilled amount": "The rules and the model agree on this price. Check it and confirm.",
  };

  function plainReason(reason, currency) {
    const text = String(reason || "");
    if (text === "not the deal currency") {
      return "No price is clearly marked in the deal's currency (" + (currency || "CNY") + "). " +
        "Colloquial 块 and bare numbers land here. Check the figure is in " + (currency || "CNY") + " before you pick it.";
    }
    return PLAIN_REASONS[text] || text;
  }

  function readerTrace(provenance) {
    const wrap = document.createElement("div");
    wrap.className = "desk-trace";
    line(wrap, readerSummary(provenance || {})).className = "desk-trace-head";
    const mentions = (provenance && provenance.extractor_mentions) || [];
    if (mentions.length) {
      const list = document.createElement("ul");
      list.className = "desk-trace-spans";
      mentions.forEach((mention) => {
        const item = document.createElement("li");
        item.dataset.spanClass = String(mention.class || "other");
        const label = document.createElement("span");
        label.className = "desk-trace-label";
        let text = SPAN_LABELS[mention.class] || String(mention.class || "Other");
        if (mention.is_question) text += " (question)";
        if (mention.polarity === "negative") text += " (negative)";
        label.textContent = text;
        const span = document.createElement("q");
        span.textContent = String(mention.span || "");
        item.appendChild(label);
        item.appendChild(span);
        list.appendChild(item);
      });
      wrap.appendChild(list);
    }
    if (provenance && provenance.shadow_would_accept) {
      line(wrap, "The rules and the model agree on the amount. You still confirm it.").className = "desk-trace-agree";
    }
    return wrap;
  }

  function renderReview(items, suppliers) {
    const root = document.getElementById("review");
    clear(root);
    if (!items.length) {
      line(root, "Nothing waiting.");
      return;
    }
    items.forEach((item) => {
      const saved = reviewChoice.get(item.id) || {};
      const block = document.createElement("div");
      block.className = "desk-event";
      block.dataset.event = item.id;
      const reason = (item.provenance && item.provenance.review_reason) || item.confidence || "review";
      const reasonLine = line(block, (item.source || "quote") + " · " + plainReason(reason, dealCurrency));
      reasonLine.title = "Reader's reason: " + reason;
      const pre = document.createElement("pre");
      pre.textContent = item.raw_text || "";
      block.appendChild(pre);
      block.appendChild(readerTrace(item.provenance || {}));
      const provenance = item.provenance || {};
      const tokens = provenance.candidates && provenance.candidates.length
        ? provenance.candidates
        : (provenance.tokens || []);
      const suggested = provenance.suggested_token || "";
      const currency = provenance.currency || dealCurrency;
      // A figure the person picked stays. A pre-selection they did not touch
      // follows the latest suggestion, so a model result that arrives after
      // the first draw can still point at the supplier's own offer.
      let chosen = (saved.token && !saved.auto) ? saved.token : (suggested || saved.token || "");
      if (chosen && tokens.indexOf(chosen) < 0) chosen = "";
      if (chosen && (!saved.token || saved.auto)) {
        reviewChoice.set(item.id, {
          supplierId: saved.supplierId || item.supplier_id || "", token: chosen, auto: true,
        });
      }
      const tier = provenance.tier_pick;
      if (tier && tier.token && tokens.indexOf(tier.token) >= 0) {
        const qty = Number(tier.quantity);
        const units = Number.isFinite(qty) ? qty.toLocaleString() : "your quantity";
        const tierText = tier.below_smallest
          ? "Tier table: your order (" + units + ") is below the smallest row, \u201c" + tier.row +
            "\u201d. Check the MOQ with the supplier before you accept " + tier.token + "."
          : "Tier table: for " + units + " the row is \u201c" + tier.row + "\u201d, so " + tier.token +
            " is pre-selected. You confirm it.";
        line(block, tierText).className = tier.below_smallest ? "desk-model-hint desk-disagree" : "desk-model-hint";
      }
      const modelToken = provenance.model_offer_token || "";
      if (modelToken && tokens.indexOf(modelToken) >= 0) {
        const said = provenance.model_offer_span ? " (\u201c" + provenance.model_offer_span + "\u201d)" : "";
        const hint = provenance.model_disagrees
          ? "The model calls " + modelToken + " the supplier's own offer" + said +
            ", but the rules read " + suggested + ". Check before you accept."
          : provenance.suggested_by === "model"
            ? "Pre-selected " + modelToken + ": the model labelled it the supplier's own offer" + said +
              ". The rules did not pick one. You confirm it."
            : "The model agrees: it labelled " + modelToken + " the supplier's own offer" + said + ".";
        const hintLine = line(block, hint);
        hintLine.className = provenance.model_disagrees ? "desk-model-hint desk-disagree" : "desk-model-hint";
      }
      const note = document.createElement("p");
      function lineContaining(raw, token) {
        const lines = String(raw || "").split(/\r?\n/);
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i];
          if (token && line.indexOf(token) >= 0 && line.trim()) return line.trim();
        }
        return "";
      }
      function showConfirm(token) {
        if (!token) {
          note.hidden = true;
          note.textContent = "";
          delete note.dataset.confirmValue;
          delete note.dataset.confirmSpan;
          return;
        }
        const mapped = provenance.confirm_spans && provenance.confirm_spans[token];
        const span = mapped || lineContaining(item.raw_text, token);
        note.hidden = false;
        note.dataset.confirmValue = token;
        note.dataset.confirmSpan = span;
        note.textContent = "Confirming " + token + " " + currency + " from \u201c" + span + "\u201d.";
      }
      showConfirm(chosen);
      block.appendChild(note);
      if (tokens.length) {
        const picks = document.createElement("div");
        picks.className = "desk-actions";
        tokens.forEach((token) => {
          const pick = document.createElement("button");
          pick.type = "button";
          pick.textContent = token;
          if (token === modelToken) {
            pick.dataset.model = "own_unit_offer";
            pick.title = "The model labelled this figure the supplier's own offer";
          }
          if (token === chosen) pick.disabled = true;
          pick.setAttribute("aria-pressed", token === chosen ? "true" : "false");
          pick.addEventListener("click", () => {
            chosen = token;
            reviewChoice.set(item.id, { supplierId: select.value, token: token });
            showConfirm(chosen);
            button.disabled = !chosen;
            Array.prototype.forEach.call(picks.children, (node) => {
              node.disabled = node === pick;
              node.setAttribute("aria-pressed", node === pick ? "true" : "false");
            });
          });
          picks.appendChild(pick);
        });
        block.appendChild(picks);
      }
      const select = document.createElement("select");
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Choose a supplier";
      select.appendChild(blank);
      suppliers.forEach((supplier) => {
        const option = document.createElement("option");
        option.value = supplier.id;
        option.textContent = supplier.name;
        if (item.supplier_id && item.supplier_id === supplier.id) option.selected = true;
        select.appendChild(option);
      });
      if (saved.supplierId) select.value = saved.supplierId;
      select.addEventListener("change", () => {
        const prev = reviewChoice.get(item.id) || {};
        reviewChoice.set(item.id, { supplierId: select.value, token: prev.token || chosen, auto: Boolean(prev.auto) });
      });
      const actions = document.createElement("div");
      actions.className = "desk-actions";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Accept";
      button.disabled = !chosen;
      button.addEventListener("click", () => {
        if (!chosen || !note.dataset.confirmValue) return;
        accept(item.id, select.value, note.dataset.confirmValue, false, block);
      });
      const reject = document.createElement("button");
      reject.type = "button";
      reject.textContent = "Reject";
      reject.addEventListener("click", () => accept(item.id, select.value, chosen, true, block));
      actions.appendChild(select);
      actions.appendChild(button);
      actions.appendChild(reject);
      block.appendChild(actions);
      block.appendChild(typedRow(item, select, block));
      root.appendChild(block);
    });
  }

  // When the figure is not a clean number in the message (28塊5, 二十八块五),
  // a person types it and names the words it comes from. The desk records it
  // as typed, and those words must stay in the message for the send check.
  function typedRow(item, select, block) {
    const kept = typedDrafts.get(item.id) || {};
    const wrap = document.createElement("details");
    wrap.className = "desk-typed";
    wrap.open = Boolean(kept.open);
    wrap.addEventListener("toggle", () => {
      typedDrafts.set(item.id, Object.assign({}, typedDrafts.get(item.id) || {}, { open: wrap.open }));
    });
    const summary = document.createElement("summary");
    summary.textContent = "The price is not one of these figures? Type it";
    wrap.appendChild(summary);
    const amount = document.createElement("input");
    amount.type = "number";
    amount.min = "0";
    amount.step = "any";
    amount.placeholder = "Unit price, as a number";
    amount.setAttribute("aria-label", "Typed unit price");
    const words = document.createElement("input");
    words.type = "text";
    words.maxLength = 80;
    words.placeholder = "Words it comes from, e.g. 28塊5";
    words.setAttribute("aria-label", "Words in the message the price comes from");
    amount.value = kept.amount || "";
    words.value = kept.words || "";
    const keep = () => typedDrafts.set(item.id, { open: wrap.open, amount: amount.value, words: words.value });
    amount.addEventListener("input", keep);
    words.addEventListener("input", keep);
    const go = document.createElement("button");
    go.type = "button";
    go.textContent = "Accept typed price";
    const note = document.createElement("p");
    note.className = "desk-note";
    note.textContent = "Recorded as typed by you. The words must appear in the message exactly.";
    go.addEventListener("click", async () => {
      const value = Number(amount.value);
      if (!(value > 0) || !words.value.trim()) {
        note.textContent = "Type a price above zero and the words it comes from.";
        return;
      }
      if (String(item.raw_text || "").indexOf(words.value.trim()) < 0) {
        note.textContent = "Those words are not in the message. Copy them exactly.";
        return;
      }
      try {
        const snapshot = await request("/api/v1/desk/deals/" + dealId + "/review/" + item.id, {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify({
            action: "accept", supplier_id: select.value || null, typed_amount: value, typed_words: words.value.trim(),
          }),
        });
        reviewChoice.delete(item.id);
        typedDrafts.delete(item.id);
        show(snapshot);
      } catch (err) {
        note.textContent = err.message;
      }
    });
    const row = document.createElement("div");
    row.className = "desk-actions";
    row.appendChild(amount);
    row.appendChild(words);
    row.appendChild(go);
    wrap.appendChild(row);
    wrap.appendChild(note);
    return wrap;
  }

  async function accept(eventId, supplierId, chosenToken, reject, block) {
    const payload = { action: reject ? "reject" : "accept" };
    if (!reject) payload.supplier_id = supplierId || null;
    if (chosenToken) payload.chosen_token = chosenToken;
    try {
      const snapshot = await request("/api/v1/desk/deals/" + dealId + "/review/" + eventId, {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify(payload),
      });
      reviewChoice.delete(eventId);
      show(snapshot);
    } catch (err) {
      line(block, err.message);
    }
  }

  function renderEvents(items) {
    const root = document.getElementById("events");
    clear(root);
    if (!items.length) {
      line(root, "No quotes yet.");
      return;
    }
    items.forEach((item) => {
      const block = document.createElement("div");
      block.className = "desk-event";
      const fixture = item.provenance && item.provenance.fixture ? " · " + item.provenance.fixture : "";
      const shown = item.status === "accepted"
        ? "accepted"
        : (item.status === "rejected" ? "rejected" : "unconfirmed");
      line(block, formatWhen(item.received_at) + " · " + (item.source || "") + " · " + shown + fixture);
      const pre = document.createElement("pre");
      pre.textContent = item.raw_text || "";
      block.appendChild(pre);
      block.appendChild(readerTrace(item.provenance || {}));
      root.appendChild(block);
    });
  }

  function renderDrafts(drafts, suppliers) {
    const root = document.getElementById("drafts");
    clear(root);
    const names = {};
    const channels = {};
    suppliers.forEach((supplier) => {
      names[supplier.id] = supplier.name;
      channels[supplier.id] = supplier.channel || "email";
    });
    const open = drafts.filter((draft) => draft.status !== "superseded");
    if (!open.length) {
      line(root, "No drafts yet.");
      return;
    }
    open.forEach((draft) => {
      const block = document.createElement("div");
      block.className = "desk-event";
      const name = names[draft.supplier_id] || "Supplier";
      const byHand = channels[draft.supplier_id] && channels[draft.supplier_id] !== "email";
      const app = channels[draft.supplier_id] === "whatsapp" ? "WhatsApp"
        : channels[draft.supplier_id] === "wechat" ? "WeChat" : "the chat app";
      line(block, name + " · " + (byHand && draft.status === "approved"
        ? "approved; copy the text and send it in " + app + " yourself"
        : draftState(draft)));
      const area = document.createElement("textarea");
      area.readOnly = true;
      area.rows = 8;
      area.value = draft.body || "";
      block.appendChild(area);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy draft";
      copy.addEventListener("click", () => copyText(draft.body || ""));
      block.appendChild(copy);
      if ((draft.status === "draft" || draft.status === "failed") && !draft.anchor_pending) {
        const send = document.createElement("button");
        send.type = "button";
        send.textContent = byHand ? "Approve (you send it in " + app + ")" : "Approve and send";
        send.addEventListener("click", () => approve(draft.id, area.value, block));
        block.appendChild(send);
      }
      root.appendChild(block);
    });
  }

  function draftState(draft) {
    if (draft.anchor_pending) {
      return "anchor did not record this send; re-anchor, then approve this draft again";
    }
    if (draft.status === "sending") return "sending";
    if (draft.status === "failed") return "send failed" + (draft.send_error ? ": " + draft.send_error : "");
    if (draft.status === "sent" && draft.transmitted) return "handed to the mail server";
    if (draft.status === "sent") return "recorded sent without a mail-server receipt";
    if (draft.status === "approved") return "approved here, not transmitted";
    return "draft, not sent";
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  }

  async function digestHex(text) {
    const bytes = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const view = new Uint8Array(digest);
    let hex = "";
    for (let index = 0; index < view.length; index += 1) {
      hex += view[index].toString(16).padStart(2, "0");
    }
    return hex;
  }

  async function approve(draftId, body, block) {
    const ok = window.confirm("Send this draft? It leaves this desk only after you confirm.");
    if (!ok) return;
    try {
      const bodySha = await digestHex(body);
      await request("/api/v1/desk/drafts/" + draftId + "/send", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({ human_approved: true, body_sha256: bodySha }),
      });
      refresh();
    } catch (err) {
      if (block) line(block, err.message);
      else boardError.textContent = err.message;
    }
  }

  function renderPasteSuppliers(suppliers) {
    const select = document.getElementById("paste-supplier");
    const current = select.value;
    clear(select);
    suppliers.forEach((supplier) => {
      const option = document.createElement("option");
      option.value = supplier.id;
      option.textContent = supplier.name;
      select.appendChild(option);
    });
    if (current) select.value = current;
  }

  // Sample deal and sample message: fictional, from a static file, and a sample
  // message that is sent unchanged is stored as simulated, never as live.
  let sample = null;
  async function loadSample() {
    if (sample) return sample;
    const res = await fetch("/static/samples/desk_sample.json");
    if (!res.ok) throw new Error("The sample could not be loaded.");
    sample = await res.json();
    return sample;
  }

  function showNote(id, text) {
    const note = document.getElementById(id);
    if (!note) return;
    note.textContent = text;
    note.hidden = !text;
  }

  const sampleDeal = document.getElementById("sample-deal");
  if (sampleDeal) sampleDeal.addEventListener("click", async () => {
    try {
      const data = await loadSample();
      const deal = data.deal || {};
      document.getElementById("product").value = String(deal.product || "");
      document.getElementById("quantity").value = String(deal.quantity || "");
      document.getElementById("target").value = String(deal.target || "");
      document.getElementById("ceiling").value = String(deal.ceiling || "");
      document.getElementById("deal-currency").value = String(deal.currency || "CNY");
      document.getElementById("suppliers").value = (deal.suppliers || [])
        .map((row) => String(row[0]) + ", " + String(row[1])).join("\n");
      showNote("sample-deal-note", String(data.label || "") + " Enter the API key, then Open desk.");
    } catch (err) {
      formError.textContent = err.message;
    }
  });

  const sampleMessage = document.getElementById("sample-message");
  if (sampleMessage) sampleMessage.addEventListener("click", async () => {
    try {
      const data = await loadSample();
      const area = document.getElementById("paste-text");
      area.value = String(data.message || "");
      area.dataset.sample = area.value;
      showNote("paste-sample-note", "Fictional sample text. The desk treats it like a pasted quote so you can see the counter; the record says it came from this button.");
    } catch (err) {
      document.getElementById("paste-error").textContent = err.message;
    }
  });

  // Freight, fees and duty go in together or not at all. A blank is "no data",
  // never zero, so a partly filled set would silently rank on the unit price.
  function landedInputs() {
    const ids = ["paste-freight", "paste-fees", "paste-duty"];
    const raw = ids.map((id) => {
      const el = document.getElementById(id);
      return el ? el.value.trim() : "";
    });
    const filled = raw.filter((value) => value !== "").length;
    if (filled === 0) return { values: null };
    if (filled < raw.length) return { error: "Fill freight, fees and duty together (0 counts), or leave all three blank." };
    const numbers = raw.map(Number);
    if (numbers.some((value) => !Number.isFinite(value) || value < 0)) return { error: "Freight, fees and duty must be zero or more." };
    return { values: { freight: numbers[0], fees: numbers[1], duty_rate: numbers[2] / 100 } };
  }

  function clearLandedInputs() {
    ["paste-freight", "paste-fees", "paste-duty"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
  }

  document.getElementById("paste-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("paste-error");
    error.textContent = "";
    const area = document.getElementById("paste-text");
    const isSample = !!area.dataset.sample && area.value === area.dataset.sample;
    const payload = {
      source: "paste",
      raw_text: area.value,
      supplier_id: document.getElementById("paste-supplier").value,
    };
    const landed = landedInputs();
    if (landed.error) {
      error.textContent = landed.error;
      return;
    }
    if (landed.values) Object.assign(payload, landed.values);
    if (isSample) {
      // Treated like any paste so the desk computes a counter. The record names
      // its source; the deal's suppliers are fictional example.test addresses.
      payload.page_title = "TradeFlow sample message (fictional demo text)";
    }
    try {
      const result = await request("/api/v1/desk/deals/" + dealId + "/quotes", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify(payload),
      });
      area.value = "";
      clearLandedInputs();
      delete area.dataset.sample;
      showNote("paste-sample-note", "");
      if (result.snapshot) show(result.snapshot);
    } catch (err) {
      error.textContent = err.message;
    }
  });

  if (dealId) {
    form.hidden = true;
    refresh();
    start();
  }
})();
