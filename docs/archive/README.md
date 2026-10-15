# 封存文件索引 (Archived Documents Index)

本目錄保存 TradeFlow AI 在黑客松期間產生、**已被取代的歷史規劃文件**。
保留目的是可追溯性（為什麼做了某些技術決策），**不是**現行開發依據。

> [!IMPORTANT]
> **現行唯一權威來源**
> - 架構與規格：[TARGET_ARCHITECTURE_AND_ROADMAP.md](../../TARGET_ARCHITECTURE_AND_ROADMAP.md)
> - 專案入口與快速開始：[README.md](../../README.md)
> - 技術邊界與實證揭露：[LIMITATIONS_AND_REAL_WORLD_FINDINGS.md](../../LIMITATIONS_AND_REAL_WORLD_FINDINGS.md)
> - 賽道要求對照：[COMPETITION_STRATEGY.md](../../COMPETITION_STRATEGY.md)
> - 提交清單：[SUBMIT.md](../../SUBMIT.md)

---

## 為什麼會有這麼多份計畫書？

專案在 2026/10/13–14 的 48 小時內，歷經了 **9 個世代的計畫迭代**。每一代都在前一版的基礎上重寫，
但舊版並未被移除，導致根目錄同時存在多份互相矛盾的「最終版」文件（`_Final`、`_Master`、`_PlanB`…）。

最關鍵的一次轉折是 **Autopilot → Copilot** 的定位轉變：

| 世代 | 文件 | 核心主張 |
|---|---|---|
| 1 | `TradeFlow_AI_Hackathon_Plan.md` | 5 Agent 跨國貿易自動化（選品/供應商/客戶/SEO/進出口）；尚未出現 Autopilot |
| 2 | `TradeFlow_Implementation_Plan_Master.md` | v4 實作計畫；**Autopilot 於此世代已成形**（`AutopilotState` 狀態機、Chrome 無人自駕擴充套件） |
| 3 | `TradeFlow_Upgrade_Plan_Final.md` | 將 Autopilot **品牌化並擴大**為「In-Browser Autopilot」完整規格 |
| 4 | `TradeFlow_PlanB_Final.md` | 5.5 天 Plan B 收斂方案 |
| 5 | `TradeFlow_Execution_Manual.md` | Plan B 的逐日執行手冊 |
| 6 | `TradeFlow_Web_Portal_Plan.md` | 獨立 Web 戰情大屏規格 |
| 7 | `TradeFlow_Cross_Lingual_Sourcing_Blueprint.md` | 跨語言認知橋樑藍圖 |
| 8 | `TradeFlow_Final_Convergence_Plan.md` | 84 小時衝刺與範圍凍結 |
| 9 | **[現行] `TARGET_ARCHITECTURE_AND_ROADMAP.md`** | **放棄 Autopilot，轉型 Copilot（AI 提議、人類定奪）** |

**Autopilot（代表買家自動發送要約）已被明確廢棄。** 該主張自世代 2 成形、世代 3 擴大，
但理由見現行文件 §1.1：在即時通訊中確認價格即構成法定要約，
企業不可能將締約權交給非確定性算法；且此架構需要偽造合成 DOM 事件，會觸發平台風控並帶來締約風險。

> **附註**：上表的世代編號是依文件內容與互相引用關係整理的**敘事順序**，
> 部分文件的絕對先後（尤其世代 6–8）無法從文件內部完全確認，僅供理解演進脈絡之用。

---

## 封存清單

| 文件 | 世代 | 取代者 | 仍具參考價值的部分 |
|---|---|---|---|
| [TradeFlow_AI_Hackathon_Plan.md](TradeFlow_AI_Hackathon_Plan.md) | 1 | TARGET_ARCHITECTURE | **5 Agent 產品願景**（供應商 Agent 為現行主線） |
| [TradeFlow_Implementation_Plan_Master.md](TradeFlow_Implementation_Plan_Master.md) | 2 | TARGET_ARCHITECTURE | Transport 抽象層的原始設計動機 |
| [TradeFlow_Upgrade_Plan_Final.md](TradeFlow_Upgrade_Plan_Final.md) | 3 | TARGET_ARCHITECTURE | 已廢棄的 Autopilot 架構（負面案例） |
| [TradeFlow_PlanB_Final.md](TradeFlow_PlanB_Final.md) | 4 | TARGET_ARCHITECTURE | 時間盒收斂方法論 |
| [TradeFlow_Execution_Manual.md](TradeFlow_Execution_Manual.md) | 5 | TARGET_ARCHITECTURE | 逐日執行排程範本 |
| [TradeFlow_Web_Portal_Plan.md](TradeFlow_Web_Portal_Plan.md) | 6 | TARGET_ARCHITECTURE | Web Portal 原始規格（已實作） |
| [TradeFlow_Cross_Lingual_Sourcing_Blueprint.md](TradeFlow_Cross_Lingual_Sourcing_Blueprint.md) | 7 | TARGET_ARCHITECTURE | 跨語言層設計（已實作於 `src/engine/bilingual.py`） |
| [TradeFlow_Final_Convergence_Plan.md](TradeFlow_Final_Convergence_Plan.md) | 8 | TARGET_ARCHITECTURE | 範圍凍結決策（多數已採納） |

---

## 注意事項

1. 本目錄文件中的**測試數量、準確率、硬體數據均可能過時**。現行數字請以 `README.md` 與
   `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` 為準。
2. 部分文件宣稱的 Autopilot 功能**在程式碼中已不存在或已改為 Copilot 模式**。
3. 部分文件記載的日期、獎池金額與賽道定義已與官方頁面不符（官方已更新）。
