# TradeFlow AI — 一間只有一個人的 AI 原生跨境貿易公司

> [!IMPORTANT]
> **原型定位與局限性前置宣告 (Upfront Prototype Disclosure)**：  
> 本專案為黑客松開發之**受約束採購決策原型（Constrained Decision Prototype）**，尚未經過真實工廠與真實供應商的閉環成交驗證。所有基準測試與對抗指標均來自**自建沙盒環境與學術文獻校準之參數化模擬**。系統決策核心採用**向量化期望最大化求解器 (Vectorized Expectimax)**，其有效性嚴格依賴先驗設定；AST 護欄僅物理約束結構化策略物件數值，自然語言渲染層依賴模板與正則過濾。詳見完整邊界審計：[LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](LIMITATIONS_AND_REAL_WORLD_FINDINGS.md)。

AMD Developer Hackathon: ACT III (lablab.ai, Online Build **2026/10/12–17**) 雙賽道參賽專案：
- **Reinvent Commerce**（商業變革主賽道）
- **Evolus Business Agents on AMD**（AMD 業務代理專項賽道）

**一句話總結**：TradeFlow 不是賣給採購部門的工具，而是**示範一間由 AI 驅動、只有一個人的跨境貿易公司如何運作**。
在資訊不對稱與供應商博弈下，以「向量化期望最大化決策引擎（Vectorized Expectimax，純數值、無 LLM 決策）」與「語意談判層（LLM 只負責表達）」解耦架構，在 AMD 運算拓撲上求解帕雷托最優讓步路徑與動態 BATNA。在遭遇單價僵局時，透過企業授權契約（Corporate Mandate）啟動非價格多維條款置換（Logrolling：定金換交期/模具全免），並以合規人機審批門禁（Compliance Air-Gap Gate）與 AST 數值硬護欄死守企業底線。

- 公開倉庫：https://github.com/Fu93/tradeflow-ai
- 戰情門戶：http://localhost:8000 （Web Portal Dashboard）
- 實證對抗與局限性報告：[LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](LIMITATIONS_AND_REAL_WORLD_FINDINGS.md)
- 真實紅隊壓力測試實錄：[REAL_WORLD_STRESS_TEST_TRANSCRIPT.md](REAL_WORLD_STRESS_TEST_TRANSCRIPT.md)
- 自博弈錦標賽基線數據（C 級合成模擬；**100 場**，即 20 matches × 5 對手原型，seed=42）：[results/self_play_benchmark.md](results/self_play_benchmark.md)
- 提交清單：[SUBMIT.md](SUBMIT.md) | 演示提案：[PITCH.md](PITCH.md) | 歷史規劃文件：[docs/archive/](docs/archive/README.md)

---

## 用戶故事：為什麼要開一間一人貿易公司？

> **過去只有兩個選擇，兩個都不好**：
> * **自己熬夜談**：用 Google 翻譯在 1688 上硬聊，工廠一聽是外國買家直接抬價 20%（¥14.80 出廠價），還看不懂工廠「模具費另算、偷換二料」的潛台詞。
> * **花 7% 佣金僱駐華中介**：中介私下向工廠吃回扣，定金被索要 80% 後產能延誤，兩頭推諉，求告無門。
>
> **TradeFlow 的答案：第三個選擇 —— 自己開一間公司，但用 Agent 當員工。**
> 經營者只有一個人。供應商訊息的理解、核價、風控與戰術生成，全部由系統完成；**但發送動作永遠由人決定**（系統不代發要約 —— AI 提議，人類定奪）。
> 首單 10,000 把雨傘，開盤 ¥14.80、博弈達成 ¥13.00，每把省 ¥1.80，合計省下 **¥18,000**（約 $2,500），零回扣、零幻覺。
>
> *\*數據來源說明：上述節省估算係依據 TradeFlow 向量化 Expectimax 在 HS6601 雨傘品類歷史價格帶之基準模擬輸出，非指單一特定實體工廠合約。*

**這正是官方 Evolus 賽道要的東西**：*"a real process that a real company runs every day"* ——
沒有比「一間公司本身」更真實的 process。

---

## 核心架構全景 (AMD 異質運算與雙 Agent 解耦拓撲)

```mermaid
flowchart TD
    subgraph BrowserClient ["端側執行與交互層 (Deterministic Headed Browser - x86-64 CPU)"]
        Browser["1688 / 阿里旺旺 / 供應商對話 (Headed Chrome)"]
        ExtContent["Content Script 風控哨兵 (Captcha Sentinel)"]
        SessionStore["SQLite WAL 會話持久化 + 狀態機"]
        Browser <--> ExtContent <--> SessionStore
    end

    subgraph NPULayer ["常駐神經策略層 (Resident Edge Policy - AMD XDNA NPU)"]
        IntentParser["InboundIntentClassifier (6大工廠套路識別)"]
        BayesCore["貝氏在線學習核心 (底價與風格後驗更新)"]
        ASTGuard["ASTMandateGuardrail (企業授權硬不變量審計)"]
        IntentParser --> BayesCore --> ASTGuard
    end

    subgraph GPULayer ["張量模擬與認知生成層 (Tensor Simulation & LLM - AMD RDNA / CDNA)"]
        MCTS96M["多供應商張量並行逆向拍賣求解器 (參數化 28.8M rollout 位)]
        Logrolling["多維條款置換引擎 (交期/定金/模具費帕雷托對)"]
        LLMBridge["AMD 託管開源模型 / Evolus 商務話術起草"]
        MCTS96M --> Logrolling --> LLMBridge
    end

    subgraph GovernanceGate ["合規檢核與遠端熔斷中樞 (Compliance & HITL Air-Gap Gate)"]
        Breaker{"安全熔斷矩陣\n(P_break >= 15% 或 阿里驗證碼)"}
        WebhookHub["Webhook Hub (asyncio.Event 異步掛起)"]
        MobilePush["手機雙向遙測 (WhatsApp / 微信 / TG 1/2/3)"]
        Breaker -->|觸發合規檢核| WebhookHub --> MobilePush
    end

    ExtContent -->|意圖文本| IntentParser
    ASTGuard -->|先驗分布| MCTS96M
    LLMBridge -->|出價草案| ASTGuard
    ASTGuard -->|合法報價指令| Breaker
    Breaker -->|安全放行| ExtContent
    MobilePush -->|手機指令 1/2/3| WebhookHub -->|解鎖回調| SessionStore
```

---

## 關鍵技術亮點

1. **張量並行求解（參數化 28,800,000 rollout 位，非執行計數）**：
   - 示範路徑 `simulate_reverse_auction()`（Web「Run Auction」預設參數 $n_{samples}=20{,}000$）的 rollout 數為 $K \times S \times N_{open} \times N_{sched}$ 的**算術乘積**：每輪 9.6M，3 輪累計 **28.8M 個張量 rollout 位**。
   - **這不是量測到的執行次數**：真正的 rollout 由**一次向量化廣播**完成，28.8M 是「被同時求值的參數組合數」（張量元素位數），不是迴圈迭代數。延遲才是量測值（單次 `solve()` 實測約 1.1 秒）。
   - rollout 位 = $K \times S \times N_{open} \times N_{sched}$；基準測試使用 50,000 樣本時為 72M；最大策略網格 (40×12) 的理論上界為 96M，但**未被任何實際路徑使用**。
   - 同時評估 $K=4$ 家供應商、隨機對手微觀參數與多組出價/讓步曲線組合。
   - MI300X 上的加速為**分析性 roofline 訪存頻寬投影**，非實機量測（開發機為 **x86-64 CPU，Intel Core Ultra 7 355**，非 AMD 硬體）。
2. **動態 BATNA 交叉壓制與 3 階戰術劃分**：
   - 即時計算排除特定對手後的外部最佳替代期望值：$\text{BATNA}_{-i}$。
   - 自動將多供應商分流為：`LEAN_IN`（傾斜鎖定最優價）、`CROSS_ANCHOR`（交叉施壓中間商）、`SIDELINE`（冷凍邊緣化報價過高者）。
3. **數值與語言解耦的 AST 硬不變量護欄 (Zero-Hallucination Guardrails)**：
   - 禁止 LLM 決定出價。決策核心輸出法定目標價後，表面生成器草稿必須通過嚴格的正則與數值邊界校驗。一旦檢測到未授權讓步詞彙或價格越界，立即切斷並降級至確定性安全模板。
4. **供應商 6 大博弈套路反制與雙語認知橋樑**：
   - 自動識別工廠的「哭窮/原料暴漲」、「以量壓價/要求加單」、「偷換規格/次料替換」、「稅率包裝拆分拆標」、「線下私聊脫離監管」與「最終妥協」。
   - 西方英語主管只需輸入純英文參數，系統自動以資深金牌買家身分博弈，杜絕「外商身分溢價」。
5. **兩段式語意管線與非阻塞設計**：
   - **Stage-1**（確定性正則 + 端側 ONNX）：實測 **~1ms**、0 tokens、完全離線。
   - **Stage-2**（AMD 託管開源模型）：實測 **2–13 秒**，故預設不啟用，避免阻塞 HTTP 請求；需要深度語意時可透過 `POST /api/v1/classify/semantic` 併行取得。
   - 此設計已通過回歸測試：`tests/test_async_stage2.py` 確保 Stage-1 永不發出網路請求。
6. **安全熔斷 (Circuit Breaker) 與手機雙向控制閉環**：
   - 破局風險 $P_{\text{break}} \ge 15\%$ 或供應商要求私下交易時瞬間掛起。
   - 透過 WhatsApp / Telegram / WeChat 自動推播，主管手機回覆 `1` (妥協)、`2` (堅持)、`3` (撤出)，遠端即時解鎖續談。

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
- **多供應商逆向拍賣看板**：一鍵點擊 `Run Auction` 觸發張量拍賣運算（參數化 28.8M rollout 位，非執行計數）。
- **遙測渠道熱重載面板**：可視化配置 WhatsApp / Telegram / WeChat Token。

### 2. 命令列工具 (CLI 功能矩陣)

```bash
# [A] 4 供應商逆向拍賣張量協同演示 (產出 results/multi_supplier_auction.md)
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

### 3. Chrome Manifest V3 瀏覽器 Copilot 側邊欄

專案內建適用於 1688 / 阿里巴巴網頁的 **Copilot 側邊欄**（非自動送約的 Autopilot）：
1. 開啟 Chrome 瀏覽器，前往 `chrome://extensions/`。
2. 開啟右上角「開發者模式 (Developer mode)」。
3. 點擊「載入未封裝項目 (Load unpacked)」，選擇專案中的 `extension/` 目錄。
4. 點擊擴充套件圖示即可在右側側邊欄（SidePanel）呼叫 TradeFlow 終端，並可開啟 `extension/mock_chat.html` 進行無風險沙盒對話測試。

> **Copilot 定位**：擴充套件**被動監聽**工廠訊息，產出戰術決策卡供採購員**手動複製後自行發送**。
> 系統不代發要約 —— 因為在即時通訊中確認價格即構成法定要約，締約權必須保留在人類手上。
> （詳見 [TARGET_ARCHITECTURE_AND_ROADMAP.md](TARGET_ARCHITECTURE_AND_ROADMAP.md) §1.1）

---

## 測試與驗證

專案具備完整測試覆蓋（包含核心邏輯、張量運算、意圖分類、API 路由、併發 CAS、非同步語意、安全護欄與 E2E 閉環）。**目前 326 項測試 + 68 subtests 全部通過**：

```bash
# 運行全量測試套件 (326 項測試 + 68 subtests 通過)
python -m pytest tests -q

# 運行真實端到端整合業務鏈測試
python verify_live.py
```

> **評測效力邊界揭露 (Evaluation Validity Disclosure)**：  
> 為維持技術誠信，我們明確區分下列兩類評測的效力：
> - `tests/test_intent_benchmark.py` 為 **in-distribution 回歸集**，其樣本與 Stage-1 正則關鍵詞高度重疊（60 條中 54 條含相同詞幹），僅用於偵測行為退化，**不可**據此宣稱泛化能力。
> - `tests/test_ood_500_benchmark.py` 評測的是 **合成對抗覆蓋集的 held-out 切分**（500 條切出 held-out 195 條，模板與 ONNX 訓練集完全不相交），**held-out 實測 Accuracy 63.08%**、P99 延遲 0.27–1.14ms（12 次執行區間）。
> - **更正紀錄**：本文件先前引用的 **92.00% 其實是訓練集分數** —— 舊版 `scripts/export_onnx_intent_model.py` 以整份 OOD-500 訓練 ONNX 分類頭，再用同一份評測；同一份模型在訓練切分上實測 92.79%、在 held-out 切分僅 63.08%，該差距即記憶成分。現行切分、訓練與門檻已全部改以 held-out 為準。
> - 兩者的斷言門檻皆已調至貼近實測值，以確保能實際攔截回歸而非流於形式。

驗證輸出結果（`python verify_live.py`）：
```text
[PASS] 1. Web Portal Dashboard GET / (Status: 200)
[PASS] 2. Static Assets (/static/css/theme.css, /static/js/app.js)
[PASS] 3. Session Created: test-session-live-01
[PASS] 4. Round 1 Cruising -> Intent: CRY_POVERTY_COST_HIKE
[PASS] 5. Round 2 Circuit Breaker Tripped -> Reason: Security escalation: OFFLINE_BYPASS_DIVERT
[PASS] 6. Mobile Webhook Command Executed -> Action: concede_step
[PASS] 7. Multi-Auction Run -> Winner: Supplier A (浙江頂尖雨具製造) (Rollout positions: 28,800,000; parameterised, not an execution count)
```

---

## 核心元件與目錄結構

| 目錄 / 模組 | 職責說明 |
|---|---|
| `src/web/` | 獨立 Web Portal 大屏路由、SSE 串流、暗黑量化終端主題與互動腳本 |
| `src/engine/model.py` | 對手微觀參數抽樣分佈與出價/讓步曲線候選網格 |
| `src/engine/solver.py` | 單對手向量化求解、貝氏更新與帕累托最優選擇 |
| `src/engine/multi_solver.py` | **多供應商張量求解器**、動態 BATNA 與 3 級戰術分流 |
| `src/engine/bilingual.py` | **跨語言認知大腦**、6 大供應商套路意圖分類器、數值 AST 硬不變量護欄、兩段式語意管線 |
| `src/engine/safe_regex.py` | 線性時間正則引擎（有界量詞，免疫 ReDoS） |
| `src/engine/onnx_classifier.py` | 端側 ONNX 意圖分類器（純本地推斷） |
| `src/benchmark/` | 對抗自對弈競技場 (`arena.py`) 與 5 大典型對手原型 (`adversaries.py`) |
| `src/session/` | 會話狀態機生命週期 (`schema.py`) 與 SQLite WAL 原子持久化管理器 (`manager.py`) |
| `src/transport/` | 可插拔傳輸抽象、手機多通道遙測通知 (`telemetry.py`) 與雙向指令中樞 (`webhook_hub.py`) |
| `src/evolus/` | Evolus REST 客戶端（AI Tools、workflow、agent chat）—— **已實連驗證** |
| `extension/` | Chrome Manifest V3 Copilot 側邊欄（被動監聽、決策卡、一鍵複製） |

---

## 賽道評分對齊 (Judging Alignment)

| 評審維度 | TradeFlow AI 實踐細節 |
|---|---|
| **Originality** | 打破「以 LLM 為決策核心」的慣例，開創「數學核心做決策，LLM 只做表面語言表達」架構。並以「**一間只有一個人的 AI 原生貿易公司**」作為產品形態，而非又一個採購 SaaS。 |
| **Business Value** | 對照傳統兩條路徑（自己熬夜談 / 7% 佣金代理），本方案為 **1 人營運**、**0% 預算擊穿**、**無中間人故無回扣風險**。可量化增益（C 級合成模擬、20 場/對手原型、共 100 場；`results/self_play_benchmark.md`）：節省率 **8.8%–20.0%**。<br>*該區間為參數化對手模型下的模擬結果；解析度仍有限（每格 20 場），且引擎在 Hard-Bargainer 上仍略遜於「每輪固定讓 5%」基準（8.8% vs 9.2%）—— 我們如實報告，不挑選有利格。* |
| **Application of Technology (AMD)** | ① 引擎為張量廣播密集型任務，`_xp()` 抽象層可自 CPU 無痛擴展至 AMD Instinct GPU（ROCm/CuPy）。② Stage-2 語意層**已實連** AMD 託管開源模型端點（實測 HTTP 200）。<br>*MI300X 時延為分析性 roofline 資料搬運下界，非實機量測，亦非執行時間預測。*<br>*開發機為 Intel Core Ultra 7 355（x86-64），本專案**未**在 AMD CPU 上執行過。* |
| **Evolus Integration** | 真實 API 呼叫已產生 usage stats：AI Tools `email` / `doc-structure` / `sentiment`（共 8.33 credits），並由 `src/app/orchestrator.py` 完成「Evolus 回呼 → 引擎 → Evolus 起草」端到端鏈路。<br>*workflow 與 agent 尚未建立，我們不宣稱已完成。* |
| **Presentation & Rigor** | 純 Python 一鍵啟動、**326 項測試 + 68 subtests**、含「評測效力邊界揭露」與完整局限性報告、生產級 SQLite WAL 與安全熔斷防護。 |

---

## 已知缺口 (Known Gaps)

為避免誤導評審，以下項目**尚未完成**：

- Evolus **workflow** 與 **agent** 尚未建立（`GET /api/v1/workflows` 與 `/agents` 回傳空陣列）。
- **MI300X 未實機量測**，目前為分析性 roofline 投影。
- **未經真實工廠閉環成交驗證**，所有指標來自沙盒與參數化模擬。
- Stage-2 語意層延遲為 **2–13 秒**，不適用於毫秒級互動路徑。

完整清單見 [SUBMIT.md](SUBMIT.md)。
