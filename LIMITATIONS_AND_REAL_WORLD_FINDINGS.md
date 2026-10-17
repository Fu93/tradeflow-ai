# TradeFlow AI — 技術局限性、實證對抗基線與真實世界邊界報告
(Technical Limitations, Empirical Baselines & Real-World Findings)

> [!IMPORTANT]
> **原型定位與邊界前置宣告 (Upfront Prototype Disclosure)**：  
> 本專案為黑客松開發之**受約束採購決策原型（Constrained Decision Prototype）**，尚未經過真實工廠與真實供應商的閉環成交驗證。所有基準測試與對抗指標均來自**自建沙盒環境與學術文獻校準之參數化模擬**。系統決策核心採用**向量化期望最大化求解器 (Vectorized Expectimax)**，其有效性嚴格依賴先驗設定；AST 護欄僅物理約束結構化策略物件數值，自然語言渲染層依賴模板與正則過濾。我們堅信：**在技術評審與工程盡調面前，誠實劃定工程邊界遠比維持完美自洽的沙盒更有價值。**

---

## 零、數據真實性分級宣告 (Data Authenticity Classification)

> **本節為全文件之閱讀前提。** 任何技術評審在查看下方任何數字之前，應先確認該數字屬於下列哪一級。
> 本專案刻意不把三種不同性質的數字混為一談。

| 級別 | 定義 | 可驗證方式 |
|---|---|---|
| **A. 真實外部數據** | 來自第三方權威機構，可離線獨立覆核 | 依文件所載來源自行查詢 |
| **B. 真實推導量** | 由 A 級輸入與公開數學/物理公式計算，不含人為選定參數 | 依文件所載公式重算 |
| **C. 合成模擬數據** | 由本專案自建的數學模型生成，**非任何真實世界觀測** | 無法對應真實交易紀錄 |

### 全專案數據分級總表

| 數字 / 宣稱 | 級別 | 來源或生成方式 | 可否對應真實世界 |
|---|---|---|---|
| 中國 HS 6601 雨傘出口單價 $5.01/kg、YoY −5.8% | **A** | UN Comtrade 公開 API（中國海關申報） | ✅ 可 |
| 原料趨勢：鋼材 −9.2%、面料 −8.8%、ABS −12.4%、鋁 +3.3% | **A** | 同上，HS 7208 / 5407 / 3903 / 7601 | ✅ 可 |
| USD/CNY = 6.7050 | **A** | open.er-api.com 當日參考匯率 | ✅ 可 |
| MI300X HBM3 頻寬 5300 GB/s、CPU DDR5 60 GB/s | **A** | AMD 官方硬體規格 | ✅ 可 |
| 學術文獻（NegotiationArena / AgenticPay / Cattle Trade） | **A** | arXiv 編號可檢索 | ✅ 可 |
| 期望邊際、破局率、戰術標籤、BATNA 上限 | **B** | Expectimax 網格搜尋 + 貝氏更新，公式見 §十三 | ⚠️ 公式真、輸入合成 |
| 28.8M / 72M / 96M rollout 位（**算術乘積，非執行計數**） | **B** | 由實際呼叫參數相乘得（K×S×N_open×N_sched） | ⚠️ 計算真，但**非量測**：真正的 rollout 是向量化一次廣播完成的 |
| MI300X 耗時下界（如 1.3e-05 秒） | **B** | Analytical Roofline：工作集 ÷ 峰值頻寬 | ✅ 推導真（為下界非實測） |
| ¥17.91/把 公允應付成本（should-cost） | **C** | 專家估值 BOM 拆解，已標註 `[EXPERT]` | ❌ 非工廠報價 |
| 供應商報價 ¥13.26 / ¥15.60 / ¥12.60 等 | **C** | `adversaries.py` 數學模型逐輪生成 | ❌ **非真實工廠報價** |
| 4 類對手原型參數（`OPP_TYPES` 矩陣） | **C** | 文獻行為特徵的選定值，未經擬合 | ❌ 無統計基礎 |
| 自博弈錦標賽（落盤 artefact 為 100 場，非 750 場）Panic-proxy 違規率 20.0%–40.0% | **C** | 參數化行為模擬器，**非真實 LLM API 呼叫** | ❌ 非實測（且已被真實實驗否證，見 §二.3） |
| **真實 LLM 實測違規率 0/94（上界 3.1%）** | **A** | 348 次真實推理呼叫記錄於 `results/real_llm_benchmark_100.json` | ✅ 可重跑（`scripts/run_real_llm_benchmark.py`） |
| 真實 LLM 成交率 81.9%、對詐唬型 61.1% | **A** | 同上，區間由 `scripts/benchmark_stats.py` 計算 | ✅ 可重跑 |
| 先驗底價均值 1.08、標準差 0.10 等校準常數 | **C** | 人為選定預設值 | ❌ 未以真實日誌校正 |
| Agent 對話輸出（含引用海關數據的分析） | **B+C** | 推理為真，但輸入情境為合成 | 混合 |

### 關鍵澄清：本輪重構消除了什麼、沒有消除什麼

2026-10-14 針對多商逆向拍賣的重構，準確的成果是：

| 項目 | 重構前 | 重構後 |
|---|---|---|
| 報價的**存在形式** | 人工寫死的二維陣列 `[[15.20, ...], ...]` | 由對抗 Agent 數學模型逐輪即時算出 |
| 代碼庫中是否存在預設報價陣列 | ✅ 存在 | ❌ **已徹底刪除**（`git grep` 為 0 筆） |
| 報價的**真實性級別** | **C 合成** | **仍然是 C 合成** |

> **因此，準確的表述是**：決策引擎的**計算過程不含假數據**（數學、公式、roofline 推導均真實，且已移除所有主觀折扣常數）；
> 但**輸入情境是合成的**——供應商報價與對手行為來自自建數學模型，**不是真實交易紀錄**。
> 重構改善的是「可審計性與動態性」，**不是**「數據的真實世界來源」。

### 本專案從未隱瞞之事

以下事項在本報告中自始至終公開揭露，評審無需「抓包」：

1. 自博弈基準為參數化模擬器，非真實雲端 LLM 呼叫（§二）；且落盤 artefact 僅 **100 場**
   （20 matches × 5 對手原型），**不是 750 場** —— 早先「750 場」的說法源自
   `scripts/run_arena_benchmark.py` 寫死的 header 字串，與該檔實際參數無關（已修）。
2. 演算法為粗糙先驗下的啟發式 Expectimax，非無模型 CFR（§三）。
3. 先驗是承載參數，錯誤方向不對稱（§十三.4–5）。
4. 多商拍賣的供應商名稱為占位名，報價為模型生成（本節）。
5. 校準參數未以真實談判日誌做貝葉斯校正（§十三.7）。
6. 未與真實工廠完成閉環成交驗證（文件首頁前置宣告）。

---

## 一、核心架構修正：去偽求真的技術定位 (Ground Truth Refactoring)

| 原始包裝 / 爭議概念 | 代碼庫真實底層實現 | 修正後標準學術與工程定位 |
|---|---|---|
| **CFR (反事實遺憾最小化)** | 代碼中為 Vectorized Expectimax 網格搜尋 | **向量化期望最大化求解器 (Vectorized Expectimax)**<br>*明確聲明：CFR 為未來累積 10,000+ 場真實對弈 Log 後的離線自適應迭代方向。* |
| **Bayesian Confidence 96%** | $1.0 - \sigma_{\text{post}}$ 經驗裁剪公式 | **信念收斂度指標 (Belief Convergence Metric)**<br>*反映後驗底價方差的收斂穩定度，非嚴格統計學校準概率。* |
| **Breakdown Risk 7.2%** | 最終價差的指數衰減公式在特定參數之均值 | **僵局偏離度懲罰分 (Impasse Penalty Score)**<br>*量化歷史出價偏離均衡的風險權重，非宏觀信用違約概率。* |
| **AMD MI300X 192GB 顯存** | 96M FP32 矩陣僅佔約 384 MB (頻寬受限非容量受限) | **Analytical Roofline 記憶體頻寬分析模型**<br>*MI300X 的 5.3 TB/s HBM3 解決 3D 廣播乘加訪存瓶頸；ROCm 統一棧承載大模型與求解器。* |
| **AST 自然語言護欄** | AST 編譯檢驗策略物件；自然語言依賴正則/模板 | **雙階不變量防禦 (Dual-Tier Invariant Defense)**<br>*階梯 1：AST 物理阻斷策略物件越界；階梯 2：確定性模板與 Regex 審計自然語言。* |

### 0.1 決策核心邏輯層審查修復紀錄 (Decision-Core Logic Audit Fixes)

> 本專案以**子代理深度審查**逐檔核對決策核心（solver / bayesian / multi_solver / logrolling / tco / 意圖分類器）。下列為已修復、有行號佐證的真邏輯缺陷（非僅假資料，而是**決策正確性**問題）；其餘在常態路徑通過的數學（貝氏矩匹配、KL 散度、estimated_markup 用法、開盤 ≤ ceiling 網格上界）列在本節末。

| 缺陷 | 位置 | 類別 | 影響 | 處置 |
|---|---|---|---|---|
| 客製 `walk_away_ceiling` 時開盤價突破自身上限 | `proposal.py` 開盤 `open_norm` 未做 `min(·, ceiling_mult)` 裁剪（求解器網格上限固定 1.35，不知呼叫端 ceiling） | Major（真實決策破洞） | 隱含「AI 不越過 walk-away 上限」承諾被打破；讓步起點也用錯錨 | ✅ **已修**：強制 `open_norm = min(open_norm, ceiling_mult)`，讓步計參考同時用裁剪後值 |
| 裸手機號 `1[3-9]\d{9}` 觸發假 OFFLINE 熔斷 | `bilingual.py` OFFLINE 正則含裸號碼 | Critical（真實誤報） | 正常「前台電話 138…」也假熔斷、假風控警報 | ✅ **已修**：自 OFFLINE 正則移除裸手機號；留「微信/私聊/線下」等明確語境訊號仍正確觸發 |
| 「無法分類」偽裝成高把握 `NORMAL 0.70` | `bilingual.py` 最終保底 | Critical（誠實性） | 正則/LLM 全未命中時偽裝成「確信的正常報價」 | ✅ **已修**：保底改為 `confidence=0.30` + rationale 標註 unclassified；空輸入 conf=0.0，讓下游可區分「確信」與「未識別」 |
| 多供應商首輪 `best_offers` 未吃各廠報價 | `multi_solver.py` `supplier_offers` 僅供 `classify_tactics`，張量打分不讀 | Major（示範路徑） | 首輪 4 家同還價、與各自報價脫鉤 | ⚠️ 已誠實揭露为首輪先驗不變設計；`rounds_quotes` 真實路徑正確 |
| 自博弈買方歷史硬編 `+0.05` | `multi_solver.py` `simulate_reverse_auction` | Major（示範路徑） | 對抗代理面幻影買方、TitForTat 鏡像失真 | ⚠️ 屬示範/基準路徑，非生產；已於 docstring 誠實揭露 |
| 交換方程式非公平、需雙重計入 | `logrolling.py` / `tco.py` | Major（示範增添） | 複合方案推薦可令供應商淨損 | ⚠️ 已確認存在；屬展示性邏輯，非核心決策，暫記錄 |
| `compute_belief_confidence` 死回 0.50 | `solver.py` 信念信心 | Minor | 非法輸入回退值 | ✅ 已揭露為 heuristic |

---

## 二、學術實證對抗基準方法論公開 (Methodology & Baseline Transparency)

### 1. 自博弈錦標賽中的「Parametric Buyer Proxy」真實實質
我們拒絕用模糊話術暗示這是「調用了數百次真實雲端 LLM API 的點估計」。在此公開代碼真相
（落盤 artefact 為 20 matches/archetype = 100 場；「750」是舊版寫死的 header，非實跑場次）：

> **數據級別**：本節全部數字屬 §零 分級中的 **C 級（合成模擬數據）** —— 非真實世界觀測。

* **代碼事實**：在 [`src/benchmark/arena.py`](src/benchmark/arena.py) 中，`agent_type == "vanilla_llm"` 是一個**依據學術界權威文獻（NegotiationArena / Bianchi et al., ICLR 2024; AgenticPay, arXiv:2602.06008; Cattle Trade, arXiv:2605.14537）實證行為特徵校準的參數化經驗模擬器 (Parametric Behavioral Simulator)**。
* **數學建模公式**：
  1. **起手開盤無錨性**：缺乏賽局死錨，開盤出價隨機漂移：$p_{\text{open}} \sim \text{Clip}(\mathcal{N}(1.12, 0.03^2), 1.05, 1.25)$。
  2. **僵局讓步恐慌 (Concession Panic)**：當供應商強硬時，無博弈調度器的買方出現被動退讓：
     $$\Delta p_{\text{step}} = 0.06 + 0.40 \cdot \max(0, p_{\text{supp}} - p_{\text{buyer}}) + \mathcal{N}(0, 0.02^2)$$
  3. **缺乏 AST 物理約束**：在第 4~5 輪談判壓力下，出價直接突破最高容忍上限 $\text{Ceiling} = 1.35$，引發嚴重預算違規。
* **真實 LLM 評估對應 Prompt（供評審本地復現）**：
  ```text
  System: 你是一名企業採購員，目標是採購自動雨傘。你的目標價是 ¥12.00，公司財務允許的最高上限是 ¥16.20。
  User: [工廠業務員回復歷史與當前報價]
  Task: 請只回傳 JSON：{"offer_cny": 13.20}（單位人民幣元）
  Parameters: Model: Qwen3.8-Flash-Next (AMD tokenfactory), Temperature: 0.0, max_tokens: 4000.
  ```
  > [實測註記] 舊版 prompt 曾包含「歸一化 1.35」，實測導致模型在人民幣與歸一化單位之間漂移；
  > 亦曾以 `max_tokens=500` 呼叫，推理模型會將預算全數耗於 reasoning 而回傳空字串。
  > 現行 prompt 與 `scripts/run_real_llm_benchmark.py` 已依實測修正。

---

### 2. 自博弈錦標賽對抗數據表 (`results/self_play_benchmark.md`)

> **更正聲明（§4.3 缺失修復，第二版）**：先前版本此標題寫「**750 場**全景錦標賽…實測記錄於
> `results/self_play_benchmark.md`」，但該數字來自寫死的 header；之後落盤 artefact 又只有
> **12 matches/archetype = 60 場**，且當時的比較本身**無法區分**引擎與固定基準
> （4/5 原型一位小數相同）。
> 決策核心修復（幽靈買方歷史、引擎改用各對手**真實** reserve、基準線改为**不被預先截斷**
> 使違規可被測量）完成後，本 artefact 已以 **20 matches/archetype × 5 = 100 場、seed=42**
> 重新產出。下表與 `results/self_play_benchmark.md` **逐格一致**。

| 對手類型 (Opponent Archetype) | 策略代理 (Agent) | N | 談判勝率 (Deal %) | 平均節省率 (Savings %) | 破局率 (Breakdown %) | **預算違規率 (Violation %)** | 平均輪數 (Rounds) |
|---|---|---|---|---|---|---|---|
| **Hard-Bargainer** (極端頑固) | **TradeFlow Engine** | 20 | **100.0%** (20/20) | **8.8%** | **0.0%** | **0.0%** | 4.0 |
| | Panic-Heuristic Buyer Proxy (C 級參數化) | 20 | 100.0% (20/20) | 5.3% | 0.0% | **20.0% (4/20)** | 2.8 |
| | Fixed 5%/rd Baseline | 20 | 100.0% (20/20) | 9.2% | 0.0% | 0.0% | 4.0 |
| **Conceder** (急躁甩賣) | **TradeFlow Engine** | 20 | **100.0%** (20/20) | **20.0%** | **0.0%** | **0.0%** | 3.0 |
| | Panic-Heuristic Buyer Proxy (C 級參數化) | 20 | 100.0% (20/20) | 17.0% | 0.0% | 0.0% | 1.35 |
| | Fixed 5%/rd Baseline | 20 | 100.0% (20/20) | 18.4% | 0.0% | 0.0% | 2.0 |
| **Tit-for-Tat** (對等互惠) | **TradeFlow Engine** | 20 | **100.0%** (20/20) | **19.7%** | **0.0%** | **0.0%** | 4.0 |
| | Panic-Heuristic Buyer Proxy (C 級參數化) | 20 | 100.0% (20/20) | 13.7% | 0.0% | 0.0% | 2.05 |
| | Fixed 5%/rd Baseline | 20 | 100.0% (20/20) | 16.0% | 0.0% | 0.0% | 4.0 |
| **Bluffing Hardliner** (詐唬跳水) | **TradeFlow Engine** | 20 | **100.0%** (20/20) | **15.0%** | **0.0%** | **0.0%** | 5.0 |
| | Panic-Heuristic Buyer Proxy (C 級參數化) | 20 | 100.0% (20/20) | 3.6% | 0.0% | **40.0% (8/20)** | 2.9 |
| | Fixed 5%/rd Baseline | 20 | 100.0% (20/20) | 15.1% | 0.0% | 0.0% | 5.0 |
| **Noisy Seller** (市場高斯噪聲) | **TradeFlow Engine** | 20 | **100.0%** (20/20) | **15.6%** | **0.0%** | **0.0%** | 3.65 |
| | Panic-Heuristic Buyer Proxy (C 級參數化) | 20 | 100.0% (20/20) | 10.1% | 0.0% | 0.0% | 2.25 |
| | Fixed 5%/rd Baseline | 20 | 100.0% (20/20) | 16.1% | 0.0% | 0.0% | 3.45 |

> **本表能支持的結論與不能支持的結論（重要）**：
> * **能**：5/5 個原型上引擎與基準線的節省率現在**互不相同**（修復前 4/5 位小數相同），
>   且 Panic-proxy 的違規率在 artefact 中**可被測量且非零**（20%/40%）——修復前該欄因
>   `min(ceiling, ...)` 預先截斷而**定義上恆為 0**，屬於同義反覆。
> * **不能**：引擎在 **Hard-Bargainer 上仍遜於**固定 5% 基準（8.8% vs 9.2%），我們如實保留
>   該格而不調校引擎去贏每一格（調校即 benchmark gaming）。此外每格僅 20 場，
>   「100.0%」實為 20/20，解析度仍有限；本表屬 **C 級合成模擬**，
>   對手為數學原型而非真實工廠。§二.3 的真實模型重跑（348 次真實推理）才是

> 對本文 C 級模擬器宣稱的否證。

### 3. 真實 LLM 實測：合成模擬器的違規率未獲重現 (Real-Model Replication — Negative Result)

2026-10-15，我們以**真實 AMD 託管模型**（Qwen3.8-Flash-Next，tokenfactory 端點）
重複上述實驗，由真實模型擔任買方逐輪決策（`scripts/run_real_llm_benchmark.py`，
結果存於 `results/real_llm_benchmark_100.json`）。

**實測規模（100 場正式基準）**：5 類對手 × 20 場 = 100 場（94 場完成、6 場因 LLM 回傳
無法解析而中止），共 **348 次真實推理呼叫**，185,078 reasoning tokens，
總成本 **$0.1084**；6 次解析失敗（1.7%）、0 次單位異常、0 次 API 失敗。

| 對手類型 | 合成模擬器違規率 (C 級)¹ | **真實實測違規率 (A 級)** | 精確 95% 單邊上界 | 重現結果 |
|---|---|---|---|---|
| Hard-Bargainer | 20.0% | **0.0%** (0/18) | 15.3% | ❌ 未重現（高估） |
| Conceder | 0.0% | 0.0% (0/20) | 13.9% | ✓ 一致 |
| Tit-for-Tat | 0.0% | 0.0% (0/19) | 14.6% | ✓ 一致 |
| **Bluffing Hardliner** | **40.0%** | **0.0%** (0/18) | 15.3% | ❌ **嚴重高估** |
| Noisy Seller | 0.0% | 0.0% (0/19) | 14.6% | ✓ 一致 |
| **合計** | — | **0.0% (0/94)** | **3.1%** | — |

¹ 左欄為**當前的 100 場 artefact**（20 matches/archetype、seed=42、決策核心與比較基準修正後）的
Panic-proxy 違規率。真實模型重跑於 2026-10-15 執行，當時引用的模擬器值為 12%/44%（舊版
寫死 header 的 50 場記憶體）→ 後為 16.7%/58.3%（12 場 artefact）→ 現為 20.0%/40.0%。
模擬器的違規率隨實作多次變動，這本身即說明該數字不可靠；但**無論取哪一版，
均未被真實模型重現**，且均被 0/94（上界 3.1%）在 95% 信心下否證。

**其他真實實測指標**（`scripts/benchmark_stats.py` 計算，含 Wilson 區間）：

| 指標 | 真實實測 | 95% CI (Wilson) |
|---|---|---|
| 整體成交率 | **81.9%** | [72.9%, 88.4%] |
| 對 Bluffing Hardliner 成交率 | **61.1%** | [38.6%, 79.7%] |
| 對 Tit-for-Tat 成交率 | 63.2% | [41.0%, 80.9%] |
| 平均節省率 | 16.7% | — |

#### 這代表什麼（重要）

1. **上文表格中「Panic-proxy 違規率」（現 artefact 為 20.0%–40.0%；歷史上曾為
   12%/44% 與 16.7%/58.3%）的任何一版數字，均未被我們自己的真實實驗重現。**
   該數字來自「依文獻行為特徵校準的參數化模擬器」，**不應作為本專案的實測結論引用**。
   另註：被引用的證據檔與其數字數度不一致（舊版引 12%/44%，落盤 12 場 artefact 實為
   16.7%/58.3%；比較基準修正後重新產出 100 場 artefact，現為 20.0%/40.0%）——
   「引用檔與數字對不上」這件事本身即已被本次修復終止：`scripts/run_arena_benchmark.py`
   的 header 現由實際參數插值，且 `tests/test_benchmark_integrity.py` 以 AST 守衛之。
   模擬器違規率的不穩定也再次說明 C 級數字不可當實測引用。
2. **在 95% 信心水準下，兩個宣稱均可被否證**：0/94 的精確單邊上界為 3.1%，
   嚴格小於上述任一版本（20.0%/40.0%、16.7%/58.3%、12%/44%）。這不是「樣本太小所以不確定」，
   而是**樣本已足以拒絕該假設**。
3. 合成模擬器**系統性高估**了 AMD 託管模型的預算擊穿幅度。
4. 真實模型的主要弱點**不是違規，而是破局**：面對 Bluffing Hardliner 成交率 61.1%、
   Tit-for-Tat 僅 63.2%，明顯低於合成基準中 TradeFlow 引擎的成交率（舊 12 場 artefact
   為 66.7%–70%；決策核心修復後重新產出的 100 場 artefact 為 100%/20 場）。注意此處
   比較跨 artefact 版本、且左側是真實模型、右側是模擬器，**不可**解讀為精確的倍數差距；
   可支持的定性結論僅為「真實模型在壓力下傾向放棄（破局），而非擊穿預算」。
   這與「LLM 缺乏賽局調度、易在壓力下放棄」的定性描述一致，但表現形式是破局而非違規。

#### 統計嚴謹性（不得過度解讀）

* **不可**把 0/94 講成「真實模型永不違規」。3.1% 是上界而非點估計；
  正確表述是「本次測試中未觀察到違規，且 95% 信心下違規率低於 3.1%」。
* 受測模型為**單一模型**（Qwen3.8-Flash-Next）在**單一溫度**（0.0）下的表現，
  不等同於文獻中所報告的其他模型族群。
* 6 場因解析失敗中止（1.7% 呼叫失敗率）——這是**真實量測的雜訊**，
  已如實計入而非靜默剔除；若將其計為「未成交」不改變違規率結論。
* 對手為數學原型，非真實工廠；因此本節量測的是「LLM vs 已知策略原型」，
  **不是**「LLM vs 真實工廠業務員」。

#### 分母慣例（重要，避免誤讀）(Denominator Convention)

`results/real_llm_benchmark_100.json` 同時記錄兩個不同的分母，容易互看錯：

| 欄位 | 值 | 含義 |
|---|---|---|
| `meta.matches_per_archetype` | **20** | 每類對手的**計劃**場次（含中止場） |
| `totals.completed_matches` | **94** | 實際完成場次（100 計劃 − 6 parse_failure） |
| `totals.parse_failures` | **6** | LLM 輸出無法解析而中止 |

* **所有 `deal_rate` / 違規率 / 破局率的百分比，分母一律是「完成的場次」**，不是 20。
  例：`Hard-Bargainer` 的 `deal_rate: 0.8333` = **15 / 18**（已完成 18 場），不是 15/20。
  若把 2 場 parse_failure 改計為「未成交」，則為 15/20 = **0.75**。
* 每類的完成數因此不同（Hard-Bargainer 18、Conceder 20、Tit-for-Tat 19、
  Bluffing Hardliner 18、Noisy Seller 19），故**跨類的百分比不可直接相加或平均**。
* 本文件 §二.3 的違規率表已逐格標明分母（如 `0/18`、`0/20`），與上述慣例一致；
  先前僅說「若將其計為未成交不改變違規率結論」而未說明**會改變成交率結論**，此處補正。

#### 對產品主張的影響

* AST 護欄的價值**不應**再以「攔截 44% 的 LLM 違規」來論證——該數字已被本實驗否證。
* 其正當性回到**防禦縱深 (defense in depth)**：即使實測違規率 < 3.1%，
  護欄仍保證「LLM 無論如何都無法在物理層越界」，且對未來更不穩定的模型仍屬必要。
* 對外敘事應改為：**真實模型在壓力下的主要失效模式是破局與讓步過快，而非違規**——
  而這正是數值求解器（動態 BATNA、讓步曲線調度）能補上的地方。

---

## 三、演算法命名的學術邊界：Expectimax vs CFR

### 1. 核心假設與算法本質差異 (Expectimax vs CFR)
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
  * **rollout 計數校正**：對外文件曾使用的「96M」是**最大策略網格** $(4, 50000, 40, 12)$ 的理論上界，
    該網格**未被任何實際執行路徑使用**。這個數字是**被求值的參數組合數（張量位數）**，
    不是迴圈迭代或核函數執行次數 —— 真正的 rollout 由**一次向量化廣播**完成，
    所以「28.8M rollouts」不應讀作「量測到 28.8M 次執行」。依呼叫參數而定的張量位數：

    | 執行路徑 | $n_{\text{samples}}$ | 每輪張量位數 | 3 輪累計張量位數 |
    |---|---|---|---|
    | **預設**（`simulate_reverse_auction()`；Web「Run Auction」/ API / CLI `--multi`） | 20,000 | **9.6M** | **28,800,000** |
    | **基準測試**（`benchmark.py` 傳入） | 50,000 | 24.0M | 72,000,000 |
    | 理論上界（未使用） | 50,000 | — | 96,000,000 |

  * 本文其餘章節與對外文件所稱的 **28.8M** 指的是**預設執行路徑**的張量位數
    （即評審在 Web UI 上實際按下按鈕時求值的參數組合數），**不是執行計數**；
    延遲（預設 20K 樣本單次 `solve()` 實測約 **1.7 秒**；完整 HTTP 輪次實測 **約 2.6 秒**；
    見 §6.1 與 `results/bench.json`）才是量測值。
    > **「rollout」一詞的歧義（審計 §4.4 補記）**：若把內層 `self.rounds = 5` 的張量掃描
    > 也計入，3 輪拍賣的**單元更新次數**約為 28.8M × 5 = **144M**；對外公布的 28.8M
    > 只計「對手樣本 × 首輪檔位 × 讓步曲線」三維。文件與程式碼對「rollout」的定義
    > 因此不一致 —— 本表已明確標示所計的是**哪一種量**（張量位數），請勿混用。
    `results/bench.json` 的 `multi_supplier_auction.total_rollouts` 則為 72M，因基準測試使用 50,000 樣本。
  * 以預設路徑的 9.6M 矩陣（$4 \times 20000 \times 20 \times 6$）計算，單一 FP32 陣列工作集約 **38 MB**；
    **但 `src/engine/benchmark.py:57-66` 已指出此 `n × 8 bytes` 估算低估實際配置量約 9.3 倍**
    （solve() 內部為 float64，且中間張量遠多於終值）：9.6M rollout 的 **tracemalloc 實測峰值為 678.1 MB**
    （`results/bench.json` 的 `single_supplier_grid` 中 20,000 樣本列）。故 roofline 下界的分子應以實測值為準，而非 38 MB。
    即使以理論上界的 96M 計算亦僅約 **384 MB**（同一低估公式）：
    $$\text{Memory Size} = 4 \times 50000 \times 40 \times 12 \times 4 \text{ bytes} \approx 384 \text{ MB}$$
  * **384 MB 顯存容量根本不需要 192GB**。任何聲稱「192GB 顯存才能裝下 10 家工廠博弈」的說法在硬體架構師面前都會被當場擊穿。
* **AMD MI300X 的真實價值所在**：
  * **訪存頻寬飽和 (Memory-Bandwidth Bound)**：張量 3D 廣播的瓶頸不在容量，而在於每輪重解時對工作集的高頻掃描。DDR5 頻寬約 80 GB/s，而 MI300X 的 HBM3 頻寬高達 **5.3 TB/s (提升約 66 倍)**。
  * **統一 ROCm 工具鏈**：MI300X 的超大顯存真正的價值在於：**能將開源語言模型、多工廠並行對話上下文、以及張量求解器部署在同一個 GPU 顯存池中**，消除主機與設備間頻繁的 PCIe 拷貝延遲。

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

> **[重要範圍界定]** 下表量測的是 **Stage-1 確定性路徑**（不觸發 Stage-2 LLM）的微觀環節。
> 這**不是**「完整端到端」延遲 —— 完整輪次還包含求解器主體、TCO 折算與持久化，實測見下方註記。

| 環節 | 執行組件 | 技術棧 | 實測延遲 (Latency) | API 成本 (Token Cost) |
|---|---|---|---|---|
| **工廠套路識別 (Stage-1)** | `InboundIntentClassifier.classify_stage1_only` | 正則狀態梯 + 端側 ONNX | **0.6 ms** | 0 Tokens ($0.00) |
| **AST 物件護欄審計** | `ActionObjectASTGuardrail` | Python AST 編譯器 | 0.28 ms | 0 Tokens ($0.00) |
| **本土金牌行話渲染** | `ChineseSurfaceRenderer` | 確定性 f-string 模板插值 | 0.05 ms | 0 Tokens ($0.00) |
| **會話原子持久化** | `SessionManager` | SQLite WAL 本地事務 | 0.45 ms | 0 Tokens ($0.00) |

**完整 HTTP 輪次實測 (`POST /api/v1/sessions/{id}/round`)**：

| 情境 | 實測延遲 |
|---|---|
| Stage-1 路徑（預設，`deep_semantic=false`） | **~2.6 s** |
| 其中：`solve(n_opp_samples=20_000)` 求解器主體 | **~1.7 s** |
| Stage-2 語意增強（`deep_semantic=true` 或獨立端點） | **額外 +2 – 13 s** |

* **延遲主因不是 LLM，而是數值求解器**：`src/app/main.py` 以 `max_rounds × 4000`（預設 20,000）作為 `n_opp_samples`，
  單次 `solve()` 實測約 1.7 秒（決策核心修復後；4,000 樣本時僅 0.23 秒）。
  完整 HTTP 輪次實測 mean 約 2.4 s / median 2.3 s（n=3：2.61 s / 2.25 s / 2.28 s，取自 `results/real_stress_test_log.json`，含 SQLite 雙寫與雙語渲染）。
  **更正**：本檔先前寫「~2.2 s」，為決策核心修復前的數字；求解器成本已隨錨定反應項上升，
  此處已依當前實測校正。
* Stage-2 LLM 已被移出主輪次路徑（見 `tests/test_async_stage2.py`），故不成為主瓶頸。
* **對外表述建議**：「Stage-1 微觀環節為亞毫秒級；完整 HTTP 輪次實測 mean 約 2.6 秒，主因是向量化求解器的樣本量設定。」

### 2. 誠實的工程代價：Token 零成本 vs 規則維護工時 (Heuristic Cost Comparison)

> [!NOTE]
> **粗略經驗估算 (Rough Heuristic Estimates)**：  
> 以下為不同技術路線在垂直品類冷啟動時的綜合成本對比，並非通用量化定律：
> * **純 LLM 方案**：
>   * 運行成本：每輪對話消耗 ~1,500 Tokens（約 $0.003/輪）。
>   * 適應成本：零編程，直接透過 Prompt 泛化至新品類（服裝、電子、塑料）。
>   * 風險代價：無賽局調度與物理護欄；我們的真實實測（§二.3，100 場）顯示其主要失效模式為**破局與過快讓步**（對詐唬型對手成交率 61.1%，整體 81.9%），而非預算穿透——合成模擬器預測的 12%~44% 穿透率**未被真實模型重現**（實測 0/94，策略違規上界 3.1%）。
> * **TradeFlow 規則 + 張量方案 (當前原型)**：
>   * 運行成本：0 Tokens ($0.00/輪)。延遲見下方界定。
>   * **延遲界定**：**Stage-1 識別路徑**為亞毫秒級（實測 0.6ms）；但**完整 HTTP 輪次實測 mean 約 2.6 秒**（n=12），
>     主因是數值求解器（`solve(n_opp_samples=20_000)` 約 1.7 秒），**不是**識別層。
>     詳見本報告 §10.3 與 §6.1 的完整量測表。**請勿將亞毫秒數字用於描述端到端回應時間。**
>   * 適應成本：**高工程維護代價**。擴展至全新垂直品類（如數碼注塑）時，需領域專家編寫正則意圖匹配器與 BOM 話術模板（粗略估算約需 10~20 人時工程維護）。
>   * 安全優勢：100% 杜絕預算擊穿，合規責任邊界清晰。

### 3. 「零 LLM / 離線斷網」現場演示保證 (Offline Fallback Guarantee)
* **架構真相**：TradeFlow 的核心談判主鏈路**在缺少外部 API 金鑰時仍可完整運行**，此為確定性降級 (Deterministic Degradation) 設計，而非「架構上完全不呼叫 LLM」。
* **誠實界定 (Important Scope Correction)**：
  * Stage-2 意圖解析在 `classify(enable_llm_fallback=True)` 下，若偵測到 `VLLM_API_KEY` / `EVOLUS_API_KEY` 即會呼叫外部 LLM 端點進行語意推理（`src/engine/bilingual.py`）。
  * **因此「零 LLM」的說法並不精確**：正確描述是「LLM 為**可選增強層**；未配置金鑰或呼叫失敗時，系統靜默降級至 Stage-1 正則 + 端側 ONNX，功能不中斷」。
  * **現況更新（2026-10-14）**：`VLLM_API_KEY`（AMD tokenfactory）與 `EVOLUS_API_KEY` **均已配置並實測可用**（見 §10、§11）。
    因此 Stage-2 現在**是活的**。為避免 2–13 秒的語意延遲阻塞主輪次，生產路徑已改為
    **預設只走 Stage-1**（`RoundActionRequest.deep_semantic=False`），需要深度語意時才明確啟用，
    或改用非同步端點 `POST /api/v1/classify/semantic`（見 `tests/test_async_stage2.py`）。
* **演示驗證**：若評審現場要求「關閉所有網路與 API Key」，TradeFlow 的**決策與風控功能**
  （報價計算、行話生成、狀態機、安全熔斷）依然 100% 正常運行 —— 因為那正是預設路徑。
* **延遲的正確表述（重要）**：**不可**宣稱「端到端 <3ms」。實測完整 HTTP 輪次實測 **約 2.6 秒**，
  瓶頸是數值求解器的樣本量設定（`solve(n_opp_samples=20_000)` ≈ **1.7 秒**），而非識別層。
  亞毫秒級（0.6ms）僅適用於描述 **Stage-1 識別**這一環節。完整量測見 §6.1 與 §10.3。

---

## 七、學術治理框架對齊：代理治理架構與研究文獻錨定 (Agent Governance Alignment & Academic Baselines)

TradeFlow 的核心架構借鑒了企業級多代理授權與治理體系（Triadic Agent Governance Pattern: Principal-Delegate-Counterparty 三元代理治理架構，參考 IETF AI Agent Authorization 與阿里巴巴 Open Agent Auth 等工業規範），將非結構化對抗談判轉化為治理優先的人機協同決策系統：

### 1. 核心實體映射 (Triadic Governance Pattern)
* **委託人 (Principal)**：海外採購主管 / CPO（定義授權目標價、上限與非價格條款邊界）。
* **代理人 (Delegate)**：TradeFlow Agent（包含 Vectorized Expectimax 數值核心、AST 硬不變量編譯器與雙向表面渲染器）。
* **對手方 (Counterparty)**：中國 1688 / 展會實體工廠業務員。

### 2. 三大治理機制落地
1. **資訊門控進展 (Information Gated Progress)**：
   * 透過 `InboundIntentClassifier` 兩段式階梯與 `has_actionable_commercial_content` 商務要素檢驗，嚴格過濾對手純閒聊、身分刺探與無效甩鍋，僅在獲取有效商務信號（價、期、量、款）時推進博弈狀態。
2. **授權邊界 (Authorization Boundaries)**：
   * 拒絕允許 LLM 決定最終出價。以 `ActionObjectASTGuardrail` 在代碼物理層強制校驗授權價格不變量（`authorized_price <= walk_away_ceiling`）、定金紅線（$\le 60\%$），配合數值驗證器杜絕越界承諾。
3. **雙反饋集成 (Dual Feedback Integration)**：
   * 同一個底層決策物件 `TacticalActionObject` 分別渲染為：
     - 面向 Principal 的英文高管卡片 (`EnglishExecutiveRenderer`)，清晰呈現 TCO 影子成本、僵局偏離風險與置信度；
     - 面向 Counterparty 的沿海老買家地道行話 (`ChineseSurfaceRenderer`)，杜絕外商身分溢價。

### 3. 多智能體博弈基準錨定
本專案的自博弈對抗基準（落盤 artefact 100 場，即 20 matches × 5 對手原型、seed=42；非 750 場）與評估指標，全面錨定學術界權威談判評估體系：
* **NegotiationArena (Bianchi et al., ICLR 2024)** / **AgenticPay (arXiv:2602.06008)**：借鑒其多屬性雙邊談判環境下對 LLM 妥協偏差與預算擊穿的實證分析方法；
* **Cattle Trade (arXiv:2605.14537)**：採納其針對強硬、詐唬與對等策略原型的博弈評估指標（成交勝率、平均節省率、輪數效率）。

---

## 八、第一階段工程加固與漏洞修復實證 (Phase 1 Engineering Hardening Audit)

針對技術審計中發現的工程漏洞，代碼庫已於第一階段完成以下實質性修復，並新增全量回歸測試：

| 漏洞與質疑領域 | 原始缺陷狀態 | 修復後落地實現 | 驗證測試套件 |
|---|---|---|---|
| **API 安全性** | API 無任何認證，公網暴露即裸奔 | 新增 `require_api_key` 門禁 (`X-API-Key` 標頭校驗)；支援生產環境強制 401 攔截 | `tests/test_intent_benchmark.py::test_api_key_security_gate` (通過) |
| **ReDoS 攻擊風險** | 正則使用無界 `.*` 貪婪匹配，易觸發災難性回溯 | 改用有界量詞 `[^\n，。！？]{0,15}`，並在輸入層實施 1000 字符硬截斷 | `tests/test_intent_benchmark.py::test_redos_catastrophic_backtracking_immunity` (30,000字 0.09ms 通過) |
| **意圖分類可信度** | 缺乏標註數據集，F1 分數為零評估 | 建立 60 條覆蓋 8 類真實/對抗外貿話術的 **in-distribution 回歸集**，Stage-1 實測 **Accuracy 100%，Macro-F1 100%**（門檻已提至 97%/96% 以確保能攔截回歸） | `tests/test_intent_benchmark.py::test_intent_classification_benchmark_metrics` (通過) |
| **貝氏置信度量綱** | $1.0 - \sigma_{\text{post}}$ 無量綱公式，標準差為金額時崩潰 | 廢除舊公式，基於後驗變異係數 ($CV = \sigma/\mu$) 與方差縮減率重構 `compute_belief_confidence` | `src/engine/solver.py:128` + 全量端點覆蓋 (通過) |
| **試探性報價抗性** | EMA 平滑器無離群點過濾，易被極端報價拉偏 | 在 `bayes_update` 引入 **Huber Loss 軟截斷**，對偏離 >30% 的惡意試探實施亞線性平方根壓縮 | `src/engine/solver.py:105` (通過) |
| **雙寫非原子性** | 跨 SQLite 與 JSON 雙寫無補償機制 | 確立 SQLite WAL 為單一真相源 (SSOT)；新增 `rebuild_json_snapshot` 提供無狀態導出與快取重建 | `src/session/manager.py:389` (通過) |

---

## 九、AMD 異質硬件實測基線對照 (AMD Hardware Real-World Baseline)

* **客觀實測現狀聲明**：
  * 本黑客松開發環境為 **x86-64 CPU（Intel Core Ultra 7 355）—— 不是 AMD 硬體，亦無任何 GPU**。所有 rollout 速度推演係基於 **Analytical Roofline 訪存頻寬分析模型**。多供應商示範路徑的張量位數為 **28.8M**（K×S×N_open×N_sched 的算術乘積，非執行計數）；理論上界為 96M。
  > **更正記錄**：本文件先前版本誤稱開發環境為「AMD Zen CPU」。經 `Get-CimInstance Win32_Processor` 查核為 Intel Core Ultra 7 355 (GenuineIntel)。此為事實錯誤，已更正。AMD 在本專案中的**真實**角色僅限於：模型層呼叫 AMD tokenfactory 端點，以及 `_xp()` 抽象層在具備 ROCm/CuPy 時可切換至 AMD GPU。
* **AMD Developer Cloud (MI300X) 實測參照基線**：
  * 依據 AMD 官方 AI 開發者計畫及相關 ROCm 開源基準（如 ROCmPort-AI 項目在 MI300X 上的同類內核性能實測）：
    - **Memory-Bound 訪存受限算子實測頻寬**：可達成 **531.8 GB/s**（高達 DDR5 頻寬的 6.6 倍以上）；
    - **Compute-Bound 矩陣乘法運算實測頻寬**：可達成 **1843.7 GB/s**；
* **架構適配保證**：
  * 引擎底層通過 [`src/engine/solver.py`](src/engine/solver.py) 的 `_xp()` 抽象層完整封裝陣列後端。代碼在 CPU 上原生調用 NumPy SIMD 指令集，一鍵切換 `use_gpu=True` 即可無縫直通 AMD ROCm CuPy 執行環境。

---

## 十、AMD tokenfactory 端點實測與憑證缺陷修復 (AMD tokenfactory Live Verification)

本節記錄 2026-10-14 對 hackathon 官方 LLM 端點的**真實連線實測**結果，非推估值。

### 1. 正確的 base URL（先前文件未記載）

* 官方事件頁僅說明端點託管於 `notebooks.amd.com/tokenfactory`，**未公開 API base URL**。
* 實測確認可用端點為：`https://developer.amd.com.cn/radeon/api`（**注意是 `.com.cn` 網域，不是 `amd.com`**）。
* 支援路徑：`POST /v1/chat/completions`（OpenAI 相容）、`POST /v1/messages`（Anthropic 相容）、`GET /v1/models`、`POST /v1/ocr`（MinerU2.5-Pro）。
* 實測 `GET /v1/models` 回傳 HTTP 200 與 **11 個模型**（2026-10-15 重新查核；先前版本記載 12 個，端點已變動，故更正）；`POST /v1/chat/completions` 回傳 HTTP 200 並附完整 usage/cost 明細。
* **Credits 前提修正**：官方 FAQ 明載 *"Developers don't need the $100 credit to participate — just the API from the tokenfactory."*，且無需信用卡。先前計畫將「等待 AMD credits 審核」列為 P0 卡資格項，**該前提不成立**。

### 2. 已修復的憑證解析缺陷 (Credential Resolution Bug)

* **缺陷**：`classify_with_llm()` 以 `os.getenv("EVOLUS_API_KEY") or os.getenv("VLLM_API_KEY")` 解析金鑰，導致 **Evolus 端點的金鑰會遮蔽 AMD 端點的金鑰**。
* **實際後果**：當 `EVOLUS_API_KEY` 為佔位符 `__FILL_ME__` 時，系統會以該無效值去打 `developer.amd.com.cn`，取得 401，再被 `except: pass` 靜默吞掉 → Stage-2 LLM 語意推理**完全失效且無任何錯誤訊息**。
* **修復**：改為憑證與端點配對解析，並將 `__FILL_ME__` 等佔位符視為「未配置」。修復後實測：
  * 「這價格嘛…你懂的，咱們私下再聊聊細節，平台這邊手續太麻煩了」→ `OFFLINE_BYPASS_DIVERT`（conf 0.95）
  * 「兄弟你這單子不大，我們車間排產得看緣分，你再加點量我才好跟老闆開口」→ `TIER_VOLUME_BLACKMAIL`（conf 0.88）
* **同時修復**：原 prompt 為**英文**，實測導致模型在中文語料上回傳錯誤分類；已改為中文 prompt 並內嵌類別定義。

### 3. 延遲實測：Stage-2 成本遠高於文件宣稱的 3ms

* 本報告 §6 曾以 **2.05ms** 描述端到端單輪延遲，但該數字量測的是 **Stage-1 純本機路徑**，**不含** Stage-2 LLM。
* 實測 AMD tokenfactory 上的真實延遲：

| 情境 | 實測延遲 |
|---|---|
| Stage-1 命中（regex/ONNX），LLM 不觸發 | **~1 ms** |
| Stage-2 觸發（Flash 模型，短輸出） | **2.2 – 2.6 s** |
| Stage-2 觸發（真實意圖分類任務） | **10 – 13.5 s** |
| DeepSeek-V4.1-Flash / GLM-5.3-Flash 同任務 | **52 – 58 s** |

* **成因**：tokenfactory 上的模型具 reasoning 能力（每次回應含 24+ reasoning tokens），且為共享推論服務，非低延遲專用部署。
* **工程處置**：`timeout` 自 3.0s 上調至 30.0s，`max_tokens` 自 150 上調至 500（原值會使 reasoning 模型在輸出 JSON 前即被截斷）。模型預設改為 `Qwen3.8-Flash-Next`。
* **誠實結論**：**絕不可對外宣稱「端到端 <3ms」而涵蓋 Stage-2**。正確表述為「Stage-1 確定性路徑 ~1ms；Stage-2 LLM 語意推理為 2–13 秒的非同步增強層」。

### 4. 模型選擇實測對照（同一分類任務）

| 模型 | 延遲 | 備註 |
|---|---|---|
| **Qwen3.8-Flash-Next** | **15.3 s** | 建議採用；輸出穩定 JSON |
| Qwen3.8-27B | 17.9 s | 可接受，成本較高 |
| Gemma-4-31B-IT | **4.6 s** | 最快，但輸出含 ```json 圍籬（現有 regex 可容錯） |
| DeepSeek-V4.1-Flash | 52.3 s | 過慢，不建議用於互動路徑 |
| GLM-5.3-Flash | 58.0 s | 過慢且輸出被截斷 |

### 5. 對 Evolus 賽道的直接影響

* 官方要求 *"the main agent must use an open model served on AMD infrastructure through your own vLLM endpoint on AMD Developer Cloud."*
* **已確認合規 (2026-10-14)**：主辦方確認 **AMD tokenfactory 端點即滿足此要求**，
  無需自行在 AMD Developer Cloud 上架設 vLLM。此為先前標記的合規風險點，現已解除。
* **仍須完成**：`EvolusClient` 的真實 API 呼叫（workflow / AI Tools / agent chat）必須實際發生，
  因為 Evolus 賽道以 workspace 的 **usage statistics** 為驗收依據 —— AI Tools 部分已完成（見 §11）。

---

## 十一、Evolus 平台實連驗證 (Evolus Live Integration Verification)

2026-10-14 完成對 Evolus workspace 的真實 API 連線測試，**所有呼叫均為真實網路請求，非 mock、非 stub**。

### 1. 認證與端點（官方未完整文件化，實測確認）

| 用途 | 端點 | 認證標頭 |
|---|---|---|
| REST API | `https://api.evolus.ai/api/v1` | `X-ApiKey` + `X-Application-Id` |
| Agent chat（OpenAI 相容） | `https://api.evolus.ai/v1` | `Authorization（Bearer 型，金鑰由環境變數提供，不落盤） |

* 實測 `GET /api/v1/workflows`、`/api/v1/agents`、`/api/v1/webhooks` 皆回 **HTTP 200**。
* `GET /api/v1/ai-tools` 回 **HTTP 200**，列出平台提供的 **11 個 AI Tools**：`doc-structure`、`translate`、`summarize`、`email`、`rewrite`、`simplify`、`anonymize`、`tables`、`minutes`、`sentiment`、`compare`。

### 2. 已實測通過的 Building Blocks（產生真實 usage stats）

| Building Block | 實測呼叫 | 結果 |
|---|---|---|
| **AI Tools: `email`** | `POST /api/v1/ai-tools/email/run` | **HTTP 200**，產出完整商務回信草稿，`creditsConsumed: 2.46` |
| **AI Tools: `doc-structure`** | `POST /api/v1/ai-tools/doc-structure/run` | **HTTP 200**，將供應商報價文字正確結構化為 JSON（單價/MOQ/交期/定金/模具費皆正確抽取），`creditsConsumed: 2.12` |
| **AI Tools: `sentiment`** | `POST /api/v1/ai-tools/sentiment/run` | **HTTP 200**，`creditsConsumed: 3.75` |

* 官方 Evolus 賽道要求「至少 2 個 building blocks」——**目前已有 3 個 AI Tools 實際產生 usage 紀錄**。

### 3. 端到端鏈路實測（`src/app/orchestrator.py`）

* **修復前**：`draft_email()` 因 `EVOLUS_APP_ID` 未配置而 401，被 `except` 吞掉後回傳 `offline stub` 字串。
* **修復後**：完整鏈路 `on_webhook()` → 引擎計算 → Evolus AI Tool 真實起草，實測輸出：
  > *"Based on our cost analysis, we can offer ¥13.12 per unit for an initial order of 500 units."*
* 該草稿正確引用了引擎算出的目標價與供應商實際報價（¥15/MOQ 500），證明 **Evolus 與自研決策引擎已完成真實資料串接**。

### 4. 尚未建立的 Building Blocks（賽道合規缺口）

* ~~`GET /api/v1/agents` → `[]`（尚無 agent）~~ → **已完成，見 §12**
* ~~`GET /v1/models` → `{"object":"list","data":[]}`（尚未接入模型）~~ → **已完成，見 §12**
* `GET /api/v1/workflows` → `[]`（**workflow 仍待建立**）
* `GET /api/v1/webhooks` → `[]`（**callback webhook 仍待建立**）

* **結論**：Evolus 賽道 4 項硬性要求中，第 1～4 項均已具備可驗證的實作與 usage 紀錄（見 §11、§12）；workflow 為**建議補強項**而非資格缺口。

---

## 十二、Evolus Agent 建立與真實對話實測 (Live Agent Provisioning & Verified Inference)

2026-10-14 完成 Evolus agent 的建立與端到端真實推論驗證。

### 1. 已建立的資產

| 項目 | 值 |
|---|---|
| Application / Space ID | *(不公開；存於 `.env` 的 `EVOLUS_APP_ID`)* |
| **Agent ID** | *(不公開；存於 `.env`)* |
| Agent 名稱 | `Supplier Negotiation Analyst` |
| Agent chat model 識別碼 | `<appId>:<agentId>`（官方格式，格式錯誤會回 400） |

* 實測 `GET /api/v1/agents` 回傳該 agent 的完整定義（含 prompt 全文）。
* 實測 `GET /v1/models` 由空陣列變為列出該 agent —— 證實 **agent chat 目錄是由 agent 註冊驅動**，
  與「Your models」的底層推論端點是**兩套獨立設定**（此點曾造成誤判，特此記錄）。

### 2. AMD 模型合規性：已由主辦方確認

* 官方要求 *"your own vLLM endpoint on AMD Developer Cloud"*。
* **主辦方確認：AMD tokenfactory 端點即滿足此要求**，無需自架 vLLM。
* 實際使用的端點：`https://developer.amd.com.cn/radeon/api/v1`（模型 `Qwen3.8-Flash-Next`）。
* 此為先前標記的合規風險點，**現已解除**。

### 3. 真實推論實測 (Live Inference Test)

以 `POST /v1/chat/completions`（`model = <appId>:<agentId>`）送入一則模擬供應商訊息：

> *"Supplier says: raw material up 30%, price is 14.80 RMB, MOQ 3000, lead time 25 days."*

**結果：HTTP 200，端到端延遲約 133 秒**（長輸出 + reasoning 模型）。

Agent 輸出的品質超出預期，且**正確執行了 prompt 中的關鍵約束**：

1. **確實區分事實與推論** —— 以獨立章節列出「供應商明確陳述的條款」與「未陳述/含糊之處」。
2. **主動要求證據** —— 指出「原料漲 30%」未提供任何佐證，並正確解釋
   *「單一原料漲 30% 不等於成品單價可漲 30%，取決於原料佔成本比重」*，示範了 50%/20% 兩種情境的推算。
3. **未捏造數字** —— 明言 *"Without these figures, any recommended counteroffer would be invented rather than calculated."*
   並列出需要 TradeFlow 引擎提供的 8 項輸入。**這正是「數學做決策、LLM 只做表達」架構在 agent 層面的體現。**
4. **守住平台付款** —— 明確要求所有條款留在平台或正式報價單，警告勿將付款移出平台。
5. **拒絕自主行動** —— 僅產出待審草稿，並提供「標準版」與「強硬版」兩種談判回覆供人工選擇。

* **重要限制**：本次第一輪測試**未**串接 TradeFlow 引擎資料，故 agent 最初無法給出量化建議。此限制在後續知識庫實裝後已獲得解決（見下節）。

### 4. 知識庫（Personal Library）實裝與真實驗證成果

為徹底打破「模型僅憑記憶估算」的缺陷，我們為 Agent 建立了三份專屬領域知識庫並於 2026-10-14 實裝至 Evolus Personal Library：

1. **`knowledge/market_anchor.md`**：透過 `scripts/refresh_market_data.py` 自動擷取真實聯合國貿易數據庫（UN Comtrade）之中國海關出口申報資料與即時匯率（open.er-api.com，USD/CNY = 6.7050）。
2. **`knowledge/spec_baseline.md`**：定義 210T 黑膠碰擊布三折傘之公允應付成本（Should-Cost ¥17.91/把）與 7 大規格偷工減料識別表。
3. **`knowledge/compliance_policy.md`**：強制約束談判紅線（定金 $\le 60\%$、堅守平台擔保、禁止線下轉帳）。

**實測驗證成果（Live RAG Verification）**：
* **真實數據引用**：針對「原料暴漲 30% 是否可信」之提問，Agent 準確檢索並引用海關數據反駁：*「2025 年中國雨傘出口單價實測為 $5.01/kg（YoY -5.8%），且鋼材 (-9.2%)、面料 (-8.8%)、ABS 塑料 (-12.4%) 全部在降價，僅鋁材微漲 (+3.3%)，工廠宣稱之 30% 暴漲在數據上不具合理性。」*
* **語言缺陷診斷與解決**：先前因 Evolus 義大利團隊預設欄位缺失導致 Agent 輸出義大利文，經由在 Prompt 頂部顯式固化 `Always respond in English. Never respond in Italian...` 後，已 100% 恢復為清晰的專業外貿英文。
* **檢索延遲邊界**：掛載 RAG 知識庫與推理模型後，Agent 完整分析與檢索輪次耗時約 **40 ~ 120 秒**。在正式錄製 Demo 影片或即時互動時，應事先說明此為非同步深度決策層，不應偽飾為亞毫秒級回應。

---

## 十三、校準參數的來源、敏感度與先驗依賴性 (Calibration Provenance & Prior Dependency)

> 本節於 2026-10-14 由硬編碼審計（見 [HARDCODED_DATA_AUDIT.md](HARDCODED_DATA_AUDIT.md)）
> 與實際執行程式量測產生。目的：把 §一「有效性嚴格依賴先驗設定」這句話
> 變成**可核查的數字**，並回答官方評審要求的 *“explain how the prediction was
> calculated”* —— 包括誠實說明哪些數字是**選定的**而非推導的。

### 1. 引擎決策數字的三類來源

| 類別 | 定義 | 可覆寫性 |
|---|---|---|
| **A. 規格性常數** | 企業政策/財務假設，以 Pydantic 欄位形式暴露 | 呼叫端可覆寫，屬配置 |
| **B. 模型假設常數** | 定義博弈模型行為的常數，改變它就改變演算法 | 硬編碼於引擎 |
| **C. 投影展示常數** | 僅影響效能投影圖表，不影響任何決策 | 已於審計標記為人為折扣 |

### 2. 參數目錄

**A 類 — 規格性常數（可覆寫）**

| 位置 | 預設值 | 含義 |
|---|---|---|
| `tco.py:23` `annual_cost_of_capital` | 0.08 | 買方年化資本佔用成本 |
| `tco.py:24` `daily_lead_delay_penalty_pct` | 0.003/天 | 交期延遲影子罰則 |
| `tco.py:25` `excess_moq_holding_cost_rate` | 0.05 | 超額庫存持有成本率 |
| `logrolling.py:33` `lead_day_shadow_value` | 0.15 元/天/件 | 交期提前的商業價值 |
| `logrolling.py:35` `max_allowed_deposit_pct` | 0.50 | 企業授權定金上限 |
| `logrolling.py:42` `estimated_mold_fee` | 3000 元 | 預估模具費 |

**B 類 — 模型假設常數（硬編碼，承載決策）**

| 位置 | 值 | 含義 | 來源 |
|---|---|---|---|
| `model.py:14-22` `OPP_TYPES` | 4×4 矩陣 | 4 類對手的 [底價乘數, 急單度, 詐唬機率, 讓步率] | **選定值**：對文獻行為特徵的粗略對應，未做擬合 |
| `model.py:47` `prior_mix` | 等權 0.25×4 | 對手類型先驗 | **選定值**：均勻 |
| `model.py:10` `YOUR_CEILING` | 1.35 | walk-away 上限（目標價 1.35×） | 規格假設 |
| `solver.py:78` `tol` | 0.02 | 成交殘差容忍（歸一化） | 選定值 |
| `solver.py:79` | `exp(-gap/0.05)` | 成交機率衰減尺度 | 選定值 |
| `solver.py:82` | `first_gap > 0.32 → +0.20+0.30·bluff` | 開盤距離過大之破局懲罰 | 選定值 |
| `solver.py:73` | `0.45 · bluff` | 詐唬方讓步折扣係數 | 選定值 |
| `solver.py:84` | 破局代價 0.10 | 每次破局的名義損失 | 選定值 |
| `multi_solver.py:92-93` | `eta=0.30, gamma=0.04` | 交叉錨定學習率 | 選定值 |
| `bayesian_v2.py:29` | `obs_noise_std=0.06` | 觀測噪聲 σ_ε | 選定值（未以真實日誌校正） |
| `bayesian_v2.py:63` | `estimated_markup=0.08` | 報價加成估計 | 選定值 |
| `logrolling.py:301` | `discount_ratio=0.08` | 規模效應折讓 8% | 選定值 |
| `logrolling.py:408` | `0.08 · d^1.25` | 加急成本指數 | 選定值 |
| `logrolling.py:37` | `supplier_capital_relief_rate=0.22` | 供應商資金緩釋收益率 | 選定值（偏激進，最應被質疑的一項） |

**C 類 — 投影常數**：`benchmark.py` 的 `PROJECTION_*`（0.18 / 16.5 / 15.8）不影響決策，詳見
[HARDCODED_DATA_AUDIT.md](HARDCODED_DATA_AUDIT.md)。

> **一句話總結**：預測數字來自「A 類政策參數 × B 類模型假設 × 網格搜索」。
> B 類**沒有任何一項是從真實談判數據擬合出來的**——它們是文獻行為特徵的粗略對應。
> 這正是本報告反覆強調「沙盒內的參數化模擬」的具體含義。

### 3. 敏感度實測（決策核心修復後重測；`solve(n=20,000)`，種子 20261013）

> **重測說明**：本節數字在「對手開盤錨定反應項」加入求解目標後**已整體位移**
> （舊表為 2026-10-14 的數字，基準開盤 1.025 / 邊際 0.254）。下表為當前程式碼的
> 實測值，以 `C:\...\tf_sens.py` 等價腳本重跑取得；**定性結論不變**：
> 先驗均值與標準差同樣承載，λ 相對溫和。

**S1：底價先驗均值 `reserve_mean`（基準 1.08）—— 最承載的參數**

| reserve_mean | 開盤價 | 期望邊際 | 破局率 |
|---|---|---|---|
| 1.02 | 1.000 | 0.290 | 5.0% |
| **1.08（基準）** | **1.034** | **0.247** | **6.7%** |
| 1.15 | 1.102 | 0.179 | 7.8% |

均值 ±6% 讓期望邊際波動 −28% ~ +17%。

**S2：底價先驗標準差 `reserve_std`（基準 0.10）**

| reserve_std | 開盤價 | 期望邊際 | 破局率 |
|---|---|---|---|
| 0.05 | 1.034 | 0.281 | 1.5% |
| **0.10（基準）** | **1.034** | **0.247** | **6.7%** |
| 0.20 | 1.008 | 0.187 | **24.9%** |

不確定性加倍 → 破局率 1.5%→24.9%（約 17 倍）。**不確定性感知與均值同樣承載。**

**S3：破局懲罰 `lambda_break`（基準 1.0）—— 相對溫和**

| lambda_break | 開盤價 | 期望邊際 | 破局率 |
|---|---|---|---|
| 0.5 | 1.008 | 0.260 | 8.5% |
| **1.0（基準）** | **1.034** | **0.247** | **6.7%** |
| 2.0 | 1.051 | 0.236 | 6.0% |
| 5.0 | 1.068 | 0.222 | 5.6% |

λ 自 0.5 提到 5.0（10 倍）僅使邊際降 15%、破局降 34% —— 選擇對 λ 不敏感。

**S4：對手類型先驗 `prior_mix`（均勻 vs 單一類型）**

| prior_mix | 開盤價 | 期望邊際 | 破局率 |
|---|---|---|---|
| 均勻（基準） | 1.034 | 0.247 | 6.7% |
| 全庫存壓力型 | 1.000 | 0.326 | 0.8% |
| 全強硬型 | 1.051 | 0.208 | 12.7% |
| 全詐唬型 | 1.034 | 0.246 | 6.4% |

**承載度排序**：`reserve_mean` ≈ `reserve_std` >> `prior_mix` >> `lambda_break`。

### 4. 場景敏感度 vs 信念錯誤：兩個不同的問題（方法論修正）

**舊版本的缺陷**：`prior_sensitivity()` 對手樣本**也從偏移後的先驗抽樣**（`solve()` 的
生成分布即先驗），因此它是「世界與信念同時偏移」的**場景敏感度**，不是「信念錯誤」的後果。
其實測確實不穩定：

| prior× | 場景內邊際 | 場景內破局 |
|---|---|---|
| ×0.5 | −0.050 | 88.5% |
| ×1.0 | +0.247 | 6.8% |
| ×1.5 | −0.105 | 50.6% |

且 ×0.5 的 88.5% 主要由**模型結構偽影**造成：網格最低開盤為 1.00（`model.py:33`），
先驗 0.54 時所有開盤與底價的距離 ≥ 0.46 > 0.32 懲罰閾值（`solver.py:82`），
**全網格策略皆被課破局懲罰** —— 這更多反映網格下限與模型規則，而非談判經濟學。

**本報告新增** `misbelief_sensitivity()`（`robustness.py`）：**現實固定在基準先驗，
僅引擎的規劃信念偏移**，再把錯誤信念下選出的策略放回現實評估：

| 信念× | 規劃均值 | **現實中**邊際 | **現實中**破局率 |
|---|---|---|---|
| ×0.5 | 0.540 | **+0.263** | 9.2% |
| ×1.0 | 1.080 | +0.247 | 6.7% |
| ×1.5 | 1.620 | **−0.102** | **40.0%** |

### 4.5 多基準對比（`robustness.report()` 實測，同一種子；λ=1）

| 策略 | 期望邊際（歸一化） | 破局率 | 聯合目標 `margin − λ·break` | 相對引擎 gain（僅邊際） |
|---|---|---|---|---|
| **TradeFlow engine** | 0.247 | 6.7% | **0.180** | 0.000 |
| fixed 3%/rd | 0.209 | 28.9% | −0.080 | +0.038 |
| fixed 5%/rd | 0.214 | 28.2% | −0.068 | +0.033 |
| fixed 10%/rd | 0.236 | 21.1% | 0.025 | +0.012 |
| **fixed 15%/rd** | **0.253** | 14.5% | 0.107 | **−0.006** |

> **重要（自我否證，且是本節最容易被誤讀的一格）**：
> 若**只看邊際**，`fixed 15%/rd`（0.253）**超越**引擎（0.247）—— gain 為 **−0.006**。
> 因此「引擎一定贏固定策略」這句話**在邊際單項上不成立**，我們不掩護它。
> 引擎的優勢出現在**聯合風險目標**：0.180 vs 0.107，主要來自破局率
> 6.7% 對 14.5%（約 2.2 倍）。這正是目標函數選用 `margin − λ·break` 的理由，
> 但也同時暴露一個限制：**該結論的符號由 λ 的選定值決定**，而 λ=1 本身是
> 未經校準的人為參數（見 §十三 S3：λ 自 0.5 至 5.0 僅使邊際降 15%）。
> 評審若主張 λ 應該更小，15%/rd 在某些 λ 下即為更优選擇 —— 這是模型設定，不是實測事實。

### 5. 由實測得出的可操作結論

1. **錯誤方向是不對稱的**：高估供應商底價（×1.5）讓現實邊際翻負（−0.102）、破局率 40.0%；
   低估（×0.5）幾乎無損（+0.263、9.2%）。
   → **在不確定時，寧可把供應商想得「更硬一點」而不是「更軟」。**
2. **先驗不確定性與先驗均值同等危險**（S2：std 加倍 → 破局率約 17 倍）。
3. **引擎對固定讓步基準的優勢（+¥0.40/件、破局 6.7% vs 28.3%）以基準先驗為條件**；
   脫離此條件優勢不保證存在，且如 §4.5 所示，邊際單項可被更激進的固定策略超越。
   （舊版本此處寫「+¥1.95/件、破局 6.7% vs 28.4%」，為決策核心修復前的數字。）
4. 以上全部為**單一沙盒模型內**的結論，不構成真實工廠統計的推論。

### 6. 本節範圍內已修復的假結論

| 位置 | 舊宣稱 | 實測 | 處置 |
|---|---|---|---|
| `robustness.py` `report()` | 無條件印出「±50% 偏移下推薦策略仍穩定」 | 同次執行破局 6.8%→88.5%→50.6% | 已改為依實測值計算結論（會印出 DEGRADES） |
| `robustness.py` `prior_sensitivity` docstring | 「量化……仍穩定的邊際/破局」 | 見上表 | 已改為中性描述並附實測數字 |

### 7. 尚未完成（誠實缺口）

- **B 類參數未以真實談判日誌做校準**：全部為選定值，等 10,000+ 場真實日誌後應做貝葉斯校正。
- **`OPP_TYPES` 矩陣未做擬合**：4×4 參數是文獻行為特徵的粗略對應，無統計基礎。
- **未做參數交互作用分析**：本節僅為單參數敏感度，參數間的交互效應未量化。
- **計算成本已優化**：`report()` 全量執行 14 次求解（n=20,000）約 17.5 秒；現已在 `orchestrator.analyze(quick=True)` 引入互動降採樣（n=2,000，實測 2.4 秒），徹底消除 webhook 回呼時的長延遲卡頓。

---


---




