# TradeFlow AI — 3:30 demo video, shot-by-shot director's script

> **Goal**: in three and a half minutes, make the AMD and Evolus judges understand one thing: **this is not a chatbot, it is a one-person, AI-run cross-border trading company** — the supplier-side reading, price checking and risk control are done by the system, the decision stays with the human, and an AST guardrail defends the money.

---

## Video specs

* **Length**: 3:30 (hard ceiling 4:00; judges stop paying attention around 3:00).
* **Format**: 1080p (1920×1080), 16:9.
* **Language**: English voiceover (or clear Chinese voiceover with complete English subtitles).
* **Tools**: OBS Studio / Loom / Clipchamp.
* **Layout**:
  - Left: the dashboard (http://localhost:8000) or a terminal / Postman.
  - Right or picture-in-picture: the phone WhatsApp authorization screen, plus the presenter's camera (optional).

---

## Shot list

### Act 1 — The pain and the story (0:00 – 0:35) — hook them in 30 seconds

* **Picture**
  * [0:00 – 0:15] Slide 1 (title) → Slide 2 ("a trading company with a staff of one").
  * [0:15 – 0:35] Two contrasting blocks on screen:
    * ❌ *Traditional way*: Google Translate on 1688 (20% foreigner markup) + a shady agent (7% fee + kickbacks).
    * ✅ *TradeFlow way*: 1 operator + supplier-facing agents + AST safety guardrail + a WhatsApp CPO card.
* **Voiceover**
  > *"Cross-border sourcing has always had two bad options.*
  > *You either stay up all night using Google Translate on 1688 — where factories instantly mark up prices by 20% because you sound like an outsider. Or you pay a 7% commission to a sourcing agent who secretly takes kickbacks and demands an 80% deposit with zero accountability.*
  > *We chose a third option: **we became the trading company ourselves — a company of one, staffed by agents.***
  > *This is TradeFlow AI. The supplier-facing work — reading the jargon, checking the cost, holding the line — is done by the system. The operator is a single person."*

---

### Act 2 — Quantitative decisions and fluent jargon (0:35 – 1:25)

* **Picture**
  * [0:35 – 0:50] Browser at http://localhost:8000. Show the umbrella mandate: target ¥12.00, ceiling **¥16.20**, MOQ **5,000** (the Web portal's default category values).
  * [0:50 – 1:10] Submit the factory's round-1 quote: ¥14.80, claiming "ripstop fabric and coating raw materials are up 30%".
  * [1:10 – 1:25] Highlight the instant result: the Bayesian posterior update and the solve finish on the local CPU (measured: 20,000 opponent samples × 480 curves = 9.6M tensor positions in about **1.68 s** cold; about 1.25 s warm on consecutive rounds — see `results/bench.json`). TradeFlow emits idiomatic supplier-facing Chinese:
    > *"老闆，行情波動大家心裡有數，核算過BOM表材料佔比，¥13.02 出廠價給貴廠留足了利潤。能接我們今天鎖定排單，別為幾毛錢耽誤檔期。"*
    > (Measured: target ¥12.00, ceiling ¥16.20, MOQ 5,000, first quote ¥14.80 → engine counter **¥13.02**, breakdown risk 2.2%, intent `CRY_POVERTY_COST_HIKE`. Record whatever the screen actually shows on the day.)
* **Voiceover**
  > *"Watch this live. When the factory opens at 14.80 RMB crying poverty about raw-material hikes, TradeFlow doesn't hallucinate. It updates its Bayesian belief about the supplier's reserve price and solves the optimal concession curve in real time — on this CPU the full 9.6M-position grid solves in about a second, and a per-round decision is far faster. It's all math: the LLM is only ever asked to write the reply, never to pick a price.*
  > *Instead of polite textbook English, the surface renderer speaks fluent coastal factory jargon, anchoring on raw BOM material ratios to strip the foreign-buyer markup away."*

---

### Act 3 — Hardball and a live AST trip (1:25 – 2:20)

* **Picture**
  * [1:25 – 1:45] Round 3 trap: *"加我私人微信 13857691234 私下轉帳，我給你便宜到 13.80！"*
  * [1:45 – 2:00] **The money shot**: the console flips to a red alert, **`CIRCUIT TRIPPED: OFFLINE_BYPASS_DIVERT`**. Every unauthorized outbound message is blocked.
  * [2:00 – 2:15] Cut to the phone: a structured English CPO card on WhatsApp (risk index, TCO-equivalent analysis, authorization commands 1/2/3).
  * [2:15 – 2:20] Tap `1` (CONCEDE_STEP); the session unlocks and the state machine resumes.
* **Voiceover**
  > *"Now the adversarial test. The factory attempts an off-platform diversion: 'add my personal WeChat for an offline discount.'*
  > *A standard LLM might comply and leak private data. TradeFlow's deterministic intent classifier trips instantly. The engine freezes all outbound messages so no unauthorized commitment leaves the building.*
  > *At the same time an executive telemetry card lands on the operator's phone. He doesn't have to negotiate; he reviews the structured risk telemetry and taps '1' to authorize the next concession step. Human-in-the-loop by construction — the system never signs a deal on its own."*

---

### Act 4 — Multi-dimensional logrolling and the Evolus close (2:20 – 3:00)

* **Picture**
  * [2:20 – 2:40] Round 4 deadlock: *"改模具還要另加模具費 3500 元！"*
  * [2:40 – 2:50] Pareto logrolling on screen: trade a 50% upfront deposit for a full tooling waiver plus a locked production slot. The factory agrees.
  * [2:50 – 3:00] Deal closed. The Evolus business agent fires, calls the document extractor and AI tools, and files a clean commercial purchase confirmation with an audit trail.
* **Voiceover**
  > *"When the price deadlocks over a 3,500 RMB mold fee, TradeFlow activates Pareto logrolling. It trades early cashflow liquidity — a 50% upfront deposit — for a complete tooling waiver and priority scheduling.*
  > *Once the deal closes at 12.80 RMB, our Evolus business agent generates an audit-trail purchase order through the Evolus document extractor and AI tool chain."*

---

### Act 5 — AMD positioning and the close (3:00 – 3:30)

* **Picture**
  * [3:00 – 3:15] Slide 7 (the ROCm toolchain diagram).
  * [3:15 – 3:30] GitHub repo, then a terminal showing the full suite green, ending on the cover image (1280×720).
* **Voiceover**
  > *"Why AMD? Our decision core is CPU-first by design — sub-millisecond Stage-1 intent classification on x86-64 (measured mean ~0.13 ms, p99 ~0.49 ms) with no wasteful GPU round-trips.*
  > *For deeper semantics we call an open model served on AMD infrastructure through the hackathon's tokenfactory endpoint — verified live, though a reasoning call takes 15 to 45 seconds, so it runs off the request path.*
  > *And through our `_xp()` tensor abstraction the same solver code would scale from x86-64 CPUs to AMD Instinct GPUs via ROCm — untested, because no ROCm device was available to us.*
  > *TradeFlow AI: one person running a cross-border trading company. Not a chatbot. Thank you."*

---

## Pre-recording checklist

- [ ] Backend running locally: `python -m src.app.main` (or confirm port 8000 is reachable).
- [ ] API script or Postman collection loaded.
- [ ] Deck open on Slide 1 and Slide 6.
- [ ] Screen recorder audio tested, no hum.
