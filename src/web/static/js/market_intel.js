/* Market intel card. Figures come from the payload. This file has no trade values. */
(function () {
  const COPY = {
    en: {
      title: "Market intel",
      note: "reference only",
      search: "Product or HS code",
      lookUp: "Look up",
      hero: "UK imports from China",
      world: "UK imports from world",
      qty: "Quantity",
      unit: "Implied customs unit value",
      unavailable: "No data from source",
      noData: "No customs figures for this code.",
      prompt: "Enter an HS code or item keyword",
      sparkCaption: "UK import value from China, USD",
      offline: "Offline",
      retry: "Retry",
      refresh: "Refresh",
      cached: "Cached",
      seconds: "seconds ago",
      minutes: "minutes ago",
      hours: "hours ago",
      caveat: "Customs codes can't separate automatic/carbon/custom items",
      notQuote: "Implied customs unit value is not a price quote.",
      suggest: "Suggestions. Confirm one code. A keyword does not fetch.",
      loading: "Loading customs reference",
      source: "Source",
      fetched: "Fetched",
      valuation: "Valuation",
      hs: "HS",
      themeDark: "Dark",
      themeLight: "Light",
      lang: "繁",
      confirm: "Confirm",
      sessionHint: "Refresh to load the world total and earlier years.",
      usd: "USD",
    },
    "zh-Hant": {
      title: "市場對照",
      note: "僅供參考",
      search: "品名或 HS 編碼",
      lookUp: "查詢",
      hero: "英國自中國進口",
      world: "英國自全球進口",
      qty: "數量",
      unit: "海關隱含單價",
      unavailable: "來源沒有資料",
      noData: "這個編碼沒有海關數字。",
      prompt: "輸入 HS 編碼或品名",
      sparkCaption: "英國自中國進口金額，美元",
      offline: "離線",
      retry: "重試",
      refresh: "重新整理",
      cached: "快取",
      seconds: "秒前",
      minutes: "分鐘前",
      hours: "小時前",
      caveat: "海關編碼無法區分自動、碳纖或客製款",
      notQuote: "海關隱含單價不是報價。",
      suggest: "候選編碼。請確認一個代碼。關鍵字不會自動查詢。",
      loading: "正在讀取海關對照",
      source: "來源",
      fetched: "擷取時間",
      valuation: "計價基礎",
      hs: "HS",
      themeDark: "深色",
      themeLight: "淺色",
      lang: "EN",
      confirm: "確認",
      sessionHint: "按重新整理可載入全球合計與較早年度。",
      usd: "USD",
    },
  };

  function text(node, value) {
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function langOf(root) {
    return root.dataset.lang === "zh-Hant" ? "zh-Hant" : "en";
  }

  function copy(root) {
    return COPY[langOf(root)];
  }

  function q(root, name) {
    return root.querySelector("[data-market='" + name + "']");
  }

  function missing(value) {
    return value == null || value === "" || value === "not available";
  }

  function groupAmount(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    const negative = n < 0;
    const abs = Math.abs(n);
    const digits = abs >= 1000 ? 0 : 2;
    const cents = Math.round(abs * (digits === 0 ? 1 : 100));
    const scale = digits === 0 ? 1 : 100;
    const wholeNum = digits === 0 ? cents : Math.floor(cents / scale);
    const frac = digits === 0 ? "" : String(cents % scale).padStart(2, "0");
    const whole = String(wholeNum).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const body = frac && frac !== "00" ? whole + "." + frac : whole;
    return (negative ? "-" : "") + body;
  }

  function agePhrase(seconds, labels) {
    const n = Number(seconds);
    if (!Number.isFinite(n)) return "";
    const rounded = Math.round(n);
    if (rounded < 60) return labels.cached + ", " + rounded + " " + labels.seconds;
    const minutes = Math.floor(rounded / 60);
    if (minutes < 60) return labels.cached + ", " + minutes + " " + labels.minutes;
    const hours = Math.floor(minutes / 60);
    return labels.cached + ", " + hours + " " + labels.hours;
  }

  function normalize(payload) {
    if (!payload || typeof payload !== "object") return null;
    if (payload.view === "card" || payload.hero) return payload;
    const provenance = payload.provenance || {};
    return {
      view: "session",
      status: payload.status,
      reason: payload.reason,
      hs: payload.hs,
      hs_desc: payload.hs_desc,
      hero: { value_usd: payload.value_usd, year: payload.year },
      world: { value_usd: "not available", status: "unavailable", reason: "not_fetched", year: payload.year },
      qty: payload.qty,
      qty_unit: payload.qty_unit,
      unit_value_usd: payload.unit_value_usd,
      valuation: payload.valuation,
      series: [],
      reporter: payload.reporter,
      partner: payload.partner,
      provenance: provenance,
    };
  }

  function applyChrome(root) {
    const labels = copy(root);
    text(q(root, "title"), labels.title);
    text(q(root, "note"), labels.note);
    text(q(root, "searchLabel"), labels.search);
    text(q(root, "submit"), labels.lookUp);
    text(q(root, "refresh"), labels.refresh);
    text(q(root, "retry"), labels.retry);
    const sep = langOf(root) === "zh-Hant" ? "。" : ". ";
    text(q(root, "caveat"), labels.caveat + sep + labels.notQuote);
    const theme = q(root, "theme");
    const dark = document.documentElement.dataset.theme === "dark";
    text(theme, dark ? labels.themeLight : labels.themeDark);
    text(q(root, "lang"), labels.lang);
    const query = q(root, "query");
    if (query) query.setAttribute("aria-label", labels.search);
  }

  function setHero(root, value, plain) {
    const hero = q(root, "hero");
    text(hero, value);
    if (hero) hero.classList.toggle("is-plain", !!plain);
  }

  function setBusy(root, busy) {
    const live = q(root, "live");
    const skel = q(root, "skel");
    if (live) live.setAttribute("aria-busy", busy ? "true" : "false");
    if (skel) skel.hidden = !busy;
    root.dataset.loading = busy ? "1" : "0";
    if (busy) {
      const labels = copy(root);
      setHero(root, labels.loading, true);
      text(q(root, "heroYear"), "");
      text(q(root, "prov"), "");
      const metrics = q(root, "metrics");
      if (metrics) metrics.replaceChildren();
      text(q(root, "state"), "");
    }
  }

  function clearSuggest(root) {
    if (root && root._suggestTimer) {
      window.clearTimeout(root._suggestTimer);
      root._suggestTimer = 0;
    }
    if (root) root._suggestGen = (root._suggestGen || 0) + 1;
    if (root) root._suggestIndex = -1;
    const list = q(root, "suggest");
    const query = q(root, "query");
    if (list) {
      list.replaceChildren();
      list.hidden = true;
    }
    if (query) query.setAttribute("aria-expanded", "false");
  }

  function showSuggest(root, candidates) {
    const list = q(root, "suggest");
    const query = q(root, "query");
    const labels = copy(root);
    if (!list) return;
    list.replaceChildren();
    const note = document.createElement("li");
    note.className = "market-suggest-note";
    note.textContent = labels.suggest;
    list.appendChild(note);
    (candidates || []).forEach((row) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "market-suggest-btn";
      button.setAttribute("role", "option");
      const code = String(row.hs || "");
      const body = String(row.text || "");
      const shown = body.indexOf(code) === 0 ? body : code + " " + body;
      button.textContent = shown;
      button.addEventListener("click", () => {
        if (query) query.value = code;
        clearSuggest(root);
        fetchCard(root, code, false);
      });
      item.appendChild(button);
      list.appendChild(item);
    });
    list.hidden = false;
    root._suggestIndex = -1;
    if (query) query.setAttribute("aria-expanded", "true");
  }

  function suggestButtons(root) {
    const list = q(root, "suggest");
    if (!list || list.hidden) return [];
    return Array.prototype.slice.call(list.querySelectorAll("button"));
  }

  function highlightSuggest(root, index) {
    const buttons = suggestButtons(root);
    buttons.forEach((button, item) => {
      const on = item === index;
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-selected", on ? "true" : "false");
    });
    root._suggestIndex = index;
    if (buttons[index] && buttons[index].scrollIntoView) {
      buttons[index].scrollIntoView({ block: "nearest" });
    }
  }

  function metric(label, value) {
    const wrap = document.createElement("div");
    wrap.className = "market-metric";
    const name = document.createElement("span");
    name.className = "market-metric-label";
    name.textContent = label;
    const figure = document.createElement("span");
    figure.className = "market-metric-value";
    figure.textContent = value;
    wrap.appendChild(name);
    wrap.appendChild(figure);
    return wrap;
  }

  function drawSpark(root, series, labels) {
    const host = q(root, "spark");
    if (!host) return;
    host.replaceChildren();
    if (!series || series.length < 3) return;
    const nums = series.map((point) => Number(point.value_usd)).filter((n) => Number.isFinite(n));
    if (nums.length < 3) return;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 32");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("overflow", "visible");
    svg.setAttribute("role", "img");
    const ariaCaption = series.map((point) => String(point.year) + " " + String(point.value_usd)).join(", ");
    svg.setAttribute("aria-label", ariaCaption);
    const min = Math.min.apply(null, nums);
    const max = Math.max.apply(null, nums);
    const span = max - min || 1;
    const coords = nums.map((n, index) => {
      const x = nums.length === 1 ? 0 : Math.round((index / (nums.length - 1)) * 100);
      const y = Math.round(32 - ((n - min) / span) * 28);
      return x + "," + y;
    });
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    poly.setAttribute("points", coords.join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "currentColor");
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(poly);
    const caption = document.createElement("p");
    caption.className = "market-spark-caption";
    caption.textContent = labels.sparkCaption || labels.hero;
    const axis = document.createElement("p");
    axis.className = "market-spark-axis";
    axis.textContent = groupAmount(min) + " – " + groupAmount(max) + " " + labels.usd;
    const years = document.createElement("p");
    years.className = "market-spark-years";
    series.forEach((point) => {
      const mark = document.createElement("span");
      mark.textContent = String(point.year);
      years.appendChild(mark);
    });
    host.appendChild(caption);
    host.appendChild(svg);
    host.appendChild(axis);
    host.appendChild(years);
    host.dataset.ready = labels.sparkCaption || labels.hero;
  }

  function paint(root, raw) {
    if (!root) return;
    root._last = raw;
    applyChrome(root);
    const labels = copy(root);
    const payload = normalize(raw);
    const state = q(root, "state");
    const retry = q(root, "retry");
    const metrics = q(root, "metrics");
    if (metrics) metrics.replaceChildren();
    if (!payload) {
      text(q(root, "heroLabel"), "");
      setHero(root, labels.prompt, true);
      text(q(root, "heroYear"), "");
      text(q(root, "desc"), "");
      text(state, "");
      if (retry) retry.hidden = true;
      drawSpark(root, [], labels);
      text(q(root, "prov"), "");
      const link = q(root, "sourceLink");
      if (link) {
        text(link, labels.source);
        link.hidden = true;
        link.removeAttribute("href");
      }
      root.dataset.hs = "";
      return;
    }
    const hero = payload.hero || {};
    const provenance = payload.provenance || {};
    const failed = payload.status !== "ok";
    const offline = payload.reason === "offline" || payload.reason === "upstream_error" || payload.reason === "rate_limited";
    text(q(root, "heroLabel"), labels.hero);
    text(q(root, "desc"), missing(payload.hs_desc) ? "" : String(payload.hs_desc));
    const noFigures = failed || missing(hero.value_usd);
    if (noFigures) {
      setHero(root, payload.reason === "offline" ? labels.offline : labels.noData, true);
      text(q(root, "heroYear"), "");
    } else {
      setHero(root, groupAmount(hero.value_usd) + " " + labels.usd, false);
      text(q(root, "heroYear"), hero.year == null ? "" : String(hero.year));
    }
    if (metrics && !noFigures) {
      const world = payload.world || {};
      const worldValue = world.status === "ok" && !missing(world.value_usd)
        ? groupAmount(world.value_usd) + " " + labels.usd
        : labels.unavailable + (world.reason ? " (" + world.reason + ")" : "");
      metrics.appendChild(metric(labels.world, worldValue));
      const qtyValue = missing(payload.qty)
        ? labels.unavailable
        : groupAmount(payload.qty) + (missing(payload.qty_unit) ? "" : " " + payload.qty_unit);
      metrics.appendChild(metric(labels.qty, qtyValue));
      if (!missing(payload.unit_value_usd) && !missing(payload.qty) && !missing(hero.value_usd)) {
        metrics.appendChild(metric(labels.unit, groupAmount(payload.unit_value_usd) + " " + labels.usd));
      }
    }
    drawSpark(root, failed ? [] : payload.series, labels);
    const bits = [];
    if (payload.reason && failed) bits.push(String(payload.reason));
    const cache = provenance.cache;
    if ((cache === "fresh" || cache === "stale") && provenance.cache_age_seconds != null) {
      bits.push(agePhrase(provenance.cache_age_seconds, labels));
    }
    if (payload.view === "session" && payload.status === "ok") bits.push(labels.sessionHint);
    text(state, bits.join(" · "));
    if (retry) retry.hidden = !offline;

    const reporter = (payload.reporter && payload.reporter.name) || "";
    const partner = (payload.partner && payload.partner.name) || "";
    const gap = noFigures ? "—" : labels.unavailable;
    const provBits = [
      labels.hs + " " + (missing(payload.hs) ? gap : payload.hs),
      reporter && partner ? reporter + " / " + partner : "",
      hero.year == null ? "" : String(hero.year),
      labels.valuation + " " + (missing(payload.valuation) ? gap : payload.valuation),
      labels.fetched + " " + (missing(provenance.fetched_at) ? gap : provenance.fetched_at),
    ].filter(Boolean);
    text(q(root, "prov"), provBits.join(" · "));
    const link = q(root, "sourceLink");
    const url = typeof provenance.url === "string" ? provenance.url : "";
    if (link) {
      text(link, (provenance.source || labels.source) + "");
      if (url.indexOf("https://") === 0) {
        link.href = url;
        link.hidden = false;
      } else {
        link.hidden = true;
        link.removeAttribute("href");
      }
    }
    root.dataset.hs = missing(payload.hs) ? "" : String(payload.hs);
  }

  async function fetchCard(root, hs, refresh) {
    clearSuggest(root);
    const endpoint = root.dataset.endpoint || "/api/v1/market";
    setBusy(root, true);
    const query = "hs=" + encodeURIComponent(hs) + "&view=card" + (refresh ? "&refresh=true" : "");
    try {
      const headers = root._marketHeaders ? root._marketHeaders() : {};
      const res = await fetch(endpoint + "?" + query, { headers: headers });
      const data = await res.json();
      setBusy(root, false);
      paint(root, data);
    } catch (err) {
      setBusy(root, false);
      paint(root, { status: "unavailable", reason: "offline", hs: hs, hero: {}, provenance: {} });
    }
  }

  async function fetchSuggest(root, keyword) {
    const endpoint = root.dataset.endpoint || "/api/v1/market";
    const generation = root._suggestGen || 0;
    try {
      const headers = root._marketHeaders ? root._marketHeaders() : {};
      const res = await fetch(endpoint + "?keyword=" + encodeURIComponent(keyword), { headers: headers });
      const data = await res.json();
      if ((root._suggestGen || 0) !== generation) return;
      if (data.status === "needs_confirmation") showSuggest(root, data.candidates || []);
      else {
        clearSuggest(root);
        text(q(root, "state"), data.reason ? String(data.reason) : "");
      }
    } catch (err) {
      clearSuggest(root);
    }
  }

  function mount(root, options) {
    if (!root || root.dataset.mounted === "1") return;
    root.dataset.mounted = "1";
    options = options || {};
    if (options.endpoint) root.dataset.endpoint = options.endpoint;
    root._marketHeaders = options.headers || null;
    const savedLang = window.localStorage && localStorage.getItem("tfMarketLang");
    const savedTheme = window.localStorage && localStorage.getItem("tfMarketTheme");
    if (savedLang === "zh-Hant" || savedLang === "en") root.dataset.lang = savedLang;
    if (savedTheme === "dark" || savedTheme === "light") document.documentElement.dataset.theme = savedTheme;
    applyChrome(root);
    paint(root, null);
    const query = q(root, "query");
    const form = q(root, "form");
    root._suggestTimer = 0;
    if (query) {
      query.addEventListener("input", () => {
        window.clearTimeout(root._suggestTimer);
        const value = query.value.trim();
        if (value.length < 2) {
          clearSuggest(root);
          return;
        }
        root._suggestTimer = window.setTimeout(() => fetchSuggest(root, value), 250);
      });
      query.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          clearSuggest(root);
          return;
        }
        const buttons = suggestButtons(root);
        if (!buttons.length) return;
        if (event.key === "ArrowDown") {
          event.preventDefault();
          const current = typeof root._suggestIndex === "number" ? root._suggestIndex : -1;
          const next = current + 1 >= buttons.length ? 0 : current + 1;
          highlightSuggest(root, next);
          return;
        }
        if (event.key === "Enter" && root._suggestIndex >= 0 && buttons[root._suggestIndex]) {
          event.preventDefault();
          buttons[root._suggestIndex].click();
        }
      });
    }
    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) clearSuggest(root);
    });
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const value = query ? query.value.trim() : "";
        if (/^\d{2,6}$/.test(value)) {
          clearSuggest(root);
          fetchCard(root, value, false);
        } else if (value) {
          fetchSuggest(root, value);
        }
      });
    }
    const refresh = q(root, "refresh");
    if (refresh) {
      refresh.addEventListener("click", () => {
        const hs = root.dataset.hs || (query ? query.value.trim() : "");
        if (/^\d{2,6}$/.test(hs)) fetchCard(root, hs, true);
      });
    }
    const retry = q(root, "retry");
    if (retry) {
      retry.addEventListener("click", () => {
        const hs = root.dataset.hs || (query ? query.value.trim() : "");
        if (/^\d{2,6}$/.test(hs)) fetchCard(root, hs, true);
      });
    }
    const theme = q(root, "theme");
    if (theme) {
      theme.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem("tfMarketTheme", next); } catch (err) { /* ignore */ }
        applyChrome(root);
      });
    }
    const lang = q(root, "lang");
    if (lang) {
      lang.addEventListener("click", () => {
        root.dataset.lang = langOf(root) === "en" ? "zh-Hant" : "en";
        document.documentElement.lang = root.dataset.lang === "zh-Hant" ? "zh-Hant" : "en";
        try { localStorage.setItem("tfMarketLang", root.dataset.lang); } catch (err) { /* ignore */ }
        applyChrome(root);
        paint(root, root._last || null);
      });
    }
    const basePaint = paint;
    root._paint = function (payload) {
      root._last = payload;
      basePaint(root, payload);
    };
  }

  function paintRoot(root, payload) {
    if (!root) return;
    if (root.dataset.loading === "1") return;
    root._last = payload;
    paint(root, payload);
  }

  window.TradeFlowMarket = { mount: mount, paint: paintRoot, copy: COPY };
})();
