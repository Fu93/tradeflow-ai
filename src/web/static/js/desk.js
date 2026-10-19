(function () {
  const POLL_MS = 2000;

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
        : err.message;
      if (err.status === 404) {
        window.clearInterval(timer);
        timer = 0;
        form.hidden = false;
      }
    }
  }

  function hazardText(supplier) {
    const figure = supplier.hazard_figure === "upper" ? supplier.hazard_upper : supplier.hazard;
    if (figure == null || figure !== figure) return "no data";
    const shown = Math.round(Number(figure) * 1000) / 10;
    const label = supplier.hazard_figure === "upper" ? " upper bound" : "";
    const prior = supplier.prior_driven ? " prior-driven" : "";
    return shown.toFixed(1) + "%" + label + prior;
  }

  function show(snapshot) {
    board.hidden = false;
    const deal = snapshot.deal || {};
    document.getElementById("deal-line").textContent =
      (deal.product || "Deal") + " · " + (deal.id || "");
    document.getElementById("basis-line").textContent =
      "Ranking basis: " + (snapshot.ranking_basis || "unit_price") + ". " + (snapshot.kappa_note || "");
    document.getElementById("hazard-line").textContent = snapshot.hazard_note || "";
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
      line(card, "Latest quote: " + quote);
      const landed = supplier.landed_status === "complete" && supplier.landed_cost != null
        ? money(supplier.landed_cost, "CNY")
        : "no data";
      line(card, "Landed cost: " + landed);
      const belief = money(supplier.belief_mean) + " x target ± " + money(supplier.belief_std);
      line(card, "Reserve belief (" + (supplier.belief_label || "prior") + "): " + belief);
      const counter = supplier.counter == null ? "withheld" : money(supplier.counter, "CNY");
      line(card, "Counter: " + counter);
      line(card, "Walk-away hazard: " + hazardText(supplier));
      root.appendChild(card);
    });
  }

  function renderReview(items, suppliers) {
    const root = document.getElementById("review");
    clear(root);
    if (!items.length) {
      line(root, "Nothing waiting.");
      return;
    }
    items.forEach((item) => {
      const block = document.createElement("div");
      block.className = "desk-event";
      const reason = (item.provenance && item.provenance.review_reason) || item.confidence || "review";
      line(block, (item.source || "quote") + " · " + reason);
      const pre = document.createElement("pre");
      pre.textContent = item.raw_text || "";
      block.appendChild(pre);
      const provenance = item.provenance || {};
      const tokens = provenance.candidates && provenance.candidates.length
        ? provenance.candidates
        : (provenance.tokens || []);
      let chosen = "";
      if (tokens.length) {
        const picks = document.createElement("div");
        picks.className = "desk-actions";
        tokens.forEach((token) => {
          const pick = document.createElement("button");
          pick.type = "button";
          pick.textContent = token;
          pick.addEventListener("click", () => {
            chosen = token;
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
      const actions = document.createElement("div");
      actions.className = "desk-actions";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Accept";
      button.addEventListener("click", () => accept(item.id, select.value, chosen, false, block));
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
      line(block, (item.received_at || "") + " · " + (item.source || "") + " · " + (item.status || "") + fixture);
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
      area.rows = 4;
      area.value = draft.body || "";
      block.appendChild(area);
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy draft";
      copy.addEventListener("click", () => copyText(draft.body || ""));
      block.appendChild(copy);
      if (draft.status === "draft") {
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
