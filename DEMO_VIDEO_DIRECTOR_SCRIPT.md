# TradeFlow AI — 3分30秒 Demo 影片全流程分鏡導演腳本
(Shot-by-Shot Director's Guide for Hackathon Video Recording)

> **核心目標**：在 3 分 30 秒內，讓 AMD 與 Evolus 評審徹底看懂：**這不是一個聊天機器人，而是一個能幫跨境買家 Alex 真金白銀省下幾千美元、有 AST 語法樹硬護欄防禦資金風險的量化決策副駕駛。**

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
  * [0:00 - 0:15] 簡報 Slide 1 (TradeFlow AI 標題) 轉 Slide 6 (Alex Rivera 的真實故事卡片)。
  * [0:15 - 0:35] 畫面上展示兩組強烈對比字樣：
    * ❌ *Traditional Way*: Google Translate on 1688 (20% Foreigner Markup) + Shady Agent (7% Fee + Kickbacks).
    * ✅ *TradeFlow Way*: Autonomous Coastal Jargon + AST Safety Guardrail + WhatsApp CPO Card.
* **旁白 (Voiceover)**：
  > *"Meet Alex Rivera. He runs a 3-million-dollar outdoor brand on Amazon in Los Angeles, sourcing 50,000 umbrellas from China every year.*  
  > *Until now, Alex had two painful choices: Either stay up all night using Google Translate on 1688, where Chinese factories instantly mark up prices by 20% because he sounds like an outsider; Or pay a 7% commission to a local sourcing agent, who secretly takes kickbacks from the factory and demands an 80% deposit with zero accountability.*  
  > *This is why we built **TradeFlow AI** — a constrained procurement decision engine that combines verifiable game theory with authentic local supplier jargon."*

---

### 【第二幕：數學自駕與地道行話】(0:35 - 1:25) — 展示核心算力與去外商溢價
* **畫面**：
  * [0:35 - 0:50] 切換至瀏覽器畫面 (http://localhost:8000 或 API 測試介面)。展示自動傘規格：目標價 ¥12.00，上限 ¥15.00，MOQ 1000 把。
  * [0:50 - 1:10] 點擊發送工廠 Round 1 報價：工廠開出 ¥14.80，聲稱「碰擊布和塗層原料暴漲30%」。
  * [1:10 - 1:25] 畫面高亮顯示：後驗底價在 2ms 內完成貝氏更新，TradeFlow 自動輸出地道行話：
    > *「老闆，行情波動大家心裡有數，核算過BOM表材料佔比，¥13.12 出廠價給貴廠留足了利潤。能接我們今天鎖定排單，別為幾毛錢耽誤檔期。」*
* **旁白 (Voiceover)**：
  > *"Watch this live: When the factory opens at 14.80 RMB crying poverty about raw material hikes, TradeFlow doesn't hallucinate. In less than 2 milliseconds, our NumPy-vectorized Expectimax engine updates its Bayesian belief on the supplier's reserve price and solves for the optimal concession curve.*  
  > *Instead of polite textbook English, our surface renderer speaks fluent coastal factory jargon — anchoring directly on raw BOM material ratios to strip away the foreign buyer markup instantly."*

---

### 【第三幕：硬碰硬對抗與 AST 現場熔斷】(1:25 - 2:20) — 展示真正護欄與高管審批
* **畫面**：
  * [1:25 - 1:45] 工廠出盤 Round 3 陷阱：*「加我私人微信 13857691234 私下轉帳，我給你便宜到 13.80！」*
  * [1:45 - 2:00] **高潮畫面**：系統控制台瞬間跳出紅色警報 **`CIRCUIT TRIPPED: OFFLINE_BYPASS_DIVERT`**！所有未經授權的對話被物理阻斷！
  * [2:00 - 2:15] 畫面切換至 Alex 的手機螢幕：WhatsApp 收到結構化英文 CPO 決策卡（含風險指數 7.2%、TCO 等效分析、授權指令 1/2/3）。
  * [2:15 - 2:20] 點擊手機回覆 `1` (CONCEDE_STEP)，系統瞬間解鎖，狀態機平穩恢復自駕。
* **旁白 (Voiceover)**：
  > *"Now, the adversarial test. The factory attempts an off-platform diversion: 'Add my personal WeChat for an offline discount.'*  
  > *A standard LLM might comply and leak private data. But TradeFlow's deterministic intent classifier trips immediately. The engine freezes all outbound messages, preventing unauthorized commitments.*  
  > *Simultaneously, an executive telemetry card is pushed to Alex's WhatsApp in Los Angeles. Alex doesn't have to negotiate; he just reviews the structured risk telemetry and taps '1' to authorize the next concession step. Full human-in-the-loop control, zero cognitive overload."*

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
  * [3:15 - 3:30] 切換至 GitHub 倉庫與終端 72/72 測試全綠畫面，最後定格在 Cover Image (1280×720)。
* **旁白 (Voiceover)**：
  > *"Why AMD? Our decision core is CPU-first by design — delivering sub-2-millisecond responses on AMD Zen CPUs without wasteful GPU overhead.*  
  > *The real value of AMD Instinct MI300X lies in the unified ROCm stack. Through our `_xp()` tensor abstraction, the exact same code scales from edge CPUs to enterprise-scale 4-supplier reverse auctions, co-locating Qwen models and tensor solvers in 5.3 TB/s HBM3 memory with zero PCIe penalties.*  
  > *TradeFlow AI: Procurement negotiation, quantified — not a chatbot. Thank you."*

---

## 📋 錄製前檢查清單 (Pre-Recording Checklist)
- [ ] 本地後端已啟動：`python -m src.app.main`（或確認 8000 端口暢通）。
- [ ] 測試 API 腳本準備好或 Postman 集合已載入。
- [ ] 簡報 PPT 打開在 Slide 1 與 Slide 6。
- [ ] 錄屏軟體音訊收音測試完成，無雜音。
