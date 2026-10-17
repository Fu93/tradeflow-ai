# TradeFlow AI 深度除錯與優化 — 最終報告（2026-10-17 提交日）

> 工作方式：5 路子代理並行審查（引擎/後端、運行時驗證、性能、前端/擴充、文檔一致性）+ 主會話實機驗證與修復收尾。
> Git：修復前快照 `51f2344` → 修復提交 `96360e4`（14 檔，+518/−178）。全部可回滾。

## 一、結論：可以提交

| 驗證項 | 修復前 | 修復後 |
|---|---|---|
| 全套 pytest | 332 passed + 68 subtests（12:41） | **332 passed + 68 subtests（12:46），零回歸** |
| 服務啟動 | — | 2.7s，lifespan 預熱正常 |
| 端點煙霧 | 全 200 | 全 200；SSE 首字節 0.0026s；拍賣 3.7s / 28.8M rollouts |
| 求解快取 | 每輪重解 ~2.5s | 同輸入命中 **0.000s** |
| 演示資料庫 | 207 條測試殘留會話 | 已清空（全量備份可回滾） |

## 二、已修復清單

### 擴充 / 前端（演示直接出醜級）
| 級別 | 問題 | 位置 |
|---|---|---|
| P0-1 | 中文店名 → session_id 含中文 → 後端 422，演示第一步即死 | `extension/content.js`（sanitizeSessionId + FNV-1a 雜湊 fallback） |
| P0-2 | 讀取後端不存在的 `turn.supplier_offer` → 3 張決策卡永不渲染 | `extension/content.js`（改讀真實契約欄位） |
| P0-3 | captcha 熔斷後復原分支不可達 → 永久凍結 | `extension/content.js`（控制流重構） |
| P0-4 | mock_chat 需「允許存取檔案網址」無說明 → HUD 不注入 | `extension/README.md` |
| P1-1/2 | sidepanel 讀錯欄位層級（顯示 undefined/¥0.00）+ 待命視圖捏造 4.8% 等數字 | `extension/sidepanel.js`（spec/history 映射、缺值一律 `--`） |
| P1-3 | Chart.js CDN 失敗中止整個啟動鏈 → 永遠 Connecting | `src/web/static/js/app.js`（順序調整 + try/catch + typeof 防禦） |
| P1-4 | 手機指令 `ignored` 顯示紅字錯誤 | sidepanel 改中性 amber 提示 |

### 後端 / 引擎
| 級別 | 問題 | 位置 |
|---|---|---|
| P1 | v2_belief 用原始先驗而非累積後驗 → deal_probability 每輪重置、評審對照即穿幫 | `src/app/main.py` ×2 處；實證 0.1132 → 0.3944 正常推進 |
| P1 | 裸吞 logrolling 全部異常（含護欄違規）＝假成功零日誌 | `main.py` → 只接 `ASTMandateViolationError` + warning |
| P1 | 「can you do 5000 units at 13.8」把數量 5000 當報價（實測解析成 500.0：`\d+` 負向斷言回溯縮水） | `src/engine/bilingual.py`（`(?!\d)` 防回溯 + 量詞排除 + 移除範圍介系詞 `to`） |
| P2 | `suppliers=[]` 靜默 fallback / rounds_quotes 列長不符 → numpy 500 | `main.py` 明確 422 |
| P2 | 同 ID 重建會話無聲清空歷史 | `create_session` 記 RECREATED 警告 |

### 性能（演示有感、低風險）
1. `/round` 求解結果有界 LRU 快取（全輸入鍵，固定 seed 確定性可重現）——重複演示命中 0.000s。
2. FastAPI lifespan 啟動預熱 ONNX/vocab/regex——首請求不再獨吃載入。
3. benchmark 兩腳本改暖機+5 次取 median±std（既有輸出鍵全保留）。
4. **刻意不做**（風險>收益）：float32 重構、SQLite 連線池、雙寫合併、CuPy 路徑、MI300X 實測。

### 文檔一致性（20 條審計全數處理）
- SUBMISSION_DRAFT §1 新舊實驗數字混用（30 vs 100 場）、LIMITATIONS 內部兩套延遲/違規數、DEMO_SCRIPT「46 秒」無出處 + MCTS 錯名 → Expectimax、PITCH 價格軌跡 13.12/14.23 衝突、tagline 155→139 字元、$2,500→$2,685 匯率錨、Evolus agent 狀態三文檔過期等（F1–F20，詳見 `debug_findings/04_docs_consistency.md` 與 `07_fix_log_docs.md`）。
- 測試數 326 → **332**（README×3、SUBMIT、SUBMISSION_DRAFT；LIMITATIONS 的 0.326 為機率值未動）。

### 演示資料庫清場
- 清除 207 條測試殘留會話（588 輪次、208 JSON）；全量備份於 `debug_findings/_sessions_backup/`（可完整回滾）。戰情屏現在是乾淨的空狀態，演示時新建會話即可。

### 寫入守門員誤判行的授權改寫（你已批准、零行為變化）
- `SANDBOX_DEMO_TOKEN` → `SANDBOX_DEMO_AUTH = os.getenv("TRADEFLOW_SANDBOX_DEMO_TOKEN", "SANDBOX-DEMO-TOKEN")`；`webhook_token_header` → `webhook_auth_header`；兩個門禁函數參數名統一 `supplied`；JS 端 key 常數改 `localStorage` 讀取（未設定時行為與原空字串完全相同）。
- **PEP 562 模組級 `__getattr__` 相容別名**保留舊名讀取 → 既有測試零修改（已驗證 import OK）。

## 三、重要發現（避免你重查）

1. **熔斷功能正常**：實測「加個微信私下聊」正確觸發 `CIRCUIT_TRIPPED_ESCALATED`。先前實測不觸發是 PowerShell 探測腳本把中文編碼成 `???` 所致（服務端收到問號，regex 自然不中）。
2. **延遲口徑**：solve() 20K ≈1.7s 為主要成本（`bench.json` 為準）；快取只消除「重複演示」的重解，單輪端到端 ~3.3s 屬既有行為（LIMITATIONS §6.1 已統一口徑）。
3. **後端零 P0**：已知崩潰路徑在 `49e96dd` 後已被 schema 驗證/AST 護欄/500 handler 擋住，本輪審查再確認。
4. pytest 在本除錯環境需 `sys.path.insert` 包裝（AutoClaw 附帶 Python 為 safe_path 模式）；**你自己的機器不受影響**，`python -m pytest tests -q` 照常可用。

## 四、提交日你還需親自完成（按阻塞排序）

1. **Demo 影片 3–5 分鐘**（現有僅 ~15s 無聲）——照修訂後的 `DEMO_SCRIPT.md` 重錄（旁白數字/演算法名已修正，Scene 4 改演示 agent 而非不存在的 workflow）；記得 Human-in-the-loop 審批畫面入鏡。
2. **輪換兩把 Evolus API key**（SUBMIT.md 明列：開發期間明文共用過）。
3. **pitch.pptx 同步**：跑一次 `python scripts/make_pitch.py` 確保與修訂後 PITCH.md 一致（本輪無法驗證 pptx 內容）。
4. **`python scripts/refresh_market_data.py`** 在有網路的機器跑一次（此環境網路逾時；market_anchor 仍為 10-14 版）。
5. 平台表單：tagline（已 ≤140）、標籤、封面圖（`results/cover_1280x720.png` 已存在且尺寸正確）。
6. **可放棄不扣誠信分**：MI300X 實測、Evolus workflow（LIMITATIONS 已自述為補強項）。

## 五、工作區附註

- `debug_findings/`（未入 git）：01–07 審查與修復日誌、驗證腳本（verify_fixes.py 等）、會話備份。提交後可整個刪除或自行歸檔。
- 煙霧輸出中的供應商名亂碼為 PowerShell 顯示層編碼現象，伺服器數據與瀏覽器渲染正常。
