(function () {
  function roots(doc) {
    return doc || document;
  }

  function tradeflowLatestSupplierText(doc) {
    const root = roots(doc);
    const nodes = root.querySelectorAll('.msg-bubble.msg-received, [data-supplier-message="1"]');
    if (!nodes.length) return "";
    const last = nodes[nodes.length - 1];
    return (last.innerText || last.textContent || "").trim();
  }

  function tradeflowCaptchaVisible(doc) {
    const root = roots(doc);
    const view = root.defaultView || window;
    const nodes = root.querySelectorAll(
      "#baxia-dialog-content, #nc_1_n1z, .baxia-dialog, .nc_iconfont, .nc-lang-cnt, .sm-pop, .sec-modal, #challenge-running, .cf-turnstile-wrapper"
    );
    for (const el of nodes) {
      const display = view.getComputedStyle(el).display;
      if (display !== "none" && el.offsetParent !== null) return true;
    }
    if (root.querySelector("iframe[src*='recaptcha'], iframe[src*='hcaptcha']")) return true;
    return false;
  }

  function tradeflowFold(value) {
    return String(value || "").normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function tradeflowMatchSuppliers(suppliers, page) {
    const hay = tradeflowFold([page && page.shop, page && page.title, page && page.url].filter(Boolean).join("\n"));
    return (suppliers || []).filter((supplier) => {
      const name = tradeflowFold(supplier.name);
      const address = tradeflowFold(supplier.address);
      if (name && hay.indexOf(name) >= 0) return true;
      if (address && hay.indexOf(address) >= 0) return true;
      return false;
    });
  }

  function tradeflowImportPayload(doc) {
    if (tradeflowCaptchaVisible(doc)) return { ok: false, reason: "captcha" };
    const text = tradeflowLatestSupplierText(doc);
    if (!text) return { ok: false, reason: "empty" };
    const root = roots(doc);
    const shopNode = root.querySelector(".supplier-name, [data-shop-name]");
    const shop = shopNode ? (shopNode.innerText || shopNode.textContent || "").trim() : "";
    const title = (root.title || "").trim();
    const href = root.location && root.location.href ? root.location.href : "";
    const simulated = /mock_chat\.html/i.test(href) || /simulated/i.test(title) || !!root.querySelector(".sim-banner");
    return { ok: true, text: text, shop: shop, title: title, url: href, simulated: simulated };
  }

  globalThis.tradeflowLatestSupplierText = tradeflowLatestSupplierText;
  globalThis.tradeflowCaptchaVisible = tradeflowCaptchaVisible;
  globalThis.tradeflowImportPayload = tradeflowImportPayload;
  globalThis.tradeflowMatchSuppliers = tradeflowMatchSuppliers;
})();
