(function () {
  const POLL_MS = 2000;

  function headers() {
    const value = { "Content-Type": "application/json" };
    if (typeof TF_API_KEY === "string" && TF_API_KEY) value["X-API-Key"] = TF_API_KEY;
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

  function storageSet(value) {
    if (!globalThis.chrome || !chrome.storage || !chrome.storage.local) return;
    chrome.storage.local.set(value);
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
      item.textContent = "No live desk linked.";
      ranking.appendChild(item);
    }
    suppliers.forEach((supplier) => {
      const item = document.createElement("li");
      const rank = supplier.rank == null ? "—" : String(supplier.rank);
      const quote = supplier.latest_quote == null ? "no quote" : money(supplier.latest_quote, supplier.currency || "CNY");
      item.textContent = rank + ". " + supplier.name + " · " + quote;
      ranking.appendChild(item);
      const row = document.createElement("p");
      const figure = supplier.hazard_figure === "upper" ? supplier.hazard_upper : supplier.hazard;
      const counter = supplier.counter == null ? "withheld" : money(supplier.counter, "CNY");
      const hazard = figure == null ? "no data" : money(figure);
      row.textContent = supplier.name + " counter " + counter + ", hazard " + hazard;
      counters.appendChild(row);
    });
    const current = select.value;
    clear(select);
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

  function rememberSupplier() {
    const select = document.getElementById("desk-supplier");
    if (!select) return;
    storageSet({ tfDeskSupplier: select.value });
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
    const button = document.getElementById("btn-desk-import");
    const copy = document.getElementById("btn-desk-copy");
    const select = document.getElementById("desk-supplier");
    if (!input || !button) return;
    storageGet(["tfDeskDeal", "tfDeskSupplier"], (stored) => {
      if (stored.tfDeskDeal) input.value = stored.tfDeskDeal;
      if (stored.tfDeskSupplier && select) select.value = stored.tfDeskSupplier;
      refresh();
    });
    input.addEventListener("change", () => {
      storageSet({ tfDeskDeal: input.value.trim() });
      refresh();
    });
    if (select) select.addEventListener("change", () => {
      rememberSupplier();
      refresh();
    });
    button.addEventListener("click", () => {
      storageSet({ tfDeskDeal: input.value.trim() });
      rememberSupplier();
      if (!globalThis.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {
        setStatus("Import runs inside the extension.");
        return;
      }
      chrome.runtime.sendMessage({ type: "IMPORT_DESK_QUOTE" }, (resp) => {
        if (chrome.runtime.lastError) {
          setStatus("Import did not reach the page.");
          return;
        }
        if (!resp || !resp.ok) {
          if (resp && resp.reason === "captcha") setStatus("Captcha is on screen. Import paused.");
          else if (resp && resp.reason === "no-deal") setStatus("Choose a deal and a supplier first.");
          else setStatus("No supplier message on screen.");
          return;
        }
        setStatus("Imported the visible message.");
        refresh();
      });
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
