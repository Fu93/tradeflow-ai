/* Web evidence (Tavily). Reference only.
 *
 * It lists cited public pages for a product or a supplier. It never feeds the
 * decision: the server stamps every payload reference_only / influences_solver
 * false, and this file renders text only. Every remote string goes through
 * textContent, and a link is set only when it parses as http or https.
 *
 * Two pages use it. The dashboard card is mounted with mount(); the live desk
 * calls lookup() and paintInto() for the supplier card it is drawing.
 */
(function () {
  "use strict";

  const DEFAULT_ENDPOINT = "/api/v1/market/evidence";

  const COPY = {
    en: {
      title: "Web evidence",
      note: "Tavily · reference only",
      supplierLabel: "Supplier name",
      market: "Market prices",
      supplier: "Check supplier",
      caveat: "Search snippets are unverified third-party text. They are not a price quote and do not set the counter.",
      idle: "Search public pages for wholesale prices, MOQ and supplier reviews.",
      loading: "Searching the web…",
      empty: "No public pages found.",
      needProduct: "Type a product name in the box above first.",
      needSupplier: "Type a supplier name first.",
      cached: "cached result",
      source: "Source: Tavily Search API",
      reasons: {
        sign_in: "Sign in with the desk API key to use web evidence.",
        too_many: "Too many lookups. Wait a minute and try again.",
        no_key: "Web evidence is not configured on this server.",
        offline: "Web evidence is switched off on this server.",
        timeout: "The search timed out. Try again.",
        unreachable: "The search service could not be reached.",
        auth_rejected: "The search service rejected this server's key.",
        rate_limited: "The search service is rate limiting this server. Try again later.",
        upstream_error: "The search service had an error. Try again later.",
        malformed: "The search service returned an unreadable answer.",
        bad_request: "That request could not be searched.",
        fallback: "Web evidence is unavailable."
      }
    },
    "zh-Hant": {
      title: "網路佐證",
      note: "Tavily · 僅供參考",
      supplierLabel: "供應商名稱",
      market: "查市場行情",
      supplier: "查供應商",
      caveat: "搜尋摘要是未經查證的第三方文字,不是報價,也不會決定反報價。",
      idle: "搜尋公開網頁,看批發價、起訂量與供應商評價。",
      loading: "正在搜尋網路…",
      empty: "找不到公開網頁。",
      needProduct: "請先在上方輸入產品名稱。",
      needSupplier: "請先輸入供應商名稱。",
      cached: "快取結果",
      source: "來源:Tavily Search API",
      reasons: {
        sign_in: "請用 desk API key 登入後再使用網路佐證。",
        too_many: "查詢太頻繁,請等一分鐘再試。",
        no_key: "這台伺服器尚未設定網路佐證。",
        offline: "這台伺服器已關閉網路佐證。",
        timeout: "搜尋逾時,請再試一次。",
        unreachable: "無法連線到搜尋服務。",
        auth_rejected: "搜尋服務拒絕了這台伺服器的金鑰。",
        rate_limited: "搜尋服務正在限制這台伺服器,請稍後再試。",
        upstream_error: "搜尋服務發生錯誤,請稍後再試。",
        malformed: "搜尋服務回傳了無法讀取的內容。",
        bad_request: "這個請求無法搜尋。",
        fallback: "網路佐證目前無法使用。"
      }
    }
  };

  function q(root, name) {
    return root.querySelector("[data-we='" + name + "']");
  }

  function text(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function langOf() {
    const host = document.getElementById("marketIntel");
    return host && host.dataset.lang === "zh-Hant" ? "zh-Hant" : "en";
  }

  function copy() {
    return COPY[langOf()];
  }

  function safeUrl(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (err) {
      return "";
    }
  }

  function clearNode(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function itemFor(row) {
    const li = document.createElement("li");
    li.className = "web-evidence-item";
    const href = safeUrl(row && row.url);
    const title = document.createElement(href ? "a" : "span");
    title.className = "web-evidence-link";
    text(title, (row && (row.title || row.domain)) || "");
    if (href) {
      title.href = href;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    }
    li.appendChild(title);
    const meta = document.createElement("span");
    meta.className = "web-evidence-meta";
    const bits = [];
    if (row && row.domain) bits.push(String(row.domain));
    if (row && row.published) bits.push(String(row.published));
    text(meta, bits.join(" · "));
    li.appendChild(meta);
    if (row && row.snippet) {
      const snippet = document.createElement("p");
      snippet.className = "web-evidence-snippet";
      text(snippet, row.snippet);
      li.appendChild(snippet);
    }
    return li;
  }

  /* parts = { list: <ul>, state: <p>, prov: <p> }. A null payload means idle. */
  function paintInto(parts, payload) {
    const labels = copy();
    clearNode(parts.list);
    text(parts.prov, "");
    if (!payload) {
      text(parts.state, labels.idle);
      return;
    }
    if (payload.status === "ok") {
      const rows = Array.isArray(payload.results) ? payload.results : [];
      rows.forEach((row) => parts.list && parts.list.appendChild(itemFor(row)));
      text(parts.state, rows.length ? "" : labels.empty);
      const cached = payload.provenance && payload.provenance.cache === "hit";
      text(parts.prov, labels.source + (cached ? " · " + labels.cached : ""));
      return;
    }
    text(parts.state, labels.reasons[payload.reason] || labels.reasons.fallback);
  }

  function loading(parts) {
    clearNode(parts.list);
    text(parts.prov, "");
    text(parts.state, copy().loading);
  }

  /* One call to the server. It always resolves to a payload: a 401, a 429 and a
   * network failure become explicit unavailable payloads, never a throw. */
  async function lookup(options) {
    let query = "?kind=" + encodeURIComponent(options.kind || "market")
      + "&product=" + encodeURIComponent(options.product || "");
    if (options.supplier) query += "&supplier=" + encodeURIComponent(options.supplier);
    try {
      const headers = options.headers ? options.headers() : {};
      const res = await fetch((options.endpoint || DEFAULT_ENDPOINT) + query, { headers: headers });
      if (res.status === 401) return { status: "unavailable", reason: "sign_in", results: [] };
      if (res.status === 429) return { status: "unavailable", reason: "too_many", results: [] };
      return await res.json();
    } catch (err) {
      return { status: "unavailable", reason: "unreachable", results: [] };
    }
  }

  /* ---- Dashboard card ---- */

  function partsOf(root) {
    return { list: q(root, "list"), state: q(root, "state"), prov: q(root, "prov") };
  }

  function setBusy(root, busy) {
    root.setAttribute("aria-busy", busy ? "true" : "false");
    ["market", "supplier"].forEach((name) => {
      const button = q(root, name);
      if (button) button.disabled = !!busy;
    });
  }

  async function search(root, kind) {
    const labels = copy();
    const state = q(root, "state");
    const product = String(root._product ? root._product() : "").trim();
    const supplierBox = q(root, "supplierInput");
    const supplier = supplierBox ? supplierBox.value.trim() : "";
    if (!product || /^\d{2,6}$/.test(product)) {
      text(state, labels.needProduct);
      return;
    }
    if (kind === "supplier" && !supplier) {
      text(state, labels.needSupplier);
      return;
    }
    const generation = (root._gen || 0) + 1;
    root._gen = generation;
    loading(partsOf(root));
    setBusy(root, true);
    const payload = await lookup({
      endpoint: root.dataset.endpoint,
      kind: kind,
      product: product,
      supplier: kind === "supplier" ? supplier : "",
      headers: root._headers,
    });
    if (root._gen !== generation) return;
    setBusy(root, false);
    root._last = payload;
    paintInto(partsOf(root), payload);
  }

  function applyChrome(root) {
    const labels = copy();
    text(q(root, "title"), labels.title);
    text(q(root, "note"), labels.note);
    text(q(root, "supplierLabel"), labels.supplierLabel);
    text(q(root, "market"), labels.market);
    text(q(root, "supplier"), labels.supplier);
    text(q(root, "caveat"), labels.caveat);
  }

  function mount(root, options) {
    if (!root || root.dataset.mounted === "1") return;
    root.dataset.mounted = "1";
    options = options || {};
    if (options.endpoint) root.dataset.endpoint = options.endpoint;
    root._headers = options.headers || null;
    root._product = options.product || null;
    applyChrome(root);
    paintInto(partsOf(root), null);
    const market = q(root, "market");
    const supplier = q(root, "supplier");
    if (market) market.addEventListener("click", () => search(root, "market"));
    if (supplier) supplier.addEventListener("click", () => search(root, "supplier"));
    const host = document.getElementById("marketIntel");
    if (host && window.MutationObserver) {
      new MutationObserver(() => {
        applyChrome(root);
        paintInto(partsOf(root), root._last || null);
      }).observe(host, { attributes: true, attributeFilter: ["data-lang"] });
    }
  }

  window.TradeFlowWebEvidence = {
    mount: mount,
    lookup: lookup,
    paintInto: paintInto,
    loading: loading,
    copy: COPY,
  };
})();
