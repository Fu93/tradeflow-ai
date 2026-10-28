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

  function tradeflowPhrase(hay, needle) {
    const text = tradeflowFold(hay);
    const name = tradeflowFold(needle);
    if (!text || !name || name.length < 2) return false;
    if (text === name) return true;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp("(^|[^\\p{L}\\p{N}])" + escaped + "($|[^\\p{L}\\p{N}])", "u").test(text);
  }

  function tradeflowMatchSuppliers(suppliers, page) {
    const shop = page && page.shop ? page.shop : "";
    const contact = page && page.contact ? page.contact : "";
    const url = page && page.url ? page.url : "";
    let path = "";
    try { path = new URL(url, "http://localhost").pathname; } catch (err) { path = ""; }
    return (suppliers || []).filter((supplier) => {
      const name = supplier.name || "";
      const address = supplier.address || "";
      if (tradeflowPhrase(shop, name) || tradeflowPhrase(contact, name)) return true;
      if (address && (tradeflowPhrase(shop, address) || tradeflowPhrase(contact, address) || tradeflowPhrase(path, address))) return true;
      return false;
    });
  }

  function tradeflowImportPayload(doc) {
    return tradeflowPayloadFor(doc, tradeflowLatestSupplierText(doc));
  }

  // The same page facts around a message the observer just saw.
  function tradeflowPayloadFor(doc, messageText) {
    if (tradeflowCaptchaVisible(doc)) return { ok: false, reason: "captcha" };
    const text = String(messageText || "").trim();
    if (!text) return { ok: false, reason: "empty" };
    const root = roots(doc);
    const shopNode = root.querySelector(".supplier-name, [data-shop-name]");
    const shop = shopNode ? (shopNode.innerText || shopNode.textContent || "").trim() : "";
    const contactNode = root.querySelector("[data-contact], .contact-name");
    const contact = contactNode ? (contactNode.innerText || contactNode.textContent || "").trim() : "";
    const title = (root.title || "").trim();
    const href = root.location && root.location.href ? root.location.href : "";
    const simulated = /mock_chat\.html/i.test(href) || /simulated/i.test(title) || !!root.querySelector(".sim-banner");
    return { ok: true, text: text, shop: shop, contact: contact, title: title, url: href, simulated: simulated };
  }

  globalThis.tradeflowLatestSupplierText = tradeflowLatestSupplierText;
  globalThis.tradeflowCaptchaVisible = tradeflowCaptchaVisible;
  globalThis.tradeflowImportPayload = tradeflowImportPayload;
  globalThis.tradeflowPayloadFor = tradeflowPayloadFor;
  globalThis.tradeflowMatchSuppliers = tradeflowMatchSuppliers;
})();
