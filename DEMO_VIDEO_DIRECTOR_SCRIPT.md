# TradeFlow AI — 3分30秒 Demo 影片全流程分鏡導演腳本
(Shot-by-Shot Director's Guide for Hackathon Video Recording)

> **核心目標**：在 3 分 30 秒內，讓 AMD 與 Evolus 評審徹底看懂：**這不是一個聊天機器人，而是一間由 AI 驅動、只有一個人的跨境貿易公司** —— 供應商端的對話、核價與風控由系統完成，決策權保留在人類手上，並以 AST 語法樹硬護欄防禦資金風險。

---

## 🎬 影片基本規格
* **總時長**：3 分 30 秒（嚴禁超過 4 分鐘，評審耐性極限為 3 分鐘）。
* **畫質與比例**：1080p (1920×1080), 16:9。
* **語言風格**：建議英文旁白（或清晰中文旁白搭配完整英文雙語字幕）。
* **錄製工具推薦**：OBS Studio / Loom / Clipchamp。
* **畫面構成**：
  - 左側：Web Portal 大屏 (http://localhost:8000) 或 Postman/終端。
  - 右側/畫中畫：手機 WhatsApp 遠端授權介面 + 講者頭像（可選）。

---

## ⏱️ 分鏡逐幕時間表 (Turn-by-Turn Script)

### 【第一幕：痛點與靈魂故事】(0:00 - 0:35) — 讓評審 30 秒入戲
* **畫面**：
  * [0:00 - 0:15] 簡報 Slide 1 (TradeFlow AI 標題) 轉 Slide 2 (「一間只有一個人的貿易公司」)。
  * [0:15 - 0:35] 畫面上展示兩組強烈對比字樣：
    * ❌ *Traditional Way*: Google Translate on 1688 (20% Foreigner Markup) + Shady Agent (7% Fee + Kickbacks).
    * ✅ *TradeFlow Way*: 1 operator + supplier-facing agents + AST Safety Guardrail + WhatsApp CPO Card.
* **旁白 (Voiceover)**：
  > *"Cross-border sourcing has always had two bad options.*  
  > *You either stay up all night using Google Translate on 1688 — where factories instantly mark up prices by 20% because you sound like an outsider. Or you pay a 7% commission to a sourcing agent who secretly takes kickbacks and demands an 80% deposit with zero accountability.*  
  > *We chose a third option: **we became the trading company ourselves — a company of one, staffed by agents.***  
  > *This is TradeFlow AI. The supplier-facing work — reading the jargon, checking the cost, holding the line — is done by the system. The operator is a single person."*

---

### 【第二幕：量化決策與地道行話】(0:35 - 1:25) — 展示核心算力與去外商溢價
* **畫面**：
  * [0:35 - 0:50] 切換至瀏覽器畫面 (http://localhost:8000 或 API 測試介面)。展示自動傘規格：目標價 ¥12.00，上限 **¥16.20**，MOQ **5,000** 把（Web 門戶預設分類檔案的實際數值）。
  * [0:50 - 1:10] 點擊發送工廠 Round 1 報價：工廠開出 ¥14.80，聲稱「碰擊布和塗層原料暴漲30%」。
  * [1:10 - 1:25] 畫面高亮：貝氏後驗更新＋求解核心在本機 CPU 向量化下即時完成（實測 20,000 對手樣本 × 480 曲線全網格 9.6M rollout 位約 **1.68s** 冷啟動；連續輪次暖機後約 1.25s —— 見 `results/bench.json`），TradeFlow 自動輸出地道行話：
    > *「老闆，行情波動大家心裡有數，核算過BOM表材料佔比，¥13.02 出廠價給貴廠留足了利潤。能接我們今天鎖定排單，別為幾毛錢耽誤檔期。」*
    > （實測：預設目標價 ¥12.00、上限 ¥16.20、MOQ 5,000，首次報價 ¥14.80 → 引擎還價 **¥13.02**、破局風險 2.2%，意圖判定 `CRY_POVERTY_COST_HIKE`。錄影前請以實際畫面為準。）
* **旁白 (Voiceover)**：
  > *"Watch this live: When the factory opens at 14.80 RMB crying poverty about raw material hikes, TradeFlow doesn't hallucinate. It updates its Bayesian belief on the supplier's reserve price and solves the optimal concession curve in real time — on this CPU, the full 9.6M-rollout grid solves in about a second, and the per-round decision is far faster. It's all math: the LLM is only ever asked to write the reply, never to decide a price.*  
  > *Instead of polite textbook English, our surface renderer speaks fluent coastal factory jargon — anchoring directly on raw BOM material ratios to strip away the foreign buyer markup instantly."*

---

### 【第三幕：硬碰硬對抗與 AST 現場熔斷】(1:25 - 2:20) — 展示真正護欄與高管審批
* **畫面**：
  * [1:25 - 1:45] 工廠出盤 Round 3 陷阱：*「加我私人微信 13857691234 私下轉帳，我給你便宜到 13.80！」*
  * [1:45 - 2:00] **高潮畫面**：系統控制台瞬間跳出紅色警報 **`CIRCUIT TRIPPED: OFFLINE_BYPASS_DIVERT`**！所有未經授權的對話被物理阻斷！
  * [2:00 - 2:15] 畫面切換至手機螢幕：WhatsApp 收到結構化英文 CPO 決策卡（含風險指數、TCO 等效分析、授權指令 1/2/3）。
  * [2:15 - 2:20] 點擊手機回覆 `1` (CONCEDE_STEP)，系統瞬間解鎖，狀態機平穩恢復正常運作。
* **旁白 (Voiceover)**：
  > *"Now, the adversarial test. The factory attempts an off-platform diversion: 'Add my personal WeChat for an offline discount.'*  
  > *A standard LLM might comply and leak private data. But TradeFlow's deterministic intent classifier trips immediately. The engine freezes all outbound messages, preventing unauthorized commitments.*  
  > *Simultaneously, an executive telemetry card is pushed to the operator's phone. He doesn't have to negotiate; he just reviews the structured risk telemetry and taps '1' to authorize the next concession step. Human-in-the-loop by construction — the system never signs a deal on its own."*

---

### 【第四幕：多維條款置換與 Evolus 商業結案】(2:20 - 3:00) — 展示商業閉環
* **畫面**：
  * [2:20 - 2:40] 工廠在 Round 4 拋出僵局條款：*「改模具還要另加模具費 3500 元！」*
  * [2:40 - 2:50] 系統展示 Pareto Logrolling：用「預付 50% 定金」換取「免除模具費 3500 元 + 鎖定產線」。工廠最終妥協同意！
  * [2:50 - 3:00] 成交達成！畫面展示 Evolus Business Agent 觸發，自動調用 Document Extractor 與 AI Tool，生成一份乾淨、具備法律效力的正式商業採購確認信歸檔。
* **旁白 (Voiceover)**：
  > *"When price hits a deadlock over a 3,500 RMB mold fee, TradeFlow activates Pareto Logrolling. It trades early cashflow liquidity — offering a 50% upfront deposit — in exchange for completely waiving the tooling fee and expediting delivery.*  
  > *Once the deal is closed at 12.80 RMB, our Evolus Business Agent automatically generates an audit-trail commercial purchase order via the Evolus Document Extractor and AI Tool chain."*

---

### 【第五幕：AMD 戰略定位與有力收官】(3:00 - 3:30) — 結尾昇華
* **畫面**：
  * [3:00 - 3:15] 切換至 PPT Slide 7 (AMD 統一 ROCm 工具鏈架構圖)。
  * [3:15 - 3:30] 切換至 GitHub 倉庫與終端 327 項測試 + 68 subtests 全綠畫面，最後定格在 Cover Image (1280×720)。
* **旁白 (Voiceover)**：
  > *"Why AMD? Our deterministic decision core is CPU-first by design — delivering sub-millisecond Stage-1 intent classification on x86-64 CPUs (measured mean ~0.13 ms, p99 ~0.49 ms) without wasteful GPU overhead.*  
  > *For semantic understanding, we call an open model served on AMD infrastructure through the hackathon's tokenfactory endpoint — verified live, at 2 to 13 seconds per reasoning call.*  
  > *And through our `_xp()` tensor abstraction, the same solver code would scale from x86-64 CPUs to AMD Instinct GPUs via ROCm — untested, because no ROCm device was available to us.*  
  > *TradeFlow AI: one person, running a cross-border trading company. Not a chatbot. Thank you."*

---

## 📋 錄製前檢查清單 (Pre-Recording Checklist)
- [ ] 本地後端已啟動：`python -m src.app.main`（或確認 8000 端口暢通）。
- [ ] 測試 API 腳本準備好或 Postman 集合已載入。
- [ ] 簡報 PPT 打開在 Slide 1 與 Slide 6。
- [ ] 錄屏軟體音訊收音測試完成，無雜音。
