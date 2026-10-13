# TradeFlow AI — Chrome Manifest V3 Autopilot Extension

**TradeFlow Autopilot** 是專為企業採購談判設計的無人值守瀏覽器巡航插件，支援在 1688、阿里巴巴國際站 (Alibaba TradeMessenger) 與 WhatsApp Web 等電商通訊平台進行自主談判。

---

## 核心亮點 (Key Features)

1. **實時 DOM 串流監聽 (`MutationObserver`)**：
   - 400ms 防抖 (Debounce) 與 SHA 訊息去重演算法，精準擷取供應商對話氣泡與階梯報價。
2. **擬人化防風控工程 (Anti-Bot Emulation)**：
   - **閱讀延遲**：根據報價文字長度自適應延遲 $T_{delay} = 1200 + L \times 25 + \text{Uniform}(-300, 450)$ ms。
   - **鍵盤微打字抖動**：模擬真實人類按鍵節奏（40 ~ 130ms / 字元）。
   - **Vue / React 虛擬 DOM 原生事件穿透**：直接觸發底層 prototype setter 與 synthetic `input` / `change` 事件。
3. **安全熔斷機制 (Safety Circuit Breaker)**：
   - 當蒙地卡羅重解偵測破局風險 $\ge 15\%$ 時，自動鎖死輸入框、HUD 轉為警示紅框、暫停談判並透過 WhatsApp / WeChat 發送遠端遙測警報。
4. **極致 FinTech 量化風格側邊欄 (Chrome SidePanel API)**：
   - 即時顯示貝氏後驗底價期望 $\mu$、不確定性 $\sigma$、破局機率進度條、以及多通道手機推播狀態。
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
3. **開啟測試沙盒或目標網站**：
   - 在瀏覽器中打開測試沙盒：`file:///c:/Users/nn201/Desktop/tradeflow-ai/extension/mock_chat.html`
   - 右下角即會浮現 TradeFlow HUD 自駕浮標。
   - 點擊瀏覽器工具列上的 TradeFlow 圖標，即可展開側邊儀表板 (SidePanel)！
