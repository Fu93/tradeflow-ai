# TradeFlow AI — Pitch Deck (10 slides)

> AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD
> **One-line thesis**: 我們不是賣採購工具，我們是一間**由 AI 驅動、只有一個人的跨境貿易公司**。

---

## Slide 1 — Problem: 跨境採購不是聊天，是一場資訊不對稱的博弈

一個想從中國工廠採購的美國賣家，今天只有兩個選擇：

| 選項 | 代價 |
|---|---|
| **自己熬夜談** | 用 Google 翻譯硬聊，工廠一聽是外國人直接抬價 20%；看不懂「模具費另算」「偷換二料」的潛台詞 |
| **雇駐華代理** | 付 7%+ 佣金，代理私下向工廠吃回扣；定金被索要 80% 後產能延誤，兩頭推諉 |

**為什麼不能用一個 LLM 聊天機器人解決？**
官方 brief 明確要求：*"Do not use a language model as the only forecasting method."*
學術實測（NegotiationArena / Bianchi et al., ICLR 2024；AgenticPay, arXiv:2602.06008；Cattle Trade, arXiv:2605.14537）指出：面對強硬與詐唬對手，無賽局調度的 LLM 會出現嚴重策略短視。

而我們在 AMD 端點上做了**自己的真實實驗**（100 場、348 次真實推理、$0.108）：面對詐唬型對手，真實模型的**成交率只有 61%**（同一自博弈 artefact 中引擎為 100%，即 20/20，僅 20 場樣本；跨 artefact 比較不可直接解讀）——它真正的失效模式是**破局與過快讓步**，不是亂花錢。

> **誠實披露**：我們的參數化模擬器曾預測「違規率 20%–40%」（現行 artefact；歷史版本為 12%–44%）。真實模型實測 **0/94 違規**（精確 95% 單邊上界 **3.1%**）——已足以在該信心水準下**否證**模擬器的任一版數字。我們如實報告這個**未重現的結果**（見 `LIMITATIONS` §2.3），並把 AST 護欄的定位從「攔截違規」修正為**防禦縱深**。

---

## Slide 2 — One-liner: 一間只有一個人的貿易公司

**TradeFlow AI 是一間 AI-native trading company（AI 原生貿易公司）。**

我們把「跨境採購」這個每天真實發生的業務流程，交給一組 Agent 執行。
公司的經營者只有一個人，而**供應商訊息的理解、核價、風控與戰術生成全部由系統完成；
但發送動作永遠由人決定** —— 系統不代發要約。

> **「數學管策略，LLM 寫行話。」**
> 決策核心是純數值的向量化期望最大化求解器，輸出語言中立的結構化決策物件；
> LLM 只負責把它渲染成工廠聽得懂的地道行話，以及英文高管決策卡。

**為什麼這樣定位？** 因為官方 Evolus 賽道要的是 *"a real process that a real company runs every day"*
——沒有比「一間公司本身」更真實的 process。

---

## Slide 3 — Why not a chatbot: 自博弈對抗模擬（落盤 artefact 100 場）

> **場次更正**：先前標題寫「750 場」—— 該數字來自 `scripts/run_arena_benchmark.py`
> 寫死的 header 字串，與實際參數無關。該 header 已改為由實際參數插值，
> 並且在決策核心修復（幽靈買方歷史、對手 reserve 綁定、基準可違規化）後，
> Artefact 已以 **20 matches/archetype × 5 原型 = 100 場**、seed=42 重新產出
> （`results/self_play_benchmark.md`）。下表數字為該 artefact 的實際值。

| 指標 | TradeFlow Engine | Parametric Buyer Proxy |
|---|---|---|
| **預算違規率** | **0.0%**（AST 硬約束；基準線現在**可**違規以讓比較有意義） | **20.0% – 40.0%**（壓力下擊穿上限） |
| **採購節省率**（vs 詐唬型對手） | **15.0%** | 3.6% |
| **節省率區間**（vs 5 類原型） | 8.8% – 20.0% | 3.6% – 17.0% |

**關鍵差異**：參數化代理在談判壓力下會「恐慌讓步」；TradeFlow 的越界在編譯期就被 AST 物理阻斷。

> **誠實揭露**：這 100 場來自**依學術文獻校準的參數化行為模擬器**，不是真實雲端 LLM 呼叫。
> 每格 20 場，解析度有限（「100.0%」實為 20/20）；引擎與「每輪固定讓 5%」基準的節省率在
> 5/5 個原型上互不相同，但引擎在 Hard-Bargainer 上仍遜於基準（8.8% vs 9.2%）——
> 我們如實保留該格，不調校引擎去贏每一格。
> 完整行為公式公開於 `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §2，供評審本地復現。

---

## Slide 4 — Architecture: 為什麼要解耦

```
工廠訊息 ──► Stage 1: 確定性正則階梯（~1ms，0 tokens）
                    │ 未命中
                    ▼
             Stage 2: 端側 ONNX + AMD 託管開源模型（語意推理）
                    │
                    ▼
        ┌───────────────────────────────────┐
        │  數值決策核心（無 LLM）            │
        │  · 貝氏信念更新（高斯-均勻混合）    │
        │  · 向量化 Expectimax 求解器        │
        │  · 多維條款置換（Logrolling）      │
        └───────────────┬───────────────────┘
                        ▼
        TacticalActionObject（語言中立單一真相源）
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
  AST 硬護欄（編譯期攔截）      雙語表面渲染
  · authorized_price ≤ ceiling   · 中文：沿海老買家行話
  · deposit ≤ 60%                · 英文：CPO 決策卡
```

**設計原則**：LLM 永遠不決定出價。它只能渲染一個**已經被數學算出來、且已被 AST 驗證過**的決策。

> **定金雙軌說明**：Logrolling 業務規則強制定金 ≤50%（嚴於）；AST 硬護欄紅線為 60%，作為最後防線（參數見 `src/engine/logrolling.py`）。

---

## Slide 5 — Demo: 3 個可驗證的瞬間

1. **信念收斂（Belief Convergence）**
   工廠報價進來後，後驗底價均值即時修正，開盤出價同步調整（¥12.30 → ¥14.23）。
   每一輪的數字都可追蹤、可解釋。

2. **AST 護欄現場攔截**
   當工廠要求 85% 定金或越界單價時，AST 在編譯期拋出 `ASTMandateViolationError`，
   系統降級至安全話術，**不會**把越界承諾送出去。

3. **多供應商動態 BATNA**
   同時並行 4 家供應商，即時計算「排除第 i 家後的外部最優期望值」，
   自動分流為 `LEAN_IN` / `CROSS_ANCHOR` / `SIDELINE` 三種戰術。

---

## Slide 6 — Business value: 一間公司，一個人

**這是我們與所有參賽團隊最大的不同：我們不是在提案一個產品，我們是在運營一間公司。**

| | 傳統路徑 | TradeFlow AI |
|---|---|---|
| 人力 | 採購團隊 或 7% 佣金代理 | **1 人** |
| 每輪回應 | 人工判讀 + 回覆 | Stage-1 確定性 ~1ms（**預設路徑**）；完整輪次約 3.3s（其中求解器主體約 1.7s，20K 樣本；量測環境：開發機 x86-64 CPU）；Stage-2 語意增強為**選配**，額外 2–13s |
| 底線保障 | 靠人的自制力 | **AST 編譯期物理攔截，0% 擊穿** |
| 中間人回扣 | 無法稽核 | **不存在中間人** |
| 行話能力 | 需要多年經驗 | 模板化沿海老買家話術 |
| 締約權 | 買家本人 | **買家本人**（系統只提議，不代發要約） |

**可量化的增益**（vs 開盤報價）：節省率 **8.8% – 20.0%**；預算違規 **0%**。
以首單 10,000 把雨傘為例：開盤 ¥14.80 → 博弈達成 ¥13.00，每把省 ¥1.80，合計 **¥18,000**（**單一供應商議價情境**；多供應商拍賣為另一情境：22.2% / ¥36,000；相對固定讓步基準的邊際增益為 +¥0.40/件 —— 三者不可互換）。

> *數據來源：HS6601 品類歷史價格帶下的 Expectimax 模擬輸出，非單一特定工廠合約。*

---

## Slide 7 — AMD 技術整合（誠實版）

**我們不說「因為需要 192GB 顯存」——那是錯的。**
多供應商示範路徑（Web「Run Auction」預設）的 **28.8M rollout 位**（每輪 9.6M × 3 輪）是
$K \times S \times N_{open} \times N_{sched}$ 的**算術乘積，不是量測到的執行次數** ——
真正的 rollout 由一次向量化廣播完成，28.8M 只是「被同時求值的參數組合數」。
**工作集以 `src/engine/benchmark.py` 的 tracemalloc 實測為準：9.6M rollout 峰值約 678.1 MB**
（`results/bench.json`；舊版 `n × 8 bytes` 公式只給 73.2 MB，低估約 9.3 倍，不再採用）。
即使按最大策略網格理論上界 96M 以同一公式外推也僅約 384 MB。這是**頻寬受限（bandwidth-bound）**，不是容量受限。

**AMD 的真實價值：**
1. **統一 ROCm 抽象層**：`src/engine/solver.py` 的 `_xp()` 讓同一套張量博弈程式碼在
   x86-64 CPU（NumPy SIMD）開發、一鍵切換至 AMD Instinct GPU（ROCm/CuPy）生產部署。
2. **AMD tokenfactory 模型服務（已實測）**：Stage-2 語意層真實呼叫
   `https://developer.amd.com.cn/radeon/api` 上的開源模型，延遲、token 成本皆為實測值。

> **誠實邊界**：MI300X 的時延數字是**分析性 roofline 資料搬運下界**，非實機量測，
> 也不是執行時間預測。我們的開發機是 **Intel Core Ultra 7 355（x86-64）**——
> **本專案從未在 AMD CPU 或 AMD GPU 上執行過任何運算**。
> 我們選擇把這件事寫清楚，而不是用「AMD 環境」含混帶過。

---

## Slide 8 — Evolus 整合（全部為真實呼叫）

Evolus 賽道以 workspace 的 **usage statistics** 為驗收依據，因此我們只列**已實際產生用量**的項目：

| Building Block | 狀態 | 實測用量 |
|---|---|---|
| **AI Tool: `email`** | ✅ 已驗證 | 2.46 credits — 起草商務回信 |
| **AI Tool: `doc-structure`** | ✅ 已驗證 | 2.12 credits — 報價文字 → 結構化 JSON（單價/MOQ/交期/定金/模具費全數正確抽取） |
| **AI Tool: `sentiment`** | ✅ 已驗證 | 3.75 credits |
| **App 驅動 REST 整合** | ✅ 已驗證 | `orchestrator.py` 接收回呼 → 跑引擎 → 呼叫 Evolus 起草 |

**端到端實測輸出**：
> *"Based on our cost analysis, we can offer **¥13.12** per unit for an initial order of **500 units**."*

該草稿正確引用了引擎算出的目標價（¥13.12）與供應商實際報價（¥15 / MOQ 500），
證明 Evolus 與自研決策引擎已完成**真實資料串接**，而非文件層面的宣稱。

> **尚未完成**：workflow 尚未建立（`GET /api/v1/workflows` 回傳空陣列）；agent（Supplier Negotiation Analyst）與 Personal Library 知識庫已建立並實測（2026-10-14，見 LIMITATIONS §12）。我們不宣稱 workflow 已完成。

---

## Slide 9 — 誠實面對局限性

我們認為：**在技術評審面前，誠實劃定邊界遠比維持完美自洽的沙盒更有價值。**

1. **不是 CFR，是 Expectimax**。它假設對手策略分佈已知（4 類工廠原型各 25% 先驗）。
   若真實工廠完全脫離此分佈，最優性保證不成立。
2. **「信心度」不是校準概率**，而是後驗方差的收斂指標。
3. **AST 只保護結構化物件**，無法理解渲染後的自然語言語義；語言層依賴模板與正則過濾。
4. **BATNA 假設供應商獨立定價**。在義烏、紹興等產業集群，隱性價格卡特爾會削弱交叉施壓效果。
5. **評測效力邊界**：意圖基準集為 in-distribution 回歸集（與正則詞彙重疊），
   500 條集為程式化生成（**66 條基礎模板**回收、遮蔽數字後僅 132 條互異）。兩者均非人工盲測集。
   我們評測的是該集**與訓練集零交集的 held-out 切分（195 條）**，實測 **63.08%** ——
   先前對外引用的 92.00% 是**訓練集分數**（舊版以整份 OOD-500 訓練 ONNX 分類頭再用同一份評測），不得作為泛化數字。
6. **Stage-2 延遲為 2–13 秒**（reasoning 模型 + 共享推論服務）。我們不宣稱「端到端 <3ms」。
7. **未經真實工廠閉環驗證**。所有指標來自沙盒與模擬。

---

## Slide 10 — Next steps

1. **補齊 Evolus workflow**：建立 workflow（email-in → doc-structure → 回呼）。agent（已綁定 AMD 託管模型）與 Personal Library 知識庫已於 2026-10-14 建立並實測（見 Slide 8 與 LIMITATIONS §12），workflow 為後續補強項。
2. **累積真實談判日誌**：在取得 10,000+ 場真實外貿商務日誌後，導入真正的 CFR
   （Counterfactual Regret Minimization）做離線自適應迭代。
3. **AMD 實機量測**：在 AMD Developer Cloud 取得實例後，以實測數據取代 roofline 投影。
4. **產品化路徑**：從「示範一間 AI 原生貿易公司可行」走向「可複製的單人跨境貿易營運系統」。

---

> **文件狀態**：本簡報取代所有歷史 pitch 版本。架構細節以
> [TARGET_ARCHITECTURE_AND_ROADMAP.md](TARGET_ARCHITECTURE_AND_ROADMAP.md) 為準；
> 技術邊界以 [LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](LIMITATIONS_AND_REAL_WORLD_FINDINGS.md) 為準。
