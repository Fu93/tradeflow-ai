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

  function tradeflowImportPayload(doc) {
    if (tradeflowCaptchaVisible(doc)) return { ok: false, reason: "captcha" };
    const text = tradeflowLatestSupplierText(doc);
    if (!text) return { ok: false, reason: "empty" };
    return { ok: true, text: text };
  }

  globalThis.tradeflowLatestSupplierText = tradeflowLatestSupplierText;
  globalThis.tradeflowCaptchaVisible = tradeflowCaptchaVisible;
  globalThis.tradeflowImportPayload = tradeflowImportPayload;
})();
