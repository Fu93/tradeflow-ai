# TradeFlow AI — Pitch Deck (10 slides)

> AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD
> 核心定位：基於向量化期望最大化 (Vectorized Expectimax) 與 AST 數值硬護欄的跨語言採購決策原型

---

## Slide 1 — Problem: 為什麼對話式 AI 解決不了 B2B 採購？
- 採購不是聊天，而是在信息不對稱下的多屬性約束博弈。
- **純 Prompt LLM 的致命缺陷**：學術實測（AgenticPay / CATTLE TRADE）證實，面對強硬與詐唬對手，純 LLM 會發生「過度妥協」與「預算擊穿」（違背率高達 44%），且容易幻覺出未經授權的定金承諾。
- 主賽事 Brief 明確要求：*“explain how the prediction was calculated. Do not use a language model as the only forecasting method.”*

## Slide 2 — One-liner: 什麼是 TradeFlow？
**TradeFlow**：以**「向量化期望最大化 (Vectorized Expectimax) + 抽象語法樹 (AST) 硬護欄」**為核心的跨語言採購決策引擎。
**「數學管策略，LLM 寫行話」**——策略語言中立核心輸出純數字決策，AST 在物理層阻斷越界，彻底杜絕外商身分溢價與幻覺承諾。

## Slide 3 — Why not a chatbot: 750 場對抗實測對比
- **嚴謹實證基準**：對戰 5 類非 LLM 對抗原型（Hard-Bargainer, Conceder, Tit-for-Tat, Bluffing Hardliner, Noisy Seller）。
- **預算違規率 (Budget Violation Rate)**：
  - **TradeFlow Engine**：**0.0%**（AST 語法樹硬約束 100% 物理截斷）
  - **Vanilla LLM Agent**：**12.0% ~ 44.0%**（在壓力下擊穿 Walk-Away Ceiling）
- **採購節省率 (Savings Rate)**：
  - 面對 Bluffing 詐唬型對手：TradeFlow **18.5%** vs Vanilla LLM **3.7%**。

## Slide 4 — Architecture: 雙 Agent 解耦與異質運算拓撲
(見 README 系統拓撲圖)
- **決策核心 (Decision Core)**：純 Python / NumPy 張量廣播求解器，輸出結構化 `TacticalActionObject`。
- **表面渲染層 (Surface Renderers)**：中文老買家行話模板（沿海地道行話） + 英文 CPO 決策卡。
- **企業授權中樞 (Corporate Mandate)**：帕累托 Logrolling 多維條款置換（定金換交期/模具費減免）。
- **Evolus 整合**：文件解析提取 PDF 報價，驅動 AI 工具鏈發送正式商務郵件。

## Slide 5 — Demo: 3 Wows (真實可驗證資產)
1. **Belief Convergence**：供應商回價後，後驗信念均值動態收斂（1.08 → 1.25），開盤出價同步調整（¥12.30 → ¥14.23），可解釋、可驗證。
2. **AST Guardrail Interception**：現場展示當工廠試探或要求 85% 定金/越界單價時，AST 護欄在代碼編譯層 100% 報錯並拋出 `ASTMandateViolationError` 降級至安全話術。
3. **Multi-Supplier Dynamic BATNA**：同時並行 4 家供應商談判，動態計算外部最優替代，自動執行 `CROSS_ANCHOR` 交叉施壓。

## Slide 6 — Business value: 真實用戶故事 (Alex's Story: Before vs After)
- **具體用戶 (User Persona)**：Alex Rivera，洛杉磯 Amazon 戶外五金品牌創始人，年銷售額 $3M，年採購雨傘 5 萬把。
- **現狀困境 (The Painful Alternatives)**：
  - *方案 A (自己熬夜聊 1688)*：用 Google Translate 溝通，工廠一眼看出是老外直接抬價 20%（¥14.80 出廠價），看不懂「模具費另算」、「偷換二料」等潛台詞，被坑還說 Thank you。
  - *方案 B (僱用駐華採購代理)*：除付 7% 佣金外，代理私下向工廠吃回扣；定金索要 80% 後產能延誤，代理兩頭推諉。
- **TradeFlow 改善 (The TradeFlow Transformation)**：
  - Alex 洛杉磯下午 4 點在 WhatsApp 收到英文 CPO 決策卡（TCO 影子成本折算完畢、定金死守 50%、模具費由工廠全免），點擊「1」授權。
  - Agent 在 1688 上以沿海金牌老買家行話自駕談妥，首單 10,000 把省下 ¥18,000 人民幣（約 $2,500 美元）\*，零回扣、零幻覺。
- *\*註：¥18,000 係依據 HS6601 品類歷史價格帶（開盤 ¥14.80，成交 ¥13.00，節省 ¥1.80/把）之 Expectimax 模擬輸出，非指單一特定工廠合約。*

## Slide 7 — AMD 戰略價值：統一 ROCm 工具鏈 (Unified ROCm Portability)
- **CPU 優先設計 (CPU-First by Design)**：
  - 核心談判求解單輪 <2ms，日常單一對弈在 CPU 上已經足夠快，無需盲目堆疊 GPU 算力。
- **AMD 的真實核心價值：統一 ROCm 軟體生態與跨設備零重構**：
  - **`_xp()` 抽象層無縫切換**：同一套張量博弈代碼庫，在開發階段於 AMD CPU (NumPy SIMD) 運行，部署階段一鍵直通 AMD GPU (ROCm / CuPy)，邊緣端適配 Ryzen AI NPU。
  - **大模型與算力共生 (Zero PCIe Penalty)**：在並行逆向拍賣（4 供應商同時博弈）與本地 Qwen-14B/72B 推理共存的企業級場景下，AMD MI300X 的統一內存池使大語言模型與張量求解器同處一處，徹底消除跨設備數據拷貝延遲。

## Slide 8 — Evolus Business Agents 整合深度
- 4 大 Building Blocks 全鏈路貫通：
  - **Workflow**：觸發 Inbound 郵件與 PDF 報價單提取。
  - **Document Extractor**：結構化解析供應商初始單價、交期與 MOQ。
  - **AI Tools**：受 AST 護欄保護的商務郵件起草。
  - **Knowledge Library**：儲存海關 HS 編碼與品類標準 TDS 技術規範。

## Slide 9 — 誠實面對局限性 (Limitations & Technical Rigor)
- **非全自動神器，而是受約束的副駕駛 (Human-in-the-Loop Copilot)**：
  - 破局風險超標（$P_{\text{break}} \ge 15\%$）或觸發平台驗證碼時，瞬間掛起並通過 WhatsApp/微信呼叫人類授權。
  - AI 輸出在法律上定義為「邀約引誘」，最終 PO 蓋章永遠鎖定在人工授權節點。
- **信念指標定位**：坦承「置信度」為後驗方差倒數之工程收斂指標（Belief Convergence Metric），非大規模歷史標定概率。

## Slide 10 — Next steps: 從原型走向生產級
1. **真實談判日誌與離線 CFR 強化學習**：在累積 10,000+ 場真實外貿商務日誌後，接入真正的 CFR / Counterfactual Regret Minimization 算法。
2. **AMD 實體機實測**：在 AMD 雲端實例完成 ROCm / CuPy 端到端真實內核 Benchmark 驗證。
3. **賽道雙投**：主賽道（Reinvent Commerce）+ 合作夥伴專項（Evolus Business Agents）。
