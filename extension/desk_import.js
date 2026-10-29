(function () {
  function roots(doc) { return doc || document; }

  // Receive-only profiles. Fixture coverage is not live-account validation.
  function tradeflowChatProfile(doc) {
    const root = roots(doc);
    const host = String(root.location && root.location.hostname || "").toLowerCase();
    const common = {
      platform: "generic",
      containers: ".chat-messages, .im-message-box, #messages-box, #chatMessages, .messages-container",
      messages: '.msg-bubble, .msg-received, .msg-sent, .message-in, .message-out, [data-supplier-message="1"], [data-message-direction]',
      shop: ".supplier-name, [data-shop-name]",
      contact: "[data-contact], .contact-name",
    };
    if (host === "web.whatsapp.com") return Object.assign({}, common, {
      platform: "whatsapp",
      containers: '#main [data-testid="conversation-panel-messages"], #main [role="application"], .chat-messages',
      messages: ".message-in, .message-out",
      shop: '#main header [data-testid="conversation-info-header-chat-title"], #main header span[title]',
      contact: '#main header [data-contact]',
    });
    if (["wx.qq.com", "wx2.qq.com", "web.wechat.com", "web2.wechat.com"].includes(host)) {
      return Object.assign({}, common, {
        platform: "wechat", containers: ".chat_bd, .chat-messages", messages: ".message",
        shop: ".chat_hd .title_name, .title_wrap .title_name, .chat_hd .title",
      });
    }
    if (host === "1688.com" || host.endsWith(".1688.com")) return Object.assign({}, common, {
      platform: "1688", containers: ".im-message-box, .im-message-list, .chat-messages, #messages-box",
    });
    if (host === "alibaba.com" || host.endsWith(".alibaba.com")) return Object.assign({}, common, {
      platform: "alibaba", containers: ".im-message-list, .im-message-container, .chat-messages, #messages-box",
    });
    return common;
  }

  function tradeflowVisibleChat(doc) {
    const root = roots(doc);
    return Array.from(root.querySelectorAll(tradeflowChatProfile(root).containers)).find((node) =>
      node.getClientRects().length && !node.closest('[hidden], [aria-hidden="true"]')
    ) || null;
  }

  function tradeflowChatDirection(node, doc) {
    const profile = tradeflowChatProfile(doc);
    const message = node && node.closest ? node.closest(profile.messages) : null;
    if (!message) return "unknown";
    if (message.closest('.msg-sent, .message-out, .outgoing, .sent, .self, .own-message, [data-message-direction="outgoing"], [data-message-direction="sent"]')) return "sent";
    if (profile.platform === "wechat") return message.classList.contains("me") ? "sent" : "received";
    if (message.matches('.msg-received, .message-in, [data-supplier-message="1"], [data-message-direction="incoming"], [data-message-direction="received"]')) return "received";
    return "unknown";
  }

  function tradeflowLatestSupplierNode(doc) {
    const root = roots(doc);
    const profile = tradeflowChatProfile(root);
    const container = tradeflowVisibleChat(root);
    if (!container) return null;
    const nodes = Array.from(container.querySelectorAll(profile.messages)).filter((node) =>
      !node.querySelector(profile.messages) && tradeflowChatDirection(node, root) === "received"
    );
    return nodes.length ? nodes[nodes.length - 1] : null;
  }

  function tradeflowMessageId(node) {
    return node ? node.getAttribute("data-message-id") || node.getAttribute("data-id") || node.id || "" : "";
  }

  function tradeflowLatestSupplierText(doc) {
    const last = tradeflowLatestSupplierNode(doc);
    return last ? (last.innerText || last.textContent || "").trim() : "";
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

  function tradeflowChatIdentity(page) {
    let url = String((page && page.url) || "");
    try {
      const parsed = new URL(url);
      parsed.username = ""; parsed.password = "";
      parsed.searchParams.sort();
      url = parsed.href;
    } catch (err) { /* An unknown page cannot acquire another page's identity. */ }
    return JSON.stringify([url, tradeflowFold(page && page.shop), tradeflowFold(page && page.contact)]);
  }

  function tradeflowPhrase(hay, needle) {
    const text = tradeflowFold(hay);
    const name = tradeflowFold(needle);
    if (!text || !name || name.length < 2) return false;
    if (text === name) return true;
    const escaped = name.replace(/[.*+?^$(){}|[\]\\]/g, "\\$&");
    return new RegExp("(^|[^\\p{L}\\p{N}])" + escaped + "($|[^\\p{L}\\p{N}])", "u").test(text);
  }

  function tradeflowMatchSuppliers(suppliers, page) {
    const shop = page && page.shop ? page.shop : "";
    const contact = page && page.contact ? page.contact : "";
    let path = "";
    try { path = new URL(page && page.url || "", "http://localhost").pathname; } catch (err) { /* unknown */ }
    return (suppliers || []).filter((supplier) => {
      const name = supplier.name || "", address = supplier.address || "";
      if (tradeflowPhrase(shop, name) || tradeflowPhrase(contact, name)) return true;
      return !!address && (tradeflowPhrase(shop, address) || tradeflowPhrase(contact, address) || tradeflowPhrase(path, address));
    });
  }

  function tradeflowImportPayload(doc) {
    const node = tradeflowLatestSupplierNode(doc);
    const payload = tradeflowPayloadFor(doc, node ? (node.innerText || node.textContent || "").trim() : "");
    payload.message_id = tradeflowMessageId(node);
    return payload;
  }

  function tradeflowPayloadFor(doc, messageText) {
    if (tradeflowCaptchaVisible(doc)) return {ok: false, reason: "captcha"};
    const text = String(messageText || "").trim();
    if (!text) return {ok: false, reason: "empty"};
    const root = roots(doc);
    const profile = tradeflowChatProfile(root);
    const shopNode = root.querySelector(profile.shop);
    const shop = shopNode ? (shopNode.innerText || shopNode.textContent || shopNode.getAttribute("title") || "").trim() : "";
    const contactNode = root.querySelector(profile.contact);
    const contact = contactNode ? (contactNode.getAttribute("data-contact") || contactNode.innerText || contactNode.textContent || "").trim() : "";
    const title = (root.title || "").trim();
    const href = root.location && root.location.href ? root.location.href : "";
    const simulated = /mock_chat\.html/i.test(href) || /simulated/i.test(title) || !!root.querySelector(".sim-banner");
    return {ok: true, text, shop, contact, title, url: href, platform: profile.platform, simulated};
  }

  Object.assign(globalThis, {
    tradeflowChatProfile, tradeflowVisibleChat, tradeflowChatDirection, tradeflowMessageId,
    tradeflowChatIdentity, tradeflowLatestSupplierText, tradeflowCaptchaVisible,
    tradeflowImportPayload, tradeflowPayloadFor, tradeflowMatchSuppliers,
  });
})();
