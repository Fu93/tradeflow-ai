# 驗證與語言邊界報告（2026-10-17，內部紀錄）

> 本檔為**開發/稽核紀錄**，依約定保留中文，不列入提交。提交面向的文件全部改為英文。

## 一、突變測試：證明測試真的會紅（Part 1）

工具：`debug_findings/mutation_check.py`（注入突變 → 跑對應測試 → 還原檔案）。
最終結果 **12/12 KILLED、0 holes**：

| # | 突變（注入的缺陷） | 檔案 | 判定 | 被誰抓到 |
|---|---|---|---|---|
| 1 | 價格夾制失效（越界價直接回傳） | `bilingual.py` | KILLED | test_bilingual + logrolling guardrail |
| 2 | 片語禁用模式集不再被迭代 | `bilingual.py` | KILLED | test_logrolling_guardrail（12 failed） |
| 3 | 核准價改用目標價 | `app/main.py` | KILLED | test_audit_2026_10_17_fixes |
| 4 | annotate_last_turn 變 no-op | `session/manager.py` | KILLED | 同上 + copilot approval |
| 5 | 節省率回傳常數 0.222 | `multi_solver.py` | KILLED | test_multi_solver（新增基準一致性測試）|
| 6 | 最佳索引寫死 (0,0) | `solver.py` | KILLED | test_solver_behavior（**新增** argmax 測試）|
| 7 | 非法狀態轉移放行 | `session/manager.py` | KILLED | test_session_fsm |
| 8 | CAS 衝突被忽略 | `session/manager.py` | KILLED | test_concurrency_cas |
| 9 | stage2_error 被吞成 None | `app/main.py` | KILLED | test_audit + async_stage2 |
| 10 | SafeRegex 截斷移除 | `engine/safe_regex.py` | KILLED | test_safe_regex |
| 11 | 儀表板破局風險偽造 0.0% | `web/static/js/app.js` | KILLED | 瀏覽器探針（真 Chromium 讀 DOM）|
| 12 | 圖表先驗寫死 1.08 | `web/static/js/app.js` | KILLED | 瀏覽器探針 + **新增** `tests/test_dashboard_prior_chart.py` |

### 首輪的 4 個 hole 與處置

| 首輪 hole | 性質 | 處置 |
|---|---|---|
| `guardrail_patterns_emptied` | **假突變**：`[] or [...]` 是 no-op，不是真缺陷 | 改成 `for pattern in ():`，既有測試立刻殺掉 |
| `auction_savings_constant` | 測試存在（`test_multi_demo`）但不在我的 subset 內 | 修正 subset，並**新增**三個基準互相一致的回歸測試 |
| `solver_best_index_fixed` | **真 hole**：只斷言「best 在前沿上」，而 (0,0)` 恰好也在前沿上 | **新增** argmax 一致 + `recommend()` 取值一致兩條測試 |
| `dashboard_chart_hardcoded` | **假突變 + 探針錯誤**：`window.beliefChart` 是 `let`，讀不到 | 改讀 `Chart.getChart()`，並**新增**真瀏覽器回歸測試（3 條）|

## 二、測試套件本身的可證偽性修補

- `tests/test_web_xss.py` 的剪貼簿失敗測試原本 grep 中文字面值，其中一條還**命中英文註解**而假通過。
  改為結構化斷言：抽出複製按鈕 handler → 在 `catch` 切開 → 要求成功／失敗兩條路徑渲染不同標籤，
  且失敗路徑必須有 `console.warn`。已驗證：把失敗標籤改成成功標籤 → 2 failed；還原 → 4 passed。

## 三、硬編碼／假數據（Part 2）

| 位置 | 處置 |
|---|---|
| `main.py`（模具費 3000） | 早已移除，只留揭露註解；本次確認無殘留 |
| `logrolling.py` `estimated_mold_fee=3000` | 保留但已是**可覆寫欄位**，並在 LIMITATIONS §13 列為「選定值」|
| `model.py YOUR_CEILING=1.35` | 規格假設，已列於 §13.2 B 類 |
| `bayesian_v2` 0.85 / 0.06 | 已明確標為啟發式常數 |
| `demo_data.py` | 備援值抽成 `FALLBACK_*` 常數並加 warning；`SUPPLIER_REPLIES` 標明是腳本化 demo 回價 |
| `cli.py` `n_samples * 480` | 改由 `strategy_grid()` 實算，並把「張量位數 ≠ 執行次數」寫進輸出 |
| 儀表板 mandate 四列 | target/ceiling/MOQ 標 `ENFORCED`；lead/deposit/tooling 標 `TARGET`，並加一段說明 logrolling 用自身 baseline（25 天／30%）|
| supplier 顯示名 | 中文後綴改英文（Agile Conceder 等），四處引用同步；artefact 重新產生 |

## 四、語言邊界（Part 3）

**改為英文**：買方/操作者可見文字（儀表板、HUD、側邊欄、CLI、API 訊息）、提交文件。
**維持中文**：要貼給中國供應商的話術、模擬供應商對話、知識庫的中文行話對照、內部稽核文件。

| 檔案 | 掃描結果（CJK 行） |
|---|---|
| README / PITCH / SUBMIT / SUBMISSION_DRAFT / extension README | 0 / 0 / 0 / 0 / 0 |
| LIMITATIONS_AND_REAL_WORLD_FINDINGS.md | 5（復現用 prompt、被引用的中文原話）|
| REAL_WORLD_STRESS_TEST_TRANSCRIPT.md | 8（工廠輸入原文）|
| results/self_play_benchmark.md、results/multi_supplier_auction.md | 0 / 0 |
| `extension/content.js` | 12（3 條供應商話術卡 + 價格正則的中文關鍵詞 + 行尾註解）|
| `extension/mock_chat.html` | 4（模擬供應商的對話與公司名）|
| `src/web/**` | 0（僅 1 行行尾註解）|

## 五、最終驗證證據（Part 4）

| 檢查 | 指令 | 結果 |
|---|---|---|
| 全量測試 | `python -m pytest tests -q` | **376 passed + 98 subtests**，exit 0（293.7s）|
| 端到端業務鏈 | `python verify_live.py` | **7/7 PASS**，exit 0 |
| 擴充 E2E（真 Chromium + MV3） | `python tests/test_extension_e2e.py` | **24/24 checks**，exit 0，0 console error |
| 儀表板三種寬度 | Playwright 1440/1024/390 | 0 console error、無橫向溢出、標籤與說明皆渲染 |
| 突變測試 | `python debug_findings/mutation_check.py` | **12/12 KILLED** |
| Artefact 重生成 | `--multi`、`scripts/run_arena_benchmark.py` | 數字與舊 artefact 一致，僅標題／名稱改英文 |

> 修復歷程中曾出現 1 次紅燈：`test_web_xss.py::TestClipboardFailureHandling`（因為它斷言被翻成英文的中文字面值）。
> 已改為結構化斷言（見第二節），重跑全綠。

## 六、殘留風險（未解決，僅揭露）

1. 參數化模擬器的違規率與真實模型不符（0/94）——已在 LIMITATIONS §2.3 以負結果揭露，未修復。
2. 引擎在 Hard-Bargainer 上仍略遜固定 5% 基準（8.8% vs 9.2%），未調校。
3. `logrolling.py` 的交換方程式公平性問題（供應商可能淨損）仍記錄未修。
4. 多供應商首輪先驗不隨各廠報價分化（示範路徑），已揭露。
5. Stage-2 語意層實測 15–45 秒，不適合互動路徑（預設關閉）。
6. 儀表板 mandate 的 lead/deposit/tooling 仍為「談判目標」，未被求解器強制（現已以 TARGET 標籤與說明揭露）。
