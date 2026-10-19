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
    const figure = supplier.hazard_figure === "upper" ? supplier.hazard_upper : supplier.hazard;
    if (figure == null || figure !== figure) return "no data";
    const shown = Math.round(Number(figure) * 1000) / 10;
    const label = supplier.hazard_figure === "upper" ? " upper bound" : "";
    const prior = supplier.prior_driven ? " prior-driven" : "";
    return shown.toFixed(1) + "%" + label + prior;
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
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function apiBase() {
    return typeof API_BASE_URL === "string" && API_BASE_URL ? API_BASE_URL : "http://localhost:8000";
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

  function render(snapshot) {
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
      row.textContent = supplier.name + " counter " + counter + ", hazard " + hazardText(supplier);
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
    const chosen = select.value;
    const match = (snapshot.drafts || []).find((item) => item.supplier_id === chosen && item.status === "draft");
    draft.value = match ? match.body : "";
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
    } catch (err) {
      setStatus("Desk is offline.");
    }
  }

  function bind() {
    const input = document.getElementById("desk-deal-id");
    const keyInput = document.getElementById("desk-api-key");
    const button = document.getElementById("btn-desk-import");
    const copy = document.getElementById("btn-desk-copy");
    const select = document.getElementById("desk-supplier");
    if (!input || !button) return;
    storageGet(["tfDeskDeal", "tfDeskSupplier", "tfApiKey"], (stored) => {
      if (stored.tfDeskDeal) input.value = stored.tfDeskDeal;
      if (stored.tfApiKey && keyInput && !keyInput.value) keyInput.value = stored.tfApiKey;
      if (stored.tfDeskSupplier && select) select.value = stored.tfDeskSupplier;
      refresh();
    });
    input.addEventListener("change", () => {
      storageSet({ tfDeskDeal: input.value.trim() });
      refresh();
    });
    if (keyInput) {
      const saveKey = () => storageSet({ tfApiKey: keyInput.value.trim() });
      keyInput.addEventListener("change", saveKey);
      keyInput.addEventListener("input", saveKey);
    }
    if (select) select.addEventListener("change", () => {
      rememberSupplier(true);
      confirmPage = askedPage;
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
        if (resp.simulated) setStatus("Imported a simulated test message. It stays in review until you accept a figure.");
        else setStatus("Imported the visible message.");
        refresh();
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
