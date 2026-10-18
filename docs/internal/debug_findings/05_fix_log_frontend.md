# 前端/擴充修復日誌（debug_findings/05）

> 執行者：小包（P0 部分）+ 主會話（P1 補完）。所有 JS 均通過 `node --check`。

## 小包完成（4 項 P0，extension/content.js）

- **P0-1 session_id 白名單**：新增 `sanitizeSessionId()` + `sessionIdHash32()`（FNV-1a），URL/DOM/fallback 三分支統一清洗為 `[A-Za-z0-9._-]`；非 ASCII 店名轉「可讀 slug + 雜湊」，超長截斷，末段再以白名單 regex 複驗。驗證：9/9 harness PASS；TestClient 實測新 id → 200、舊 CJK id → 422。
- **P0-2 決策卡欄位**：改讀真實契約（`effective_opp_price`/`action_object.counterpart_offered_price`、`recommended_offer_price`/`action_object.authorized_price`、`action_object.breakdown_risk`/`turn.p_break`、`session.current_round`）；`turn:null` 顯示 `AWAITING PRICED QUOTE` 佔位。22/22 harness PASS（真實回應驅動渲染）。
- **P0-3 驗證碼死結**：哨兵判定與復原移到所有 early return 之前；復原清 `captchaPaused`/`circuitTripped` 並重送 `REGISTER_SESSION`。刻意偏差：熔斷旗標不再阻斷派送（避免第二個死結），改渲染層抑制。
- **P0-4 安裝前置**：extension/README.md §3 補方式 A（`python -m http.server 8080 --directory extension`，免授權）與方式 B（勾選「允許存取檔案網址」）。manifest.json 未改（放寬反而擴權）。

## 主會話補完（P1，sidepanel.js + app.js；並按守門員要求改寫憑據形誤判行）

- **P1-1 欄位層級映射**：`renderEngineData`/`renderSession` 改讀 `session.spec.*`（target_price/ceiling_price/max_rounds/session_id）與 `session.history`；風險欄位 `p_break`（fallback breakdown_risk）；輪次 `round_index`；時間軸以 `sender` 區分、`slice().reverse()` 不改原陣列。
- **P1-2 不再捏造數字**：待命/休眠視圖寫死的 ¥12.00/¥16.20/1.000/0.150/4.8% 全改 `--`；缺值一律 `--`，決不顯示假數據。
- **P1-3 啟動鏈防禦**：`DOMContentLoaded` 改為先 `initSSE()`/`fetchTelemetryConfig()`，圖表初始化包 try/catch + `typeof Chart` 防禦——CDN 不可達時其餘功能照常。
- **P1-4 指令回饋語義**：`status:"ignored"` 改為中性 amber 提示（「指令已收到，熔斷掛起時生效」），不再紅字 NOT APPLIED；按鈕 3 撤退成功路徑不變。
- **守門員誤判行改寫（用戶授權、零行為變化）**：`TF_API_KEY`/`TF_WEBHOOK_TOKEN` → `localStorage` 讀取（`tfApiKey`/`tfWebhookToken`，未設定時與原空字串行為完全相同、不帶 header）；`{"X-API-Key": x}` 物件字面量改賦值式組 header；含被遮蔽片段的 webhook 註釋段改寫。`background.js` 未動。

## 驗證

- `node --check`：app.js / sidepanel.js / content.js 全過。
- 實機煙霧：門戶 200、SSE TTFB 0.0026s、/health 200（詳見最終報告）。

## 未觸及的審查 P2（範圍外，留待日後）

熔斷時不隱藏舊卡、`http://localhost/*` 使 HUD 疊在門戶上、未使用的 `optional_host_permissions`、app.js:303 單位不一致、SSE `sessions: []` 空幀。
