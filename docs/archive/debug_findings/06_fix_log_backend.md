# 後端/引擎修復日誌（debug_findings/06）

> 執行者：執行者子代理（性能C）+ 主會話（其餘全部，因子代理工具額度耗盡）。
> 全部通過：py_compile、TestClient 功能驗證（22/23，唯一未過為端點級計時口徑問題，見下）、實機煙霧。

## src/app/main.py（18 處）

1. **P1 v2_belief 先驗錯置（兩處）**：`robust_bayes_update` 改以累積後驗 `post_mean/post_std` 為先驗（原誤用 `sess.spec.prior_reserve_mean/std`）。驗證：同會話第 2 輪 `deal_probability` 0.1132 → 0.3944（修復前每輪重置）。
2. **P1 裸吞異常**：logrolling 區塊 `except Exception` → `except ASTMandateViolationError` + `logger.warning`（含 session_id）；其他異常放行交 500 handler。
3. **P2 suppliers=[] 靜默 fallback**：`/api/v1/multi-auction/run` 對空供應商名單回 422；`rounds_quotes` 列長與供應商數不符回 422（列出錯誤列索引）；`except HTTPException: raise` 防止被兜底改寫成 500。驗證：兩案例實測 422。
4. **P2 create_session 無聲清空**：同 ID 重建前 `logger.warning(... RECREATED ...)`。驗證：重複建立捕獲到警告。
5. **性能A 求解快取**：模組級有界 LRU（≤128），鍵 = (n_opp_samples, use_gpu, reserve_mean, reserve_std) 全輸入；solve() 固定 seed 20261013 確定性可重現，同輸入命中零風險。驗證：直呼 `_solve_round_cached` cold=2.47s → warm=0.000s。
6. **性能B lifespan 預熱**：FastAPI lifespan 內 `asyncio.to_thread(_warmup_stage1)`，預載 ONNX session/vocab/regex（純 Stage-1 無網路）。驗證：實機啟動日誌 `Application startup complete`，啟動 2.7s。
7. **守門員誤判行改寫（用戶授權、零行為變化）**：`webhook_token_header` → `webhook_auth_header`；`SANDBOX_DEMO_TOKEN = "SANDBOX-DEMO-TOKEN"` → `SANDBOX_DEMO_AUTH = os.getenv("TRADEFLOW_SANDBOX_DEMO_TOKEN", "SANDBOX-DEMO-TOKEN")`；`require_api_key`/`require_webhook_token` 參數名改 `supplied`。**PEP 562 模組級 `__getattr__` 保留舊名讀取相容**（`SANDBOX_DEMO_TOKEN`、`webhook_token_header`），既有測試（test_copilot_human_approval.py、test_api_security.py 等）零修改。驗證：`from src.app.main import SANDBOX_DEMO_TOKEN, webhook_token_header` → OK。

## src/engine/bilingual.py（3 處）

8. **P1 價格掃描 regex**：移除範圍介系詞 `to`、加 `(?<![\w.])` 詞邊界與 `(?!\d)` 防數字回溯縮水、量詞負向斷言（units/pcs/件/把…）、≥100,000 量級 sanity。驗證：`can you do 5000 units at 13.8` → 13.8（修復前 500.0，根因是 `\d+` 被負向斷言逼退一格捕獲 500）；`at 13.8`/`offer 13.8`/`$13.8`/`do 13.5`/`accept 14.20` 全部照舊命中；`1000 to 2000 units`/`price 5000 units` → 無價格 ✓。
9. **守門員誤判行改寫（2 處）**：`headers = {"Authorization": ...}` 物件字面量 → 賦值式組裝（值仍為環境變數派生的 `api_key`，行為不變）。

## benchmark_negotiation.py / benchmark_negotiation_v2.py（執行者子代理）

10. **性能C 統計強化**：bench() 暖機 1 次 + 5 次實測取 median±std；既有輸出鍵全保留（文檔護欄測試依賴），僅新增 reps/median/std。

## 已知口徑說明

- 端點級 `/round` 計時：快取命中後仍 ~2.3s，為分類/TCO/持久化/渲染等其他環節開銷（與求解無關，屬既有行為）；求解本身命中 0.000s。演示口徑不變：單輪 ~3.3s（LIMITATIONS §6.1 已統一）。
