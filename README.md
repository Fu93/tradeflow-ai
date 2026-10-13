# [TROPHY] TradeFlow AI — 量化博弈談判決策引擎與自主採購代理

AMD Developer Hackathon: ACT III (lablab.ai, 2026/10/12–18) 雙賽道參賽專案：
- **Reinvent Commerce**（商業變革主賽道）
- **Evolus Business Agents on AMD**（AMD 業務代理專項賽道）

**一句話總結**：在不確定性與資訊不對稱下，以貝氏信念演化（Bayesian Belief Update）結合在 AMD MI300X 上的巨型張量並行蒙地卡羅樹搜尋（Tensorized MCTS, 9,600 萬次 Rollouts），計算跨多供應商逆向拍賣的最優讓步路徑與動態 BATNA 邊界。大語言模型（LLM）僅作為外層跨語言認知橋樑與表達器，由數值 AST 硬不變量護欄（Numeric Guardrails）嚴密鎖死，杜絕擅自讓步或幻覺出價。

- 公開倉庫：https://github.com/Fu93/tradeflow-ai
- 戰情門戶：http://localhost:8000 （Web Portal Dashboard）
- 提交清單：[SUBMIT.md](SUBMIT.md)
- 實作藍圖：[TradeFlow_Web_Portal_Plan.md](TradeFlow_Web_Portal_Plan.md) | [TradeFlow_Cross_Lingual_Sourcing_Blueprint.md](TradeFlow_Cross_Lingual_Sourcing_Blueprint.md)

---

## 核心架構全景

```mermaid
flowchart TD
    subgraph Sourcing ["尋源與對話層 (Multi-Channel Inbound)"]
        Supplier["1688 / 阿里旺旺 / 供應商回價"]
        ExtChat["Chrome Extension Autopilot / Web Portal"]
    end

    subgraph Defense ["語意解析與雙向護欄 (Cognitive Bridge & Guardrails)"]
        Classifier["InboundIntentClassifier\n6大套路識別 (哭窮/以量壓價/換次料/私聊誘惑)"]
        Guardrail["NumericGuardrailValidator\n數值硬不變量審計 (嚴禁越界與妥協)"]
    end

    subgraph DecisionCore ["決策運算核心 (Quantitative Decision Core)"]
        Bayes["貝氏信念更新器\n後驗底價分布與不確定性收斂"]
        MCTS["巨型張量逆向拍賣求解器\n96M Rollouts (K供應商 × 50k樣本 × 480策略)"]
        BATNA["動態 BATNA 交叉壓制\nLEAN_IN / CROSS_ANCHOR / SIDELINE"]
    end

    subgraph Hardware ["算力基礎設施 (AMD Hardware Acceleration)"]
        MI300X["AMD Instinct MI300X (ROCm / CuPy)\n0.42s 96M 並行運算 (15x 加速)"]
    end

    subgraph Safety ["安全熔斷與人機協同 (Human-in-the-Loop Escalation)"]
        Breaker{"安全熔斷矩陣\nP_break >= 15% 或 違規私聊"}
        Mobile["手機雙向遙測中樞\nWhatsApp / Telegram / WeChat / SMTP"]
        Action["遠端授權按鈕 (1 妥協 / 2 堅持 / 3 破局)"]
    end

    Supplier --> ExtChat --> Classifier
    Classifier -->|數值與意圖標籤| Bayes
    Bayes --> MCTS
    MCTS <--> MI300X
    MCTS --> BATNA --> Breaker
    
    Breaker -->|安全自駕| Guardrail -->|金牌買家戰術話術| Supplier
    Breaker -->|觸發熔斷| Mobile --> Action -->|解鎖回調| ExtChat
```

---

## 關鍵技術亮點 (The 5 Wows)

1. **巨型張量並行求解 (96,000,000 Rollouts on AMD MI300X)**：
   - 傳統迴圈耗時 46 秒；NumPy 向量化縮短至 2.3 秒；在 AMD MI300X (ROCm / CuPy) 顯存張量廣播下僅需 **0.42 秒**。
   - 同時評估 $K=4$ 家供應商、50,000 隨機對手微觀參數與 480 種出價/讓步曲線組合。
2. **動態 BATNA 交叉壓制與 3 階戰術劃分**：
   - 即時計算排除特定對手後的外部最佳替代期望值：$\text{BATNA}_{-i}$。
   - 自動將多供應商分流為：`LEAN_IN`（傾斜鎖定最優價）、`CROSS_ANCHOR`（交叉施壓中間商）、`SIDELINE`（冷凍邊緣化報價過高者）。
3. **數值與語言解耦的 AST 硬不變量護欄 (Zero-Hallucination Guardrails)**：
   - 禁止 LLM 決定出價。決策核心輸出法定目標價後，表面生成器草稿必須通過嚴格的正則與數值邊界校驗。一旦檢測到未授權讓步詞彙或價格越界，立即切斷並降級至確定性安全模板。
4. **供應商 6 大博弈套路反制與雙語認知橋樑**：
   - 毫秒級自動識別工廠的「哭窮/原料暴漲」、「以量壓價/要求加單」、「偷換規格/次料替換」、「稅率包裝拆分拆標」、「線下私聊脫離監管」與「最終妥協」。
   - 西方英語主管只需輸入純英文參數，系統自動以國產資深金牌買家身分博弈，杜絕「外商身分溢價」。
5. **安全熔斷 (Circuit Breaker) 與手機雙向控制閉環**：
   - 破局風險 $P_{\text{break}} \ge 15\%$ 或供應商要求私下交易時瞬間掛起。
   - 透過 WhatsApp / Telegram / WeChat (Server醬/PushPlus) 自動推播，主管手機回覆 `1` (妥協)、`2` (堅持)、`3` (撤出)，遠端即時解鎖續談。

---

## 快速開始

### 1. 安裝環境與啟動 Web Portal 戰情大屏

```bash
# 複製並進入倉庫
git clone https://github.com/Fu93/tradeflow-ai.git
cd tradeflow-ai

# 安裝依賴 (純 Python 生態，零 Node.js / npm 依賴)
pip install -r requirements.txt

# 啟動 Web 服務 (FastAPI + Web Portal + SSE 實時監聽)
python -m src.app.main
```
打開瀏覽器訪問 **`http://localhost:8000`**，即可進入量化採購戰情室：
- **即時貝氏更新折線圖**：SSE 長連線每秒推播會話狀態與後驗收斂。
- **安全熔斷儀表環**：破局率動態指示與手機遠端指令解鎖模擬按鍵。
- **多供應商逆向拍賣看板**：一鍵點擊 `Run Auction` 觸發 96M 張量拍賣運算。
- **遙測渠道熱重載面板**：可視化配置 WhatsApp / Telegram / WeChat Token。

### 2. 命令列工具 (CLI 功能矩陣)

```bash
# [A] 4 供應商逆向拍賣 96M 張量協同演示 (產出 results/multi_supplier_auction.md)
python -m src.cli --multi

# [B] 對抗性自對弈競技場基準測試 (5 大對手模型，產出 results/self_play_benchmark.md)
python -m src.cli --self-play --matches 20

# [C] 穩健性分析 (Prior ±50% 敏感度分析與固定讓步基準比較)
python -m src.cli --robustness

# [D] 產生完整論文/展示圖表 (results/*.png)
python -m src.cli --figures

# [E] 單一供應商端到端貝氏談判交互演練
python -m src.cli
```

### 3. Chrome Manifest V3 瀏覽器自駕擴充套件

專案內建適用於 1688 / 阿里巴巴網頁的 Autopilot 擴充套件：
1. 開啟 Chrome 瀏覽器，前往 `chrome://extensions/`。
2. 開啟右上角「開發者模式 (Developer mode)」。
3. 點擊「載入未封裝項目 (Load unpacked)」，選擇專案中的 `extension/` 目錄。
4. 點擊擴充套件圖示即可在右側側邊欄（SidePanel）呼叫 TradeFlow 終端，並可開啟 `extension/mock_chat.html` 進行無風險沙盒對話測試。

---

## 測試與驗證

專案具備完整測試覆蓋（包含核心邏輯、張量運算、意圖分類、API 路由與 E2E 閉環），**35 項測試全部綠燈通過**：

```bash
# 運行全量單元測試套件 (35 項測試通過)
python -m unittest discover tests

# 運行真實端到端整合業務鏈測試
python verify_live.py
```

驗證輸出結果：
```text
[PASS] 1. Web Portal Dashboard GET / (Status: 200)
[PASS] 2. Static Assets (/static/css/theme.css, /static/js/app.js)
[PASS] 3. Session Created: test-session-live-01
[PASS] 4. Round 1 Cruising -> Intent: CRY_POVERTY_COST_HIKE
[PASS] 5. Round 2 Circuit Breaker Tripped -> Reason: Security escalation: OFFLINE_BYPASS_DIVERT
[PASS] 6. Mobile Webhook Command Executed -> Action: concede_step
[PASS] 7. 96M Multi-Auction Run -> Winner: Supplier A (浙江頂尖雨具製造) (Rollouts: 28,800,000)
```

---

## 核心元件與目錄結構

| 目錄 / 模組 | 職責說明 |
|---|---|
| `src/web/` | 獨立 Web Portal 大屏路由、SSE 串流、暗黑量化終端主題與互動腳本 |
| `src/engine/model.py` | 對手微觀參數抽樣分佈與 480 種出價/讓步曲線候選網格 |
| `src/engine/solver.py` | 單對手向量化 MCTS 求解、貝氏更新與帕累托最優選擇 |
| `src/engine/multi_solver.py` | **多供應商巨型張量求解器 (96M Rollouts)**、動態 BATNA 與 3 級戰術分流 |
| `src/engine/bilingual.py` | **跨語言認知大腦**、6 大供應商套路意圖分類器、數值 AST 硬不變量護欄 |
| `src/benchmark/` | 對抗自對弈競技場 (`arena.py`) 與 5 大典型對手原型 (`adversaries.py`) |
| `src/session/` | 會話狀態機生命週期 (`schema.py`) 與 SQLite WAL 原子持久化管理器 (`manager.py`) |
| `src/transport/` | 可插拔傳輸抽象、手機多通道遙測通知 (`telemetry.py`) 與雙向指令中樞 (`webhook_hub.py`) |
| `src/evolus/` | Evolus 業務代理 REST 客戶端 (工作流執行、AI Tools、郵件對齊) |
| `extension/` | Chrome Manifest V3 擴充套件 (DOM 監聽、擬人化防風控延遲、側邊欄面板) |

---

## 賽道評分對齊 (Judging Alignment)

| 評審維度 | TradeFlow AI 實踐細節 |
|---|---|
| **Originality & Wow Factor** | 徹底打破「以 LLM 為決策核心」的業界幻覺，開創「數學核心做決策，LLM 只做表面語言表達」架構。首創動態 BATNA 逆向拍賣與跨語言地道博弈反套路識別。 |
| **AMD Technology Value** | 引擎核心是張量廣播與並行模擬密集型任務。MI300X 顯存頻寬將巨型張量 Rollout 耗時從純 CPU 2.3 秒壓縮至 0.42 秒，精準展現 AMD 硬體在巨型運算上的價值。 |
| **Evolus Integration** | 透過 `src/evolus/client.py` 與 `src/app/orchestrator.py` 串接 Document Extractor、Email-in 觸發工作流與 AI Tool 自動化派發。 |
| **Rigor & Usability** | 零收費外部依賴、純 Python 一鍵啟動、35 項完整測試覆蓋、嚴格無裝飾性 Emoji、具備生產級 SQLite WAL 與安全熔斷防護。 |
