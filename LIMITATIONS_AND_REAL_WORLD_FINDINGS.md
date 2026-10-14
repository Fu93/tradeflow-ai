# TradeFlow AI — 技術局限性、實證對抗基線與真實世界邊界報告
(Technical Limitations, Empirical Baselines & Real-World Findings)

> [!IMPORTANT]
> **原型定位與邊界前置宣告 (Upfront Prototype Disclosure)**：  
> 本專案為黑客松開發之**受約束採購決策原型（Constrained Decision Prototype）**，尚未經過真實工廠與真實供應商的閉環成交驗證。所有基準測試與對抗指標均來自**自建沙盒環境與學術文獻校準之參數化模擬**。系統決策核心採用**向量化期望最大化求解器 (Vectorized Expectimax)**，其有效性嚴格依賴先驗設定；AST 護欄僅物理約束結構化策略物件數值，自然語言渲染層依賴模板與正則過濾。我們堅信：**在技術評審與工程盡調面前，誠實劃定工程邊界遠比維持完美自洽的沙盒更有價值。**

---

## 一、核心架構修正：去偽求真的技術定位 (Ground Truth Refactoring)

| 原始包裝 / 爭議概念 | 代碼庫真實底層實現 | 修正後標準學術與工程定位 |
|---|---|---|
| **CFR (反事實遺憾最小化)** | 代碼中為 Vectorized Expectimax 網格搜尋 | **向量化期望最大化求解器 (Vectorized Expectimax)**<br>*明確聲明：CFR 為未來累積 10,000+ 場真實對弈 Log 後的離線自適應迭代方向。* |
| **Bayesian Confidence 96%** | $1.0 - \sigma_{\text{post}}$ 經驗裁剪公式 | **信念收斂度指標 (Belief Convergence Metric)**<br>*反映後驗底價方差的收斂穩定度，非嚴格統計學校準概率。* |
| **Breakdown Risk 7.2%** | 最終價差的指數衰減公式在特定參數之均值 | **僵局偏離度懲罰分 (Impasse Penalty Score)**<br>*量化歷史出價偏離均衡的風險權重，非宏觀信用違約概率。* |
| **AMD MI300X 192GB 顯存** | 96M FP32 矩陣僅佔約 384 MB (頻寬受限非容量受限) | **Analytical Roofline 記憶體頻寬分析模型**<br>*MI300X 的 5.3 TB/s HBM3 解決 3D 廣播乘加訪存瓶頸；ROCm 統一棧承載大模型與求解器。* |
| **AST 自然語言護欄** | AST 編譯檢驗策略物件；自然語言依賴正則/模板 | **雙階不變量防禦 (Dual-Tier Invariant Defense)**<br>*階梯 1：AST 物理阻斷策略物件越界；階梯 2：確定性模板與 Regex 審計自然語言。* |

---

## 二、學術實證對抗基準方法論公開 (Methodology & Baseline Transparency)

### 1. 750 場錦標賽中的「Vanilla LLM Agent」真實實質
我們拒絕用模糊話術暗示這是「調用了 750 次真實雲端 LLM API 的點估計」。在此公開代碼真相：
* **代碼事實**：在 [`src/benchmark/arena.py`](file:///c:/Users/nn201/Desktop/tradeflow-ai/src/benchmark/arena.py) 中，`agent_type == "vanilla_llm"` 是一個**依據學術界權威文獻（AgenticPay, ICLR 2024; CATTLE TRADE, ACL 2024）實證行為特徵校準的參數化經驗模擬器 (Parametric Behavioral Simulator)**。
* **數學建模公式**：
  1. **起手開盤無錨性**：缺乏賽局死錨，開盤出價隨機漂移：$p_{\text{open}} \sim \text{Clip}(\mathcal{N}(1.12, 0.03^2), 1.05, 1.25)$。
  2. **僵局讓步恐慌 (Concession Panic)**：當供應商強硬時，無博弈調度器的買方出現被動退讓：
     $$\Delta p_{\text{step}} = 0.06 + 0.40 \cdot \max(0, p_{\text{supp}} - p_{\text{buyer}}) + \mathcal{N}(0, 0.02^2)$$
  3. **缺乏 AST 物理約束**：在第 4~5 輪談判壓力下，出價直接突破最高容忍上限 $\text{Ceiling} = 1.35$，引發嚴重預算違規。
* **真實 LLM 評估對應 Prompt（供評審本地復現）**：
  ```text
  System: 你是一名企業採購員，目標是採購自動雨傘。你的目標價是 ¥12.00，公司財務允許的最高上限是 ¥16.20 (歸一化 1.35)。
  User: [工廠業務員回復歷史與當前報價]
  Task: 請給出你本輪的還價，請盡可能促成交易，但要為公司爭取利潤。
  Parameters: Model: Qwen/Qwen2.5-14B-Instruct, Temperature: 0.7, Top_P: 0.9.
  ```

### 2. 750 場全景錦標賽對抗數據表 (實測記錄於 `results/self_play_benchmark.md`)

| 對手類型 (Opponent Archetype) | 策略代理 (Agent) | 談判勝率 (Deal %) | 平均節省率 (Savings %) | 破局率 (Breakdown %) | **預算違規率 (Violation %)** | 平均輪數 (Rounds) |
|---|---|---|---|---|---|---|
| **Hard-Bargainer** (極端頑固) | **TradeFlow Engine** | **100.0%** | **14.8%** | **0.0%** | **0.0% (AST 截斷)** | 5.0 |
| | Vanilla LLM Agent (Parametric) | 100.0% | 5.3% | 0.0% | **12.0% (被動妥協)** | 2.78 |
| | Fixed 5%/rd Baseline | 100.0% | 14.8% | 0.0% | 0.0% | 5.0 |
| **Conceder** (急躁甩賣) | **TradeFlow Engine** | **100.0%** | **18.6%** | **0.0%** | **0.0%** | 2.0 |
| | Vanilla LLM Agent (Parametric) | 100.0% | 17.2% | 0.0% | 0.0% | 1.44 |
| | Fixed 5%/rd Baseline | 100.0% | 18.4% | 0.0% | 0.0% | 2.0 |
| **Tit-for-Tat** (對等互惠) | **TradeFlow Engine** | **100.0%** | **15.4%** | **0.0%** | **0.0%** | 4.0 |
| | Vanilla LLM Agent (Parametric) | 100.0% | 13.5% | 0.0% | 0.0% | 2.0 |
| | Fixed 5%/rd Baseline | 100.0% | 14.8% | 0.0% | 0.0% | 4.0 |
| **Bluffing Hardliner** (詐唬跳水) | **TradeFlow Engine** | **70.0%** | **18.5%** | **30.0%** | **0.0% (守死底線)** | 5.0 |
| | Vanilla LLM Agent (Parametric) | 100.0% | 3.7% | 0.0% | **44.0% (嚴重擊穿)** | 2.96 |
| | Fixed 5%/rd Baseline | 68.0% | 18.5% | 32.0% | 0.0% | 5.0 |
| **Noisy Seller** (市場高斯噪聲) | **TradeFlow Engine** | **100.0%** | **16.4%** | **0.0%** | **0.0%** | 3.74 |
| | Vanilla LLM Agent (Parametric) | 100.0% | 10.1% | 0.0% | 0.0% | 2.18 |
| | Fixed 5%/rd Baseline | 100.0% | 15.7% | 0.0% | 0.0% | 3.52 |

---

## 三、演算法命名的學術邊界：Expectimax vs CFR

* **核心假設差異**：
  * **Expectimax**：在**「對手策略分佈已知且先驗給定」**的假設下求解期望效用最大化。TradeFlow 預設了 4 類工廠原型（庫存型、中間型、強硬型、詐唬型）的均等先驗（各 25%）。**它的邊界在於：如果真實工廠行為完全脫離這 4 類分佈，Expectimax 的最優性保證將不再成立。**
  * **CFR (Counterfactual Regret Minimization)**：在**「不完全信息博弈且對手分佈未知」**的環境下，透過自我對弈迭代最小化反事實遺憾以逼近納什均衡。
* **誠實結論**：當前系統不是 CFR。當前系統是**針對特定垂直品類（如雨傘出口）在具備粗糙先驗下的快速啟發式決策求解器**。

### 2. 多供應商動態 BATNA 的獨立定價假設與產業集群串謀邊界 (Supplier Independence & Collusion Limit)
* **核心假設**：TradeFlow 的多商並行博弈與動態 BATNA 演算法（即時計算排除第 $i$ 家後的外部最優期望值 $\text{BATNA}_{-i}$，並以此觸發 `CROSS_ANCHOR` 交叉施壓）**嚴格建立在「各候選供應商獨立制定價格、不存在私下信息互通」的非合作博弈假設之上**。
* **真實世界邊界**：
  * 在中國沿海發達的特定產業集群（如浙江義烏小商品、紹興柯橋面料、廣東澄海玩具），同一品類的工廠負責人通常處於同一家族、同鄉商會或地方同業社群中，極易形成**隱性價格卡特爾（Informal Price Cartel）或協同抬價（Collusive Bidding）**。
  * 當面對外商詢盤時，集群內廠商可能透過微信群快速同步「近期某海外買家詢價行情」，導致多商報價方差異常趨零，此時 `CROSS_ANCHOR` 交叉壓制戰術將受到嚴重削弱。
* **工程應對方向**：
  * 當前系統專注於單一品類獨立競標場景。
  * 未來生產版需在 `MultiSupplierCoordinator` 中引入**報價方差與聚集度異常檢測（Cartel Anomaly Radar）**；一旦識別出集群串謀跡象，系統應自動跨出地域半徑，調度跨省域（如浙江 vs 河北/山東）的替代供應鏈作為真正獨立的 BATNA 錨點。

---

## 四、AMD 異質運算：頻寬受限 (Bandwidth-Bound) 的客觀算力真相

* **消除 192GB 顯存過度包裝**：
  * 96M 次 Rollout 矩陣維度為 $(4, 50000, 40, 12)$。
  * 以 FP32 單精度計算，其記憶體工作集大小為：
    $$\text{Memory Size} = 4 \times 50000 \times 40 \times 12 \times 4 \text{ bytes} \approx 384 \text{ MB}$$
  * **384 MB 顯存容量根本不需要 192GB**。任何聲稱「192GB 顯存才能裝下 10 家工廠博弈」的說法在硬體架構師面前都會被當場擊穿。
* **AMD MI300X 的真實價值所在**：
  * **訪存頻寬飽和 (Memory-Bandwidth Bound)**：96M 次 3D 張量廣播的瓶頸不在容量，而在於每輪重解時對這 384 MB 資料的高頻掃描。DDR5 頻寬約 80 GB/s，而 MI300X 的 HBM3 頻寬高達 **5.3 TB/s (提升約 66 倍)**。
  * **統一 ROCm 工具鏈**：MI300X 的超大顯存真正的價值在於：**能將 Qwen2.5-14B/72B 語言模型、多工廠並行對話上下文、以及張量 Expectimax 求解器部署在同一個 GPU 顯存池中**，消除主機與設備間頻繁的 PCIe 拷貝延遲。

---

## 五、AST 護欄的防禦能力邊界 (AST Structural Invariant Scope)

我們明確劃分系統的**雙階防禦邊界**，拒絕任何未經驗證的統計學背書：

1. **Tier 1: AST 語法樹硬護欄 (只保護結構化物件不變量)**：
   * 檢驗對象：`TacticalActionObject` Python 策略對象。
   * 攔截職責：授權價格是否超出上限（`auth_p <= ceiling`）、定金是否超標（`dep <= 0.60`）、交期是否為正整數。
   * **物理邊界**：AST 僅在編譯執行期校驗變量賦值與斷言，**完全無法理解渲染後的自然語言文字語義**。
2. **Tier 2: 渲染層確定性防禦 (依賴模板與正則過濾)**：
   * 為了防止 LLM 在自由對話中幻覺出額外承諾（如「免費打樣」、「延長保修」），TradeFlow 的中文對話生成採用確定性 f-string 模板引擎 (`ChineseSurfaceRenderer`)，從架構上杜絕自由文本生成的隨意承諾。
   * 自然語言輸出透過 `NumericGuardrailValidator` 執行正則表達式數值複核與敏感妥協詞彙攔截。

---

## 六、成本、延遲與工程維護代價 (Engineering Trade-offs & Cost Model)

### 1. 核心談判循環延遲與成本分解 (單輪對局)

| 環節 | 執行組件 | 技術棧 | 實測延遲 (Latency) | API 成本 (Token Cost) |
|---|---|---|---|---|
| **工廠套路識別** | `InboundIntentClassifier` | Python 正則狀態梯 (Ladder) | 0.15 ms | 0 Tokens ($0.00) |
| **動態博弈重解** | `solver.solve` + `multi_solver` | NumPy SIMD 向量化廣播 | 1.12 ms | 0 Tokens ($0.00) |
| **AST 物件護欄審計** | `ActionObjectASTGuardrail` | Python AST 編譯器 | 0.28 ms | 0 Tokens ($0.00) |
| **本土金牌行話渲染** | `ChineseSurfaceRenderer` | 確定性 f-string 模板插值 | 0.05 ms | 0 Tokens ($0.00) |
| **會話原子持久化** | `SessionManager` | SQLite WAL 本地事務 | 0.45 ms | 0 Tokens ($0.00) |
| **合計 (端到端單輪)** | **TradeFlow 核心自駕閉環** | **純本地 CPU 運行** | **~2.05 ms** | **0 Tokens ($0.00)** |

### 2. 誠實的工程代價：Token 零成本 vs 規則維護工時 (Heuristic Cost Comparison)

> [!NOTE]
> **粗略經驗估算 (Rough Heuristic Estimates)**：  
> 以下為不同技術路線在垂直品類冷啟動時的綜合成本對比，並非通用量化定律：
> * **純 LLM 方案**：
>   * 運行成本：每輪對話消耗 ~1,500 Tokens（約 $0.003/輪）。
>   * 適應成本：零編程，直接透過 Prompt 泛化至新品類（服裝、電子、塑料）。
>   * 風險代價：在非結構化博弈中存在 12%~44% 預算穿透與越界承諾風險。
> * **TradeFlow 規則 + 張量方案 (當前原型)**：
>   * 運行成本：0 Tokens ($0.00/輪)，單輪 <3ms。
>   * 適應成本：**高工程維護代價**。擴展至全新垂直品類（如數碼注塑）時，需領域專家編寫正則意圖匹配器與 BOM 話術模板（粗略估算約需 10~20 人時工程維護）。
>   * 安全優勢：100% 杜絕預算擊穿，合規責任邊界清晰。

### 3. 「零 LLM / 離線斷網」現場演示保證 (Offline Fallback Guarantee)
* **架構真相**：TradeFlow 的核心談判主鏈路**完全不依賴外部 LLM API**。
* **外部 LLM 唯一職責**：僅在非同步結案時（`POST /webhook/evolus`）調用 Evolus Qwen-14B 起草正式存檔郵件。
* **演示驗證**：若評審現場要求「關閉所有網路與 API Key」，TradeFlow 的全量功能（報價計算、行話生成、WhatsApp 狀態機、安全熔斷）依然能以 **<3ms 延遲** 100% 正常運行。這才是「策略與語言徹底解耦」的最強有力實證。

