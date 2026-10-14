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

## Slide 6 — Business value: 目標用戶畫像與真實買方痛點 (Target Persona Audit)
- **目標用戶 (Target Persona)**：年 GMV 100 萬～1,000 萬美元之歐美中型跨境電商進口商（Amazon / Shopify 賣家，採購五金、傘具、戶外家居）。
- **痛點 1（外商溢價）**：直接在 1688 / 義烏採購時，外商因英語溝通被工廠索取 15%～25%「外國人溢價」。
- **痛點 2（代理尋租與回扣）**：僱用駐華採購代理除 5%～8% 佣金外，普遍存在代理與工廠串通吃回扣風險。
- **痛點 3（跨語言潛台詞盲區）**：英文高管無法辨識中文工廠「哭窮、改模具費、偷換次料」等套路，手動審批疲於奔命。
- **TradeFlow 解決方案**：
  - 本土金牌行話自動回覆消除身分溢價；
  - 核心求解器與 AST 護欄杜絕私相授受與回扣空間；
  - 高管僅需在 WhatsApp 接收結構化英文決策卡（含 TCO 等效折算、定金比例與底線校驗），一鍵批准關鍵邊界，真正實現「管住錢袋子，放開自駕車」。

## Slide 7 — AMD 異質運算技術佈局 (Heterogeneous Topology)
- **CPU (AMD Zen 核心)**：運行 SQLite WAL 事務原子持久化、會話狀態機、零 LLM 的確定性本土行話渲染 (<2ms 延遲)。
- **GPU (AMD Instinct MI300X on ROCm)**：
  - 4 供應商並行逆向拍賣的 96M 張量巨型求解器（4 廠 × 50,000 樣本 × 480 策略族）。
  - **記憶體頻寬受限特質 (Bandwidth-Bound, 非顯存容量受限)**：96M FP32 矩陣僅佔約 384MB，但其密集的 3D 廣播乘加極度依賴訪存吞吐；MI300X 高達 **5.3 TB/s 的 HBM3 頻寬** 恰好解決了 DDR5 (約 80 GB/s) 的頻寬瓶頸（Roofline 模型分析加速 ~15x）。
  - **統一 ROCm 工具鏈**：在單一 AMD 軟體棧中同時承載 Qwen-14B/72B 併發推理與量化求解器，消除跨設備 PCIe 拷貝。
- **NPU (AMD XDNA / Ryzen AI)**：規劃邊緣部署輕量級意圖分類器（Intent Classifier）與敏感個資（PII）本地脫敏。

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
