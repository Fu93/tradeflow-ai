# TradeFlow AI — Chrome Manifest V3 Copilot Decision Deck extension

**TradeFlow Copilot** is a decision-support sidekick for professional buyers, working alongside chat on 1688, Alibaba TradeMessenger and WhatsApp Web.

---

## Key features

1. **Live DOM streaming (`MutationObserver`)**
   - 350 ms debounce plus message deduplication, so supplier bubbles and step quotes are captured precisely.
2. **Decision radar and tactical cards (Decision Deck HUD)**
   - Non-price (TCO) shadow costs for lead-time slippage, excess MOQ and payment terms are computed by `src/engine/tco.py`.
     *Boundary*: that module's conversion parameters (8% annual capital cost, 0.3% per day of lead-time delay, …) are **overridable defaults**, not calibrated against real transactions — see `docs/LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §13.
   - Produces three differentiated reply cards (budget-authority pressure, terms swap, alternative-supplier leverage), each with **one-click copy**. Card bodies are in Chinese because they are pasted straight to a Chinese supplier; the surrounding UI is English.
3. **Human-in-the-loop approval gate**
   - Copilot mode by default. The extension **deliberately cannot** fill the chat box or click send —
     the buyer pastes and sends every draft, which rules out both platform risk-control flags from synthetic events and accidental binding offers.
     (`findChatInput()` / `findSendButton()` were removed from `content.js`, with a comment explaining why.)
4. **Safety circuit breaker**
   - When the solver reports breakdown probability $\ge 15\%$ (the threshold in `src/app/main.py`), the HUD warns and opens the phone authorization channel.
5. **Quantitative side panel (Chrome SidePanel API)**
   - Live posterior reserve mean $\mu$, uncertainty $\sigma$ and a breakdown-probability bar.
     *Boundary*: $\mu/\sigma$ are prior-driven posterior updates, so accuracy depends on the prior;
     the "confidence" shown is a **belief convergence metric**, **not** a calibrated statistical probability (see `LIMITATIONS` §1).
   - Manual tactical intervention: 1 (concede), 2 (hold), 3 (walk away).

---

## Live desk panel (the part that talks to the Nebius/Nemotron quote reader)

Open the side panel's **Live desk** tab.

0. No deal yet? Press **Start a sample deal** (four fictional suppliers on a test domain).
1. Pick the **Desk server**: this computer (`127.0.0.1:8000`) or the hosted demo
   (`https://tradeflow-ai-demo.onrender.com`). Those are the only two hosts the
   manifest allows (`host_permissions` and the page CSP `connect-src`); another
   host needs both edited.
2. Enter the API key and the deal id from `/desk`.
3. On a supplier chat, press **Import visible message**, or tick **Read new supplier
   messages automatically**: each new bubble the page marks as received then goes to
   the desk by itself (the same message twice is one quote; the toolbar badge counts
   what waits). When a chat matches no supplier, or several, choose its supplier once
   in the panel. The automatic read was tested on `mock_chat.html`; the 1688, Alibaba,
   WhatsApp and WeChat page structures have not been tested.
4. Under **Waiting for you**, pick the figure, check the quoted line, choose the
   supplier, and press **Accept**. Nothing is accepted for you.
   Each waiting item says which model read the message (for example a Nemotron
   model on Nebius) or that only the rule reader did.
5. Each supplier row has **Check supplier on the web** (Tavily). Its results are
   reference only and never change a counter.
6. The counter and the draft appear below. Copy the draft and paste it yourself.
   The extension never types into the chat or presses send.

## 30-second setup

1. **Start the TradeFlow backend**
   ```bash
   python -m uvicorn src.app.main:app --port 8000 --reload
   ```
2. **Load the extension in Chrome**
   - Go to `chrome://extensions`
   - Enable **Developer mode** (top right)
   - Click **Load unpacked** (top left)
   - Select the `tradeflow-ai/extension` folder
3. **Open the sandbox or the target site (pick one)**

   **Option A (recommended, no extra permission) — serve the sandbox over http**
   ```bash
   python -m http.server 8080 --directory extension
   ```
   Then open `http://localhost:8080/mock_chat.html`. `manifest.json`'s
   `content_scripts.matches` already covers `http://localhost/*`, so the HUD injects
   without enabling any extra file-access permission (the easiest route on a demo machine).

   **Option B — open `extension/mock_chat.html` over `file://`**
   > [Required precondition] Chrome does **not** run content scripts on `file://` pages by default.
   > Skip this and no HUD appears at all (the extension looks broken).
   1. Go to `chrome://extensions`
   2. Find "TradeFlow Copilot — Procurement Decision Deck" → click **Details**
   3. Turn on **Allow access to file URLs**
   4. Reload `extension/mock_chat.html` (or press F5) and the HUD injects

   - The TradeFlow HUD appears in the bottom-right corner.
   - Click the TradeFlow icon in the toolbar to open the side panel.
   - The sandbox's "simulate the risk-check slider" button exercises the compliance gate: the HUD
     switches to `CAPTCHA DETECTED`, and after you click the slider it returns to
     `COPILOT RADAR ON` and re-registers the session binding for that tab.
