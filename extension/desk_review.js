/* Live desk: the items that wait for a person.
 *
 * Each item is a supplier message the reader has already looked at. The panel
 * shows the message, the figures found in it, and the exact line a figure came
 * from. Nothing is accepted until a person picks a figure and presses Accept.
 * Every string from the server goes in through textContent.
 */
(function () {
  const choice = new Map();

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function lineContaining(raw, token) {
    const lines = String(raw || "").split(/\r?\n/);
    for (let i = 0; i < lines.length; i += 1) {
      if (token && lines[i].indexOf(token) >= 0 && lines[i].trim()) return lines[i].trim();
    }
    return "";
  }

  function tokensOf(provenance) {
    if (provenance.candidates && provenance.candidates.length) return provenance.candidates;
    return provenance.tokens || [];
  }

  function renderItem(item, suppliers, api) {
    const provenance = item.provenance || {};
    const tokens = tokensOf(provenance);
    const saved = choice.get(item.id) || {};
    let token = saved.token || provenance.suggested_token || "";
    if (token && tokens.indexOf(token) < 0) token = "";
    let supplierId = saved.supplierId || item.supplier_id || "";

    const block = el("div", "desk-review-item");
    block.dataset.event = item.id;
    const reason = provenance.review_reason || item.confidence || "review";
    block.appendChild(el("p", "desk-note", (item.source || "quote") + " · " + reason));
    block.appendChild(el("pre", "desk-review-text", item.raw_text || ""));

    const confirm = el("p", "desk-review-confirm");
    const accept = el("button", null, "Accept");
    accept.type = "button";

    function remember() {
      choice.set(item.id, { token: token, supplierId: supplierId });
    }

    function paintConfirm() {
      if (!token) {
        confirm.hidden = true;
        confirm.textContent = "";
        accept.disabled = true;
        return;
      }
      const mapped = provenance.confirm_spans && provenance.confirm_spans[token];
      const span = mapped || lineContaining(item.raw_text, token);
      confirm.hidden = false;
      confirm.textContent = "Confirming " + token + " " + (provenance.currency || "CNY") +
        (span ? " from “" + span + "”." : ".");
      accept.disabled = !supplierId;
    }

    if (tokens.length) {
      const picks = el("div", "desk-review-picks");
      tokens.forEach((value) => {
        const pick = el("button", null, value);
        pick.type = "button";
        pick.setAttribute("aria-pressed", value === token ? "true" : "false");
        pick.addEventListener("click", () => {
          token = value;
          remember();
          Array.prototype.forEach.call(picks.children, (node) => {
            node.setAttribute("aria-pressed", node === pick ? "true" : "false");
          });
          paintConfirm();
        });
        picks.appendChild(pick);
      });
      block.appendChild(picks);
    } else {
      block.appendChild(el("p", "desk-note", "No figure was found in this message."));
    }
    block.appendChild(confirm);

    const select = document.createElement("select");
    const blank = el("option", null, "Choose a supplier");
    blank.value = "";
    select.appendChild(blank);
    suppliers.forEach((supplier) => {
      const option = el("option", null, supplier.name);
      option.value = supplier.id;
      if (supplier.id === supplierId) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      supplierId = select.value;
      remember();
      paintConfirm();
    });
    block.appendChild(select);

    const reject = el("button", null, "Reject");
    reject.type = "button";
    function send(payload) {
      accept.disabled = true;
      reject.disabled = true;
      api.post(item.id, payload).then((snapshot) => {
        choice.delete(item.id);
        api.done(snapshot);
      }).catch((err) => {
        reject.disabled = false;
        paintConfirm();
        api.fail(err && err.message ? err.message : "The desk did not take that.");
      });
    }
    accept.addEventListener("click", () => {
      if (!token || !supplierId) return;
      send({ action: "accept", supplier_id: supplierId, chosen_token: token });
    });
    reject.addEventListener("click", () => send({ action: "reject" }));
    const actions = el("div", "desk-review-actions");
    actions.appendChild(accept);
    actions.appendChild(reject);
    block.appendChild(actions);
    paintConfirm();
    return block;
  }

  // api = { post(eventId, payload) -> Promise<snapshot>, done(snapshot), fail(message) }
  function render(root, items, suppliers, api) {
    if (!root) return;
    clear(root);
    const list = items || [];
    const live = new Set(list.map((item) => item.id));
    Array.from(choice.keys()).forEach((id) => { if (!live.has(id)) choice.delete(id); });
    if (!list.length) {
      root.appendChild(el("p", "desk-note", "Nothing waiting."));
      return;
    }
    list.forEach((item) => root.appendChild(renderItem(item, suppliers || [], api)));
  }

  globalThis.TradeFlowDeskReview = { render: render };
})();
