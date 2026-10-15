---
name: truthful-implementation-audit
description: >-
  Strict anti-sidecar, hot-path verification and roadmap reality audit protocol. Use whenever
  implementing new architecture, refactoring legacy algorithms, claiming feature completion,
  or reporting roadmap milestones to eliminate "sidecar prototyping illusion" (writing isolated
  modules without wiring production hot paths), premature "all green" claims, and false completion reports.
---

# Truthful Implementation & Hot-Path Reality Audit Protocol (真實實現與調用鏈穿透驗證規範)

> **核心宗旨**：代碼寫出不等於系統接通；單元測試全綠不等於主鏈路已落實。嚴禁「在旁路寫了新算法，生產熱路徑卻依舊在跑舊代碼」的工程欺瞞（Potemkin Engineering）。

---

## 🛑 4 大不可違背之工程鐵律 (Fundamental Invariants)

### 1. 熱路徑調用鏈貫通原則 (Hot-Path Callgraph Invariant)
* **核心定義**：任何新開發的算法、核價、安全或狀態機模組，**必須被生產運行時熱路徑（Runtime Hot Path）直接調用**（如 `main.py` 路由處理器、`orchestrator.py`、核心求解循環），並實質作為後續決策的輸入參數。
* **嚴格禁令**：
  - 凡代碼僅在獨立單元測試中被 import，未被生產代碼調用者，**嚴禁聲稱「已完成/已落地」**，只能客觀標註為 `[-] 獨立原型已就緒，主鏈路尚未接通`。
  - 凡僅在 API 回傳 JSON 尾部添加字段供「前端觀賞」，而未參與實質出價決策與博弈運算者，**嚴禁宣稱「算法已升級」**。

### 2. 舊代碼截斷與廢棄原則 (Legacy Pruning & Deprecation Invariant)
* **核心定義**：當新模組旨在淘汰舊邏輯（例如以 `bayesian_v2` 淘汰經驗步進，以 `should_cost` 淘汰常數 TCO）時，**必須重構或移除舊代碼在熱路徑上的調用**。
* **負向自檢法 (Negative Verification Test)**：
  - 在舊邏輯入口主動插入 `raise RuntimeError("Legacy code should not be called")`。
  - 運行端到端業務測試：若測試依然無痛通過且未觸發該異常，說明**新代碼根本沒有接管主鏈路，系統依然在暗中依賴舊代碼！**

### 3. 嚴格分階段驗收原則 (Strict Phase Milestone Invariant)
* **核心定義**：明確劃分「當前階段任務」與「未來整體規劃」。
* **嚴格禁令**：
  - 嚴禁在 Phase 1 局部模組完成時，使用「路線圖已做完」、「全面重構完畢」等泛化結論掩蓋未做的 Phase 2/3/4。
  - 任何進度匯報必須強制輸出「全景三態清單」：
    - `[x] 已全鏈路貫通 (Production Hot Path Verified)`
    - `[-] 原型就緒但主鏈路未接通 (Isolated Prototype Only)`
    - `[ ] 尚未開始 (Pending)`

### 4. 匯報前自毀式對抗自審 (Pre-flight Reality Audit)
* 在向用戶輸出任何「已完成」結論之前，必須主動執行 3 步自檢：
  1. **查調用點**：`grep -rn "<新類名/新方法名>" src/`，驗證調用方是否包含生產入口。
  2. **查版本控制**：`git diff --stat`，檢查核心業務文件（如 `main.py`, `solver.py`）是否被實質修改。
  3. **查驗收標準**：對照路線圖的 Exit Criteria（如延遲指標、對抗樣本量、並發鎖），逐條確認是否具備實測日誌。

---

## 📋 開發與審計標準作業程序 (SOP)

```
[編寫新模組 + 單元測試]
        │
        ▼
[步驟 1: 接入熱路徑] ────► 修改 main.py / solver.py，將決策輸入全量切換至新模組
        │
        ▼
[步驟 2: 截斷舊依賴] ────► 刪除或重構舊函數，杜絕旁路雙軌運行
        │
        ▼
[步驟 3: 負向驗證]   ────► 確保舊路徑被阻斷，新路徑承載 100% 業務流量
        │
        ▼
[步驟 4: 端到端回歸] ────► 運行全量 pytest，確認無破壞性回歸
        │
        ▼
[步驟 5: 產出真實進度] ──► 誠實報告：哪些已接通，哪些仍在待辦
```

---

## 🛠️ 回覆格式強制規範 (Reporting Format)

凡向用戶匯報功能開發、重構或路線圖進度時，必須依序包含：

1. **真實調用鏈驗證路徑**：精確指出新模組在哪個文件的哪一行被生產熱路徑調用。
2. **舊邏輯處置說明**：說明舊邏輯是如何被截斷、廢棄或重定向的。
3. **分階段真實完成度清單**：列出 `[x]` / `[-]` / `[ ]` 狀態矩陣。
4. **真實全鏈路有效百分比**：客觀計算，拒絕灌水。
