(function () {
  const POLL_MS = 2000;

  let confirmPage = "";
  let askedPage = "";

  function headers() {
    const input = document.getElementById("desk-api-key");
    const key = input && input.value.trim();
    const value = { "Content-Type": "application/json" };
    if (key) value["X-API-Key"] = key;
    return value;
  }

  function hazardText(supplier) {
    if (supplier.hazard == null || supplier.hazard !== supplier.hazard) return "no data";
    const atDefault = (Math.round(Number(supplier.hazard) * 1000) / 10).toFixed(1);
    const atPush = supplier.hazard_upper == null
      ? "no data"
      : (Math.round(Number(supplier.hazard_upper) * 1000) / 10).toFixed(1) + "%";
    return "incremental " + atDefault + "% at the default, " + atPush + " at the push";
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

  const evidence = new Map();
  let dealProduct = "";

  function paintEvidence(wrap, entry) {
    const lib = globalThis.TradeFlowWebEvidence;
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
    [button, state, list, prov].forEach((node) => wrap.appendChild(node));
    button.addEventListener("click", async () => {
      const lib = globalThis.TradeFlowWebEvidence;
      if (!lib) return;
      evidence.set(id, { loading: true, payload: null });
      repaintEvidence(id);
      const payload = await lib.lookup({
        kind: "supplier",
        product: dealProduct,
        supplier: String(supplier.name || ""),
        endpoint: apiBase() + "/api/v1/market/evidence",
        headers: () => headers(),
      });
      evidence.set(id, { loading: false, payload: payload });
      repaintEvidence(id);
    });
    if (evidence.has(id)) paintEvidence(wrap, evidence.get(id));
    return wrap;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  // Fictional suppliers on a reserved test domain. Nothing here is a real company.
  const SAMPLE_DEAL = {
    product: "carbon umbrella",
    quantity: 1000,
    target_price: 12,
    ceiling_price: 16,
    risk_tolerance: 0.5,
    suppliers: [
      { name: "Sample Supplier A", address: "supplier-a@example.test" },
      { name: "Sample Supplier B", address: "supplier-b@example.test" },
      { name: "Sample Supplier C", address: "supplier-c@example.test" },
      { name: "Sample Supplier D", address: "supplier-d@example.test" },
    ],
  };
  const LOCAL_BASE = "http://127.0.0.1:8000";
  let serverBase = "";

  function apiBase() {
    if (serverBase) return serverBase;
    // [FIX 2026-10-25] IPv4 loopback fallback — `localhost` can resolve to
    // ::1 on Windows while the desk server binds IPv4 only.
    return typeof API_BASE_URL === "string" && API_BASE_URL ? API_BASE_URL : "http://127.0.0.1:8000";
  }

  function storageGet(keys, done) {
    if (!globalThis.chrome || !chrome.storage || !chrome.storage.local) {
      done({});
      return;
    }
    chrome.storage.local.get(keys, (stored) => done(stored || {}));
  }

  function storageSet(value, done) {
    if (!globalThis.chrome || !chrome.storage || !chrome.storage.local) {
      if (done) done();
      return;
    }
    chrome.storage.local.set(value, () => { if (done) done(); });
  }

  function setStatus(text) {
    const node = document.getElementById("desk-import-status");
    if (node) node.textContent = text;
  }

  let lastDrawn = "";

  function render(snapshot) {
    // The poll runs every two seconds. Redrawing an unchanged desk would replace the
    // buttons under a person's cursor, so an identical snapshot draws nothing.
    const signature = JSON.stringify(snapshot);
    if (signature === lastDrawn) return;
    lastDrawn = signature;
    dealProduct = String((snapshot.deal && snapshot.deal.product) || "");
    const ranking = document.getElementById("desk-ranking");
    const counters = document.getElementById("desk-counters");
    const draft = document.getElementById("desk-draft");
    const select = document.getElementById("desk-supplier");
    if (!ranking || !counters || !draft || !select) return;
    clear(ranking);
    clear(counters);
    const suppliers = (snapshot.suppliers || []).slice().sort((a, b) => {
      const ar = a.rank == null ? 99 : a.rank;
      const br = b.rank == null ? 99 : b.rank;
      return ar - br;
    });
    if (!suppliers.length) {
      const item = document.createElement("li");
      item.textContent = "No quotes on this deal.";
      ranking.appendChild(item);
    }
    suppliers.forEach((supplier) => {
      const item = document.createElement("li");
      const quote = supplier.latest_quote == null ? "no quote" : money(supplier.latest_quote, supplier.currency || "CNY");
      const label = supplier.rank == null ? supplier.name : String(supplier.rank) + ". " + supplier.name;
      item.textContent = label + " · " + quote;
      ranking.appendChild(item);
      const row = document.createElement("p");
      const counter = supplier.counter == null ? "withheld" : money(supplier.counter, "CNY");
      const half = supplier.sensitivity_half == null ? "withheld" : money(supplier.sensitivity_half, "CNY");
      const doubled = supplier.sensitivity_double == null ? "withheld" : money(supplier.sensitivity_double, "CNY");
      const push = supplier.push_counter == null ? "withheld" : money(supplier.push_counter, "CNY");
      const priorMean = supplier.d_prior_mean == null ? "labelled policy" : String(supplier.d_prior_mean);
      const priorStrength = supplier.d_prior_strength == null ? "labelled policy" : String(supplier.d_prior_strength);
      row.appendChild(document.createTextNode(supplier.name));
      ["Default counter: " + counter,
        "Push lower: " + push,
        supplier.push_note || "",
        "D prior (user policy): mean " + priorMean + ", strength " + priorStrength,
        "At half the prior strength: " + half,
        "At double the prior strength: " + doubled,
        "Hazard: " + hazardText(supplier),
      ].forEach((text) => {
        if (!text) return;
        const part = document.createElement("span");
        part.textContent = text;
        part.style.display = "block";
        row.appendChild(part);
      });
      row.appendChild(evidenceBlock(supplier));
      counters.appendChild(row);
    });
    const current = select.value;
    clear(select);
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose a supplier";
    select.appendChild(blank);
    suppliers.forEach((supplier) => {
      const option = document.createElement("option");
      option.value = supplier.id;
      option.textContent = supplier.name;
      select.appendChild(option);
    });
    if (current) select.value = current;
    renderReview(snapshot);
    const chosen = select.value;
    const match = (snapshot.drafts || []).find((item) => item.supplier_id === chosen && item.status === "draft");
    draft.value = match ? match.body : "";
  }

  function renderReview(snapshot) {
    const root = document.getElementById("desk-review");
    const input = document.getElementById("desk-deal-id");
    const dealId = input && input.value.trim();
    if (!root || !globalThis.TradeFlowDeskReview || !dealId) return;
    globalThis.TradeFlowDeskReview.render(root, snapshot.review || [], snapshot.suppliers || [], {
      post: async (eventId, payload) => {
        const response = await fetch(
          apiBase() + "/api/v1/desk/deals/" + encodeURIComponent(dealId) + "/review/" + encodeURIComponent(eventId),
          { method: "POST", headers: headers(), body: JSON.stringify(payload) }
        );
        if (response.status === 401) throw new Error("The API key was rejected. Enter the key this server expects.");
        if (!response.ok) throw new Error("The desk did not take that (" + response.status + ").");
        return response.json();
      },
      done: (next) => { render(next); setStatus(payload_done(next)); },
      fail: (message) => setStatus(message),
    });
  }

  function payload_done() {
    return "Saved. The counter below is computed from the figure you confirmed.";
  }

  function rememberSupplier(confirmed) {
    const select = document.getElementById("desk-supplier");
    if (!select) return;
    storageSet({
      tfDeskSupplier: select.value,
      tfDeskSupplierConfirmed: !!(confirmed && select.value),
    });
  }

  async function refresh() {
    const input = document.getElementById("desk-deal-id");
    const dealId = input && input.value.trim();
    const empty = document.getElementById("desk-empty");
    if (!dealId) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    try {
      const response = await fetch(apiBase() + "/api/v1/desk/deals/" + dealId, { headers: headers() });
      if (response.status === 401) {
        setStatus("The API key was rejected. Enter the key this server expects.");
        return;
      }
      if (!response.ok) {
        setStatus("Desk request failed.");
        return;
      }
      render(await response.json());
      window.tfDeskLinked = true;
      const status = document.getElementById("desk-import-status");
      const shown = status ? status.textContent : "";
      if (!shown || shown === "Desk linked" || shown === "Desk is offline." || shown.indexOf("Connecting") === 0) {
        setStatus("Desk linked");
      }
    } catch (err) {
      setStatus("Desk is offline.");
    }
  }

  function bind() {
    const input = document.getElementById("desk-deal-id");
    const keyInput = document.getElementById("desk-api-key");
    const button = document.getElementById("btn-desk-import");
    const sample = document.getElementById("btn-desk-sample");
    const copy = document.getElementById("btn-desk-copy");
    const select = document.getElementById("desk-supplier");
    if (!input || !button) return;
    const server = document.getElementById("desk-server");
    storageGet(["tfDeskDeal", "tfDeskSupplier", "tfApiKey", "tfApiBase"], (stored) => {
      const saved = String(stored.tfApiBase || "").trim().replace(/\/+$/, "");
      if (saved && saved !== LOCAL_BASE) {
        serverBase = saved;
        if (server) server.value = saved;
      }
      if (stored.tfDeskDeal) input.value = stored.tfDeskDeal;
      if (stored.tfApiKey && keyInput && !keyInput.value) keyInput.value = stored.tfApiKey;
      if (stored.tfDeskSupplier && select) select.value = stored.tfDeskSupplier;
      refresh();
    });
    if (server) server.addEventListener("change", () => {
      serverBase = server.value || "";
      try { API_BASE_URL = serverBase || LOCAL_BASE; } catch (err) { /* panel script not loaded */ }
      storageSet({ tfApiBase: serverBase });
      setStatus("Connecting\u2026");
      lastDrawn = "";
      refresh();
    });
    input.addEventListener("change", () => {
      storageSet({ tfDeskDeal: input.value.trim() });
      lastDrawn = "";
      refresh();
    });
    if (keyInput) {
      const saveKey = () => storageSet({ tfApiKey: keyInput.value.trim() });
      keyInput.addEventListener("change", saveKey);
      keyInput.addEventListener("input", saveKey);
    }
    if (sample) sample.addEventListener("click", async () => {
      setStatus("Creating a sample deal\u2026");
      try {
        const response = await fetch(apiBase() + "/api/v1/desk/deals", {
          method: "POST",
          headers: headers(),
          body: JSON.stringify(SAMPLE_DEAL),
        });
        if (response.status === 401) {
          setStatus("The API key was rejected. Enter the key this server expects.");
          return;
        }
        if (!response.ok) {
          setStatus("The desk did not create the sample deal (" + response.status + ").");
          return;
        }
        const created = await response.json();
        input.value = created.deal.id;
        storageSet({ tfDeskDeal: input.value, tfApiKey: keyInput ? keyInput.value.trim() : "" });
        await refresh();
        setStatus("Sample deal ready. Open a supplier chat and import its message.");
      } catch (err) {
        setStatus("Desk is offline.");
      }
    });
    if (select) select.addEventListener("change", () => {
      rememberSupplier(true);
      confirmPage = askedPage;
      lastDrawn = "";
      refresh();
    });
    button.addEventListener("click", () => {
      const remembered = { tfDeskDeal: input.value.trim(), tfApiKey: keyInput ? keyInput.value.trim() : "" };
      const send = () => {
      if (!globalThis.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {
        setStatus("Import runs inside the extension.");
        return;
      }
      chrome.runtime.sendMessage({ type: "IMPORT_DESK_QUOTE", confirmPage: confirmPage }, (resp) => {
        if (chrome.runtime.lastError) {
          setStatus("Import did not reach the page.");
          return;
        }
        if (!resp || !resp.ok) {
          if (resp && resp.page) {
            askedPage = resp.page;
            confirmPage = "";
          }
          if (resp && resp.reason === "captcha") setStatus("Captcha is on screen. Import paused.");
          else if (resp && resp.reason === "api-key") setStatus("The API key was rejected. Enter the key this server expects.");
          else if (resp && resp.reason === "no-deal") setStatus("Enter the deal id first.");
          else if (resp && resp.reason === "ambiguous") setStatus("This page matches more than one supplier. Choose one of those threads, then import again.");
          else if (resp && resp.reason === "unmapped") setStatus("This page does not name a supplier on the deal. Choose the thread, then import again.");
          else setStatus("No supplier message on screen.");
          return;
        }
        confirmPage = "";
        askedPage = "";
        const imported = resp.simulated
          ? "Imported a simulated test message. It stays in review until you accept a figure."
          : "Imported the visible message.";
        refresh().then(() => setStatus(imported));
      });
      };
      storageSet(remembered, send);
    });
    if (copy) copy.addEventListener("click", () => {
      const draft = document.getElementById("desk-draft");
      const text = draft ? draft.value : "";
      if (!text) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => setStatus("Copied. Paste it yourself.")).catch(() => setStatus("Select the draft and copy it."));
      }
    });
    window.setInterval(refresh, POLL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
