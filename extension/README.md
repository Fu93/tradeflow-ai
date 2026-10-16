# TradeFlow AI — Chrome Manifest V3 Copilot Decision Deck Extension

**TradeFlow Copilot** 是專為企業專業採購設計的智能決策副駕駛插件，支援在 1688、阿里巴巴國際站 (Alibaba TradeMessenger) 與 WhatsApp Web 等電商通訊平台進行輔助談判。

---

## 核心亮點 (Key Features)

1. **實時 DOM 串流監聽 (`MutationObserver`)**：
   - 350ms 防抖 (Debounce) 與訊息去重演算法，精準擷取供應商對話氣泡與階梯報價。
2. **採購決策雷達與商務談判牌卡 (Decision Deck HUD)**：
   - 依 `src/engine/tco.py` 計算交期延誤、超額起訂量 (MOQ) 與付款條款之非價格 (TCO) 影子成本。
     *邊界*：該模組的折算參數（年化資金成本 8%、交期延遲 0.3%/天等）為**可覆寫的預設值**，
     未以真實交易數據校準 —— 詳見 `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §十三。
   - 生成 3 條差異化實戰反擊卡（預算核定施壓、條款置換、備選廠威懾），支援**一鍵複製到剪貼簿**。
3. **人類審批門禁 (Human-in-the-Loop Approval Gate)**：
   - 預設 Copilot 模式。擴充套件**刻意不具備**自動填入聊天框或自動點擊送出的能力——
     草稿一律由採購員自行貼上並手動發送，從源頭杜絕偽造合成事件導致的平台風控，以及非預期的法定締約。
     （`content.js` 的 `findChatInput()` / `findSendButton()` 已移除，並留有註解說明原因。）
4. **安全熔斷機制 (Safety Circuit Breaker)**：
   - 當求解器回傳破局機率 $\ge 15\%$（`src/app/main.py:713` 的實際門檻）時，自動警示並提供手機端授權通道。
5. **量化風格側邊欄 (Chrome SidePanel API)**：
   - 即時顯示貝氏後驗底價期望 $\mu$、不確定性 $\sigma$、破局機率進度條。
     *邊界*：$\mu/\sigma$ 為先驗驅動的後驗更新結果，準確性受先驗設定影響；
     面板上的「置信度」是**信念收斂度指標**（belief convergence metric），
     **不是**統計校準機率（見 `LIMITATIONS` §一）。
   - 支援 1（妥協讓步）、2（堅持）、3（緊急撤退）的手動戰術干預。

---

## 30 秒安裝與加載指南 (Quick Setup)

1. **啟動 TradeFlow 後端服務**：
   ```bash
   python -m uvicorn src.app.main:app --port 8000 --reload
   ```
2. **在 Chrome 瀏覽器加載插件**：
   - 打開 Chrome，網址列輸入 `chrome://extensions`
   - 右上角開啟 **「開發人員模式 (Developer mode)」**
   - 點擊左上角 **「載入未封裝項目 (Load unpacked)」**
   - 選擇本專案中的 `tradeflow-ai/extension` 資料夾
3. **開啟測試沙盒或目標網站（二選一）**：

   **方式 A（建議，不需任何授權）— 以 http 提供沙盒頁**：
   ```bash
   python -m http.server 8080 --directory extension
   ```
   再開啟 `http://localhost:8080/mock_chat.html`。`manifest.json` 的
   `content_scripts.matches` 已涵蓋 `http://localhost/*`，HUD 會直接注入，
   不需勾選任何額外的檔案存取權限（演示機最省事的一條路）。

   **方式 B — 直接用 `file://` 開啟 `extension/mock_chat.html`**：
   > [必要前置條件] Chrome **預設不允許**擴充在 `file://` 頁面執行 content script。
   > 若略過此步驟，畫面會完全看不到 HUD 浮標（擴充看起來像沒載入）。
   1. 網址列輸入 `chrome://extensions`
   2. 找到「TradeFlow Copilot — Procurement Decision Deck」 → 點擊 **「詳細資料」**
   3. 打開 **「允許存取檔案網址 (Allow access to file URLs)」** 開關
   4. 重新載入 `extension/mock_chat.html`（或按 F5），HUD 才會注入

   - 右下角即會浮現 TradeFlow HUD 浮標。
   - 點擊瀏覽器工具列上的 TradeFlow 圖標，即可展開側邊儀表板 (SidePanel)。
   - 沙盒頁的「模擬阿里風控滑塊」按鈕可驗證合規門禁：HUD 會轉為
     `CAPTCHA DETECTED`，點擊滑塊完成驗證後會自動恢復 `COPILOT RADAR ON`
     並重新註冊本分頁的 session 綁定。
