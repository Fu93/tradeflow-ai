(function () {
  const POLL_MS = 2000;
  const reviewChoice = new Map();
  // Web evidence (Tavily) per supplier. The desk redraws every card on each poll, so a
  // result lives here, keyed by supplier id, and is painted into whichever card is on
  // screen. It is reference text: the counter is computed on the server from confirmed
  // quotes only and never reads it.
  const evidence = new Map();
  let dealProduct = "";

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

  function detailMessage(body) {
    if (!body) return "Request failed";
    if (typeof body.detail === "string") return body.detail.replace(/^Value error,\s*/, "");
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg || "invalid").join(" ");
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
  const board = document.getElementById("board");
  let dealId = sessionStorage.getItem("tfDeskDeal") || "";
  let timer = 0;

  function suppliersFrom(text) {
    const rows = text.split("\n").map((item) => item.trim()).filter(Boolean);
    return rows.map((row) => {
      const comma = row.lastIndexOf(",");
      if (comma < 1) return null;
      const address = row.slice(comma + 1).trim();
      const name = row.slice(0, comma).trim();
      if (!address || address.indexOf("@") < 1) return null;
      return { name: name, address: address, channel: "email" };
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.textContent = "";
    const suppliers = suppliersFrom(document.getElementById("suppliers").value);
    if (suppliers.some((item) => !item || !item.name || !item.address)) {
      formError.textContent = "Each line needs a name and an email.";
      return;
    }
    const payload = {
      product: document.getElementById("product").value.trim(),
      quantity: Number(document.getElementById("quantity").value),
      target_price: Number(document.getElementById("target").value),
      ceiling_price: Number(document.getElementById("ceiling").value),
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
      headlineLine.textContent = "Realised landed saving versus the best first confirmed quote: " + money(headline.saving, "CNY");
    } else {
      headlineLine.textContent = "Headline: no real closed deal yet.";
    }
    document.getElementById("heuristic-line").textContent = snapshot.heuristic || "";
    document.getElementById("hazard-line").textContent = snapshot.hazard_note || "";
    fillAssumptions(snapshot);
    const cvar = document.getElementById("cvar-line");
    if (snapshot.mc && snapshot.mc.cvar_cost != null) {
      let text = "CVaR " + money(snapshot.mc.cvar_cost, "CNY");
      if (snapshot.mc.tail_saturated_at_ceiling) text += " — tail saturated at ceiling";
      cvar.textContent = text;
    } else {
      cvar.textContent = "";
    }
    renderCards(snapshot.suppliers || []);
    renderReview(snapshot.review || [], snapshot.suppliers || []);
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
      formError.textContent = "";
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
        formError.textContent = err.message;
      }
    });
    return button;
  }

  const assumptions = document.getElementById("assumptions");
  if (assumptions) {
    assumptions.addEventListener("submit", async (event) => {
      event.preventDefault();
      formError.textContent = "";
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
        formError.textContent = err.message;
      }
    });
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
        : money(supplier.latest_quote, supplier.currency || "CNY");
      line(card, "Record: " + (supplier.origin || "live"));
      line(card, "Latest quote: " + quote);
      if (supplier.quote_confirmed === false) {
        line(card, "This quote is not confirmed.");
      }
      const landed = supplier.landed_status === "complete" && supplier.landed_cost != null
        ? money(supplier.landed_cost, "CNY")
        : "no data";
      line(card, "Landed cost: " + landed);
      const priorMean = supplier.d_prior_mean == null ? "labelled policy" : String(supplier.d_prior_mean);
      const priorStrength = supplier.d_prior_strength == null ? "labelled policy" : String(supplier.d_prior_strength);
      line(card, "D prior (user policy): mean " + priorMean + ", strength " + priorStrength);
      line(card, "Reserve: " + (supplier.reserve_note || supplier.belief_label || "no data"));
      if (supplier.needs_keep_confirmation) {
        line(card, "This supplier was not on the chain before migration. Confirm to keep them.");
        const keptAddress = document.createElement("input");
        keptAddress.type = "text";
        keptAddress.placeholder = "Address to keep";
        const keep = document.createElement("button");
        keep.type = "button";
        keep.textContent = "Keep supplier";
        keep.addEventListener("click", async () => {
          formError.textContent = "";
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
            formError.textContent = err.message;
          }
        });
        card.appendChild(keptAddress);
        card.appendChild(keep);
      }
      const half = supplier.sensitivity_half == null ? "withheld" : money(supplier.sensitivity_half, "CNY");
      const doubled = supplier.sensitivity_double == null ? "withheld" : money(supplier.sensitivity_double, "CNY");
      const counter = supplier.counter == null ? "withheld" : money(supplier.counter, "CNY");
      const push = supplier.push_counter == null ? "withheld" : money(supplier.push_counter, "CNY");
      const relation = supplier.counter_relation === "at_ask"
        ? "This supplier is the cheapest confirmed quote, so the default is their ask."
        : supplier.counter_relation === "match_rival"
          ? "The default matches the best other confirmed landed quote."
          : "";
      line(card, "Default counter: " + counter);
      if (relation) line(card, relation);
      line(card, "Push lower: " + push);
      if (supplier.push_note) line(card, supplier.push_note);
      line(card, "At half the prior strength: " + half);
      line(card, "At double the prior strength: " + doubled);
      line(card, "Walk-away hazard: " + hazardText(supplier));
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
      line(block, (item.source || "quote") + " · " + reason);
      const pre = document.createElement("pre");
      pre.textContent = item.raw_text || "";
      block.appendChild(pre);
      const provenance = item.provenance || {};
      const tokens = provenance.candidates && provenance.candidates.length
        ? provenance.candidates
        : (provenance.tokens || []);
      const suggested = provenance.suggested_token || "";
      const currency = provenance.currency || "CNY";
      let chosen = saved.token || suggested || "";
      if (chosen && tokens.indexOf(chosen) < 0) chosen = "";
      if (chosen && !saved.token) {
        reviewChoice.set(item.id, { supplierId: item.supplier_id || saved.supplierId || "", token: chosen });
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
          if (token === chosen) pick.disabled = true;
          pick.addEventListener("click", () => {
            chosen = token;
            reviewChoice.set(item.id, { supplierId: select.value, token: token });
            showConfirm(chosen);
            button.disabled = !chosen;
            Array.prototype.forEach.call(picks.children, (node) => {
              node.disabled = node === pick;
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
        reviewChoice.set(item.id, { supplierId: select.value, token: prev.token || chosen });
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
      root.appendChild(block);
    });
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
      root.appendChild(block);
    });
  }

  function renderDrafts(drafts, suppliers) {
    const root = document.getElementById("drafts");
    clear(root);
    const names = {};
    suppliers.forEach((supplier) => { names[supplier.id] = supplier.name; });
    const open = drafts.filter((draft) => draft.status !== "superseded");
    if (!open.length) {
      line(root, "No drafts yet.");
      return;
    }
    open.forEach((draft) => {
      const block = document.createElement("div");
      block.className = "desk-event";
      const name = names[draft.supplier_id] || "Supplier";
      line(block, name + " · " + draftState(draft));
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
        send.textContent = "Approve and send";
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
      else formError.textContent = err.message;
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

  document.getElementById("paste-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("paste-error");
    error.textContent = "";
    try {
      const result = await request("/api/v1/desk/deals/" + dealId + "/quotes", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({
          source: "paste",
          raw_text: document.getElementById("paste-text").value,
          supplier_id: document.getElementById("paste-supplier").value,
        }),
      });
      document.getElementById("paste-text").value = "";
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
