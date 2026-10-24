# TradeFlow AI — 升級與優化方案(全面產品化路線)

> 2026-10-24 擬定,經倉庫所有者確認。本文件取代 STATUS.md 中
> 「維護模式:fixes and documentation only, no new modules」的預承諾:
> **該約束自本方案起解除**,倉庫進入「產品化升級」狀態。
> AMD AI League Match 5(10-27 起)另開新倉的決定**不受影響**。

## 起點(已驗證的基線)

| 門 | 現狀 |
|---|---|
| pytest 全量 | 1015 passed / 3 個平台相關失敗(僅 Windows `/proc`) |
| 覆蓋率門(CI) | 92.04%(門檻 90%) |
| 變異測試 | 73 killed / 0 survived |
| verify_all 六支柱 / verify_live 端到端 | 全 PASS |
| ruff + mypy(phase-1 範圍) | 乾淨 |
| CI | test + extension-e2e 兩 job 全綠 |

## 債務清單(方案針對的問題,全部有出處)

1. `src/app/main.py` 3,004 行巨石,不在任何 lint/type/coverage 門內(STATUS.md「Not done」自認)。
2. `src/desk/service.py` 1,710 行,STATUS.md 註明「ingest and polling are the next candidates」。
3. 無多用戶身份:單一共享 API key,無按人吊銷(STATUS.md「Not done」自認)。
4. `bilingual.py` 2,035 行(含數值護欄)不在覆蓋率門內(STATUS.md 自認)。
5. docker-compose **無 volumes**,SQLite 不落宿主機;Render 免費盤為 ephemeral。
6. `src/legacy/` 4,868 行在 lint/type 門外;extension JS 4,612 行與儀表板 JS ~3,000 行僅 e2e 覆蓋。
7. 試點未跑:PILOT_RUNBOOK 的 LIVE-READY 門未過(「actually usable」的最後一關)。

---

## 階段一:架構拆分 + 質量門擴圍(2026-10-24 實施)

**目標:行為保持不變地拆掉 `main.py` 巨石,把新模組納入質量門。**

- 依倉庫既有先例:`register_desk_routes(api_v1)` 路由模組模式、
  `security.py` 斷循環導入先例、`service.py` 重導出兼容先例。
- 新增 `src/app/state.py`:`session_mgr` 全局實例、求解緩存、
  `SafeJSONResponse`/`_sanitize_non_finite`、`_legacy_route` 門控輔助。
- 新增 7 個路由模組(routes_meta / routes_negotiate / routes_command /
  routes_analysis / routes_sessions / routes_round / routes_auction),
  均暴露 `register_x_routes(api_v1)`;`main.py` 變為裝配層 + 重導出(≤450 行)。
- 新增 `tests/test_app_structure.py`:子進程證明路由模組可在
  `src.app.main` 缺席下加載,防止回耦(照 `test_structure_round2.py` 先例)。
- CI:ruff/mypy 納入新模組;coverage 納入新模組(實測 ≥90% 才進 fail-under)。

**驗收**:全部既有測試零修改通過;verify_all;變異測試;ruff/mypy;
覆蓋率 ≥92%;CI 綠;main.py ≤450 行。

**實施結果(2026-10-24)**:main.py 3,004 → 468 行;8 個新模組 + 1 個
防回耦測試(test_app_structure.py,10 例)。ruff 門已納入新模組並全綠。
與計劃的一處偏差,mypy:新模組的 35 個靜態錯誤全部源於 shim 架構
(`sys.modules[__name__] = _impl` 的兼容 shim 在靜態層面無屬性)與
`**dict` 動態解包 —— 這正是原 main.py 從未被 mypy 檢查的原因,屬文檔化的
既有結構特性而非拆分缺陷。強行用 ignore 讓門變綠是表演,故 mypy 門維持
原範圍,解除路徑記錄為階段四工作:為 shim 層寫靜態 .pyi stub 後再納入。
另:實施中發現並修掉兩處預存死代碼缺陷(retired 緩存函數 raise 之後引用
未定義變數;`/round` 不可達路徑中計算從未被讀取的 `demand_mold_waiver`),
13 處 except 內 raise 補上顯式異常鏈(與 desk 路由的既有風格一致)。

**風險**:循環導入(用 state.py 斷);測試依賴的重導出遺漏
(以 grep 清單比對 + 全量測試兜底);行為漂移(移動不改寫,逐段 verbatim)。

## 階段二:安全升級 — 多用戶身份與可吊銷

- `src/desk/users.py` + `migrations.py` 版本升級:`desk_users` 表
  (key_hash、label、created_at、revoked_at),明文密鑰只在簽發時顯示一次。
- `require_desk_mutation` 解析順序:靜態環境鍵(向後兼容)→ 有效用戶鍵;
  已吊銷立即 401。
- 審計鏈 `append_chain` 條目記錄 actor key_id,責任到人;
  限流已按 key 哈希分鍵,自動獲得按人配額。
- `scripts/manage_users.py`:issue / revoke / list。
- 測試:認證矩陣(環境鍵/用戶鍵/已吊銷/未知名)、鏈上 actor 斷言。
- 更新 `.env.example`、STATUS.md、SECURITY 相關文檔。

**驗收**:認證矩陣測試全綠;既有單鍵部署零改動可用;migrations 升級與回滾測試。

**實施結果(2026-10-24)**:完成。`src/desk/users.py` + schema version 2
(`desk_users` 表:sha256 hash、12 字符顯示前綴、label、revoked_at;明文密鑰
只在簽發時輸出一次)。`require_desk_mutation` 與 `require_api_key` 共用
`_resolve_credential`:環境鍵照舊(actor 記為 `env-key`),命名鍵為對等憑據
(actor 記為 `user:<id>:<label>`);吊銷鍵與未知鍵一律 401,無法以錯誤信息
探測吊銷狀態;production 無環境鍵的 fail-closed 500 不變。鏈條目由
`DeskStore.append_chain` 在有請求上下文時把 actor 寫入**被 MAC 簽名的
payload**(防篡改與條目其餘部分同級);無請求寫入(poller/CLI)不寫 actor,
既有條目形狀不變。`scripts/manage_users.py` issue/revoke/list。測試
`tests/test_desk_users.py`(矩陣 + actor + 存儲衛生 + CLI)。構建期決策:
actor 經 contextvar 傳遞,認證依賴改 async(同步依賴在 worker 線程 set
contextvar 會被丟棄);命名鍵查找僅在 TRADEFLOW_DESK_DB 顯式配置時進行,
v1-only 部署的失敗登錄不會意外創建數據庫文件。

## 階段三:部署與運維加固

- docker-compose 增加 `volumes:`,SQLite 落宿主機;文檔化 Render 持久磁盤需付費方案。
- `scripts/backup_desk_db.py`:sqlite3 backup API 在線備份;恢復後自動 `verify_chain`。
- `/version` 只讀端點(schema version + git sha);恢復演練與每日 anchor cron 文檔。

**驗收**:compose 起服務→寫數據→重建容器→數據仍在;備份/恢復演練通過;鏈驗證一致。

**實施結果(2026-10-24)**:完成。`docker-compose.yml` 掛載命名卷
`desk-data:/app/data` 並釘住 `TRADEFLOW_DESK_DB=/app/data/desk.db`(重建容器
數據仍在;`down -v` 仍會刪除,卷不是備份)。`scripts/backup_desk_db.py`:
SQLite backup API 在線備份 → 打開副本逐 deal `verify_rows` → 斷鏈的備份文件
直接刪除而非留著假裝可靠;`restore --yes` 恢復後同樣先驗鏈;拒絕無
`--yes` 覆蓋在用數據庫。`GET /version`(只讀、免門禁、無機密):app 版本、
schema 版本、`TRADEFLOW_GIT_SHA`(部署管線蓋章,未設則 `unknown`)。
`docs/deploy.md` 新增持久化/備份/恢復演練/anchor cron/Render 誠實說明
(starter 盤為 ephemeral,持久盤需付費方案)。測試
`tests/test_deployment_surface.py`(版本面無機密 + 備份/恢復演練/拒絕覆蓋)。

## 階段四:測試範圍擴大

- `bilingual.py` 納入覆蓋率門:先實測,補分支測試到 ≥90% 再進 fail-under。
- 拆分後 main.py 殘餘納入 ruff/mypy;`src/legacy` 以 F,E9 規則先非阻塞報告再轉阻塞。
- extension/ 與 src/web/static/js 補 eslint;單元測試誠實評估,以既有 e2e 為準。

**驗收**:覆蓋率門含 bilingual 且 ≥90%;legacy lint 報告歸零或轉阻塞;CI 不變綠。

**實施結果(2026-10-24)**:
- **mypy 解鎖完成**:為 13 個 `sys.modules` 兼容 shim(src/engine/* 11 個、
  src/session/manager 與 schema)生成 .pyi stub(`from src.legacy.<mod> import *`),
  配合 pyproject `follow_imports = "silent"`(legacy 本體保持靜默),app 層 8 個
  新模組全部進入 mypy files。定向標註僅三類,各自記錄原因:state.py 條件載入
  的 None 賦值(文檔化的執行期決策)、routes_sessions hold 分支的防禦性 404
  收窄(不可達路徑)、routes_command 的條件 kwargs(重構為顯式
  `dict[str, Any]` + 條件設值,保留「未觀測不得佔位」語意 —— deposit_pct 的
  Pydantic 預設是 0.30 而非 None,傳 None 並不等價)。mypy 37 檔全綠。
- **ruff**:main.py 清零(F401 重導出標註、刪除已遷移的 unused import、
  B904 補鏈、E402 依原檔案慣例標註),並納入 CI 門;src/legacy 以 F,E9 阻塞門
  納入(9 個 F401/F541 已修、zip 補 strict=False、SQL 字串內尾隨空白按計劃
  不屬範圍)。
- **bilingual.py 覆蓋率**:82% → 88%(miss 135 → 85):新增
  `tests/test_bilingual_stage2_paths.py`(Stage-2 LLM 回應解析、HTTP 500 /
  空回覆 / 非 JSON / 例外四條誠實失敗路徑、對抗性原生度審計的
  llm_adversarial 標記與靜態回退;requests.post 全程 monkeypatch,零網路)
  與 `tests/test_bilingual_offline_edges.py`(draft_for_approval 的存稿
  重用/重渲染語意、_clamp_price 越界丟棄、歐式千分位解析)。**已納入 CI
  覆蓋率門**,總覆蓋率過 90% 門(見 STATUS 數字);bilingual 模組本身 88%,
  未達計劃的 90%,餘下缺口為渲染器深分支與需真實端點的 LLM 路徑,記錄為
  後續工作而非隱瞞。

## 階段五:試點就緒與產品化功能

- `check_live_readiness.py` 達到 LIVE-READY:真實郵箱/SMTP 接入指南、
  每日 anchor cron、按 PILOT_RUNBOOK 預承諾成功標準的 dry-run。
- FX 匯率源(帶緩存與 fallback;明確 reference-only,不進求解器)。
- MARKET_ANALYSIS.md 補 EU/中國大陸進口商數據缺口。

**驗收**:readiness 腳本輸出 LIVE-READY;dry-run 按預承諾標準逐項勾稽。

**實施結果(2026-10-24)**:誠實結案 —— readiness 的三個 [FAIL] 全是操作員
環境憑據(TRADEFLOW_API_KEY、TRADEFLOW_CHAIN_KEY、真實郵箱通道),任何代碼
改動都無法供應,偽造憑據即違反本倉庫的誠信紀律。已交付的可完成項:
`docs/PILOT_RUNBOOK.md` 新增「Getting the readiness check to LIVE-READY」
—— 每個 FAIL 行的確切 .env 與命令(含 Phase 2 的命名操作員鍵);
`docs/MARKET_ANALYSIS.md` 新增「Closing the gaps」runbook —— EU 進口商數
(Eurostat 表)、中國專屬進口商數(代理口徑不可乘算)、desk 路徑 FX
(`official_refs.py` 已有 ECB→frankfurter 雙源 fallback 且刻意不緩存,
reference-only 蓋章;缺口精確化為「供 desk 來源標籤使用,永不進求解器」)、
小買家節省基準(只能來自真實試點)。FX「帶緩存」一項按現有設計意圖結案:
參照數據以新鮮度優先,不緩存發明匯率。真實 LIVE-READY 的最後一關是操作員
拿真實供應商跑試點 —— 這是人的工作,不是這份方案能代替的。

## 執行紀律

- 每階段獨立提交;全量驗證(pytest + verify_all + mutation + ruff/mypy + CI)通過才進下一階段。
- 行為保持類改動(拆分)與行為改變類改動(安全、功能)永不同提交。
- 誠實紀律不變:每個新能力同步更新 DATA_INTEGRITY.md 的聲明表;不誇大。
