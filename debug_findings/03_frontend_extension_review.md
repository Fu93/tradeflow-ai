# 03 — 前端 / 擴充（瀏覽器側）審查報告

- 審查範圍：`extension/`（manifest.json、background.js、content.js、sidepanel.html/js）、`src/web/`（templates/index.html、static/js/app.js、static/css/theme.css、routes_web.py）對 `src/app/main.py` 的 API 契約。
- 審查性質：**唯讀**。未修改任何被審查檔案；所有資料寫入導向 `%TEMP%`（`TRADEFLOW_DB_PATH` / `TRADEFLOW_STORE_DIR` / `TRADEFLOW_ENV_PATH`）。
- 驗證方法（證據分級）：
  1. `node --check` × 4 檔（語法）— 實測。
  2. PowerShell 對 `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js` 下載後計算 SHA-384 與 `index.html` 的 `integrity` 比對 — 實測。
  3. `fastapi.testclient.TestClient` 隔離實跑（sandbox 模式、資料導向暫存目錄）：`/api/v1/sessions`、`/api/v1/sessions/{id}/round`（copilot + autopilot、ASCII + CJK session_id）、`/api/v1/stream/session`（SSE 首幀）、`/api/v1/sessions`（POST preset body ×2）、`/webhook/mobile-command`、`/api/v1/multi-auction/run`（無 body）、`/api/v1/config/telemetry`、`/api/v1/telemetry/test-push` — **實測**。
  4. 真實 Chrome 未載入 → 擴充在瀏覽器端的注入/渲染行為為**讀碼推斷**，已於各條標明。
- 結論先行：**擴充的「報價 → 決策卡」主線在演示中不會成立**（兩個獨立 P0 各自都會讓它失敗），另有 1 個演示前置條件（file://）與 1 個狀態機死結；Web Portal 本體契約大致正確，主要風險是外部 CDN 依賴與遙控按鈕語義。

---

## P0（載入即錯 / 按鈕無反應 / 主線功能不成立）

### P0-1 `extension/content.js:117` → `:119` — 產生的 session_id 含中文，後端一律 422，整個 /round 流程第一步就死
- 問題：`extractSessionContext()` 的 DOM 分支保留 CJK：`const cleanId = rawName.replace(/[^a-zA-Z0-9_\u4e00-\u9fa5-]/g, "_")`（:117），再組出 `sessionId: \`sess_${cleanId}\``（:119）。後端 `_validate_session_id_path`（`src/app/main.py:246`，呼叫點 `src/app/main.py:1118`）要求 `^[A-Za-z0-9._-]{1,128}$`。
- 實測：`POST /api/v1/sessions/sess_浙江頂尖雨具製造實業有限公司/round` → **HTTP 422**，`detail = "Invalid session_id in path: must match ^[A-Za-z0-9._-]{1,128}$ ..."`。
- 影響面：`extension/mock_chat.html` 的 `.chat-header .supplier-name`（「浙江頂尖雨具製造實業有限公司…」）會命中 DOM 分支第一順位 selector；真實 1688/阿里中文店名同理。擴充送出的每一輪都失敗，background 只在面板顯示 `Engine HTTP 422`（`extension/background.js:163`）。
- 建議修法：DOM 分支改與 URL 分支（:95）一致只留 ASCII，或對非 ASCII 做雜湊轉寫（例如 `sess_` + FNV-1a hex），例：`const cleanId = rawName.replace(/[^a-zA-Z0-9_-]/g, "_") || \`h${hash32(rawName)}\``。不要放寬後端白名單（它同時是路徑穿越防線）。

### P0-2 `extension/content.js:863`（關聯 :865、:875）— 讀取後端從不存在的欄位，3 張決策卡永遠不渲染
- 問題：`const offer = data.turn?.supplier_offer;`。後端 copilot（預設）分支回 `"turn": None`（`src/app/main.py:1551-1563`）；autopilot 分支的 `turn` 是 `NegotiationTurn`（`src/session/schema.py:52-64`），欄位是 `round_index / sender / raw_text / parsed_price / p_break / posterior_mean / posterior_std`，**沒有** `supplier_offer`、`buyer_counter_offer`、`breakdown_risk`、`round_number`。
- 實測（同一支 TestClient）：
  - `mode=copilot` → 200，`turn = null`。
  - `mode=autopilot` → 200，`turn` keys = `[logrolling_proposal, metadata, p_break, parsed_price, posterior_mean, posterior_std, raw_text, round_index, sender, timestamp]`。
- 影響：`content.js:866-872` 命中 `offer == null` 即 `return` → 決策卡（產品主視覺）在任何模式下都不會出現，HUD 長期停在 `ENGINE SOLVING...` / 只跳狀態文字。同理 `:875` 的 `data.turn?.breakdown_risk >= 0.15` 永遠不成立（熔斷只剩 `data.session.state` 那半邊有效）；`:881` 顯示的風險值永遠是 `—`。
- 建議修法：改用真實欄位——供應商報價 `data.effective_opp_price ?? data.turn?.parsed_price`；建議還盤價 `data.recommended_offer_price`（已存在，實測 13.52）；風險 `data.action_object?.breakdown_risk`（`TacticalActionObject.breakdown_risk`，`src/engine/bilingual.py:688`）或由 `bayesian_belief_state.deal_probability` 反推；輪次 `data.turn?.round_index`。並把 `turn == null` 視為 copilot 的正常狀態，改以 `action_object` / `reply_text` 渲染卡片。

### P0-3 `extension/content.js:789` vs `:807` — 驗證碼哨兵觸發後永久凍結，解除分支不可達
- 問題：`:789` `if (!overlayEl || !overlayEl._state.active || circuitTripped) return;` 在所有邏輯之前；而驗證碼觸發時 `circuitTripped = true`（:795），負責復原的 `else if (captchaPaused) { … circuitTripped = false; }`（:807-811）位於該 early return **之後** → 永遠執行不到。
- 影響：只要頁面出現一次驗證碼（`mock_chat.html` 的「模擬阿里風控滑塊」按鈕就是為了演示它），該頁之後所有新報價都被靜默忽略、狀態釘在 `CAPTCHA DETECTED`，只能重新載入頁面。演示腳本若照展示按鈕走，後半段擴充即「按鈕無反應」。
- 建議修法：把驗證碼判定與復原搬到 early return 之前，或把條件改為 `(!active) || (circuitTripped && !captchaPaused)`；復原時一併重送 `REGISTER_SESSION` 以回復 session 綁定。

### P0-4 `extension/manifest.json:42` — `file://` 沙盒頁需要手動開啟「允許存取檔案網址」，否則 HUD 完全不注入
- 問題：`content_scripts.matches` 以 `file:///*mock_chat.html*` 覆蓋沙盒頁，但 Chrome **預設**不允許擴充在 `file://` 執行 content script（須在 `chrome://extensions` → 擴充詳細資料 → 勾「允許存取檔案網址」）。`extension/README.md:43` 的指引只寫「用 Chrome 開啟該檔案」。
- 嚴重度：P0（演示前置條件，非程式缺陷；若沿用 README 步驟，會出現「載入了擴充但畫面上什麼都沒有」）。
- 建議修法：(a) 演示前勾選該開關並寫進 demo checklist；或 (b) 以 http 提供沙盒頁（`python -m http.server 8080` 或新增 FastAPI 路由，如 `docs/archive/TradeFlow_Final_Convergence_Plan.md:110` 早已規劃的 `/sandbox/chat`），此時 `http://localhost/*`（manifest:43）已涵蓋。

---

## P1（數據不顯示 / 顯示錯誤 / 事件不更新）

### P1-1 `extension/sidepanel.js:256-261、280-281、299-303` — 側欄欄位名與 `NegotiationSession` 不符（顯示 `undefined` / ¥0.00）
- 問題：`renderSession()` 讀 `session.session_id / target_price / walk_away_ceiling / current_posterior_mean / current_posterior_std / turns / max_rounds`，並在時間軸讀 `t.round_number / t.buyer_counter_offer / t.supplier_offer / t.breakdown_risk`；後端 `GET /api/v1/sessions/{id}` 回的是 `NegotiationSession`（`src/session/schema.py:96-107`，`spec` 巢狀）。
- 實測：`session` keys = `[created_at, current_round, history, latest_opponent_offer, latest_our_offer, silence_until_ts, spec, state, trip_reason, updated_at, version]`；`has top-level target_price = False`、`has turns = False`。
- 影響：Session ID 顯示字面 `undefined`，Target/Ceiling 顯示 `¥0.00`，`Round 0 / 5`，時間軸空白——評審一打開側欄就看到壞資料。
- 建議修法：改讀 `session.spec.session_id / session.spec.target_price / session.spec.ceiling_price / session.spec.max_rounds / session.history`；後驗取 `history.at(-1)?.posterior_mean|posterior_std`；turn 用 `round_index / parsed_price / p_break`。

### P1-2 `extension/sidepanel.js:225-226、231、238、285` — 同一問題發生在 `/round` 推播路徑，且 fallback 是捏造常數
- 問題：`renderEngineData()` 讀 `data.session.target_price / walk_away_ceiling`（實際在 `session.spec.*`）與 `turn.breakdown_risk / turn.round_number`（不存在）；`tfNum(..., 12.0/16.2/0.048/1)` 的 fallback 直接寫死。
- 實測連鎖：copilot 輪次 `turn = null` → `const turn = data.turn || {}` → 風險必定顯示 **4.8%**、輪次必定 **Round 1**、目標價必定 **¥12.00 / ¥16.20**（與實際 mandate 無關）。
- 建議修法：改讀 `data.session.spec.*`、`data.action_object.breakdown_risk`、`data.turn?.round_index`；缺值顯示 `—`。此專案多處明文禁止捏造數字，這裡應比照處理。

### P1-3 `src/web/static/js/app.js:49`（+ `src/web/templates/index.html:20`）— Chart.js CDN 失敗會連帶讓 SSE 與 session 清單永不啟動
- 問題：`DOMContentLoaded` 內依序 `initBeliefChart(); initSSE(); fetchTelemetryConfig();`（app.js:48-52），而 `initBeliefChart()` 於 `:61` 直接 `new Chart(ctx, {...})`。Chart.js 來自 CDN（index.html:20-23）。若 CDN 不可達（離線會場、網路政策、SRI 不符），`ReferenceError: Chart is not defined` 會在該 listener 內拋出 → **同一個 listener 後續的 `initSSE()` / `fetchTelemetryConfig()` 不會執行**。
- 影響：門戶右上永遠 `Connecting...`、Session 清單永遠「No sessions」、遙測設定永遠空白 → 近似白屏；單獨刷新無效。
- 佐證（已排除 SRI 疑慮）：實測 CDN 檔案的 SHA-384 = `sha384-e6nUZLBkQ86NJ6TVVKAeSaK8jWa3NhkYWZFomE39AvDbQWeie9PlQqM3pmYW5d1g`，與 index.html:21 完全相同 → 連線正常時可載入，這是純外部依賴風險。
- 建議修法：(a) 把 `initSSE()` 提到最前、並以 `if (typeof Chart === "undefined") { /* 顯示「圖表離線」*/ } else { initBeliefChart(); }` 包住；(b) 更佳：將 `chart.umd.min.js` 放入 `src/web/static/vendor/` 本地託管（同時把 CSP `script-src` 的 jsdelivr 去掉）。

### P1-4 `src/web/static/js/app.js:415`（sendCommand）＋ `src/app/main.py:627-645` — 遙控按鈕 1/2 在常見狀態下回 `ignored`，UI 顯示「NOT APPLIED」
- 問題：Portal 的 Remote Command 1/2 走 `/webhook/mobile-command`。當會話停在 `AWAITING_HUMAN_DECISION`（copilot 每輪的常態）時，後端不視為可套用狀態 → 回 HTTP 200 但 `status="ignored"`，前端據此顯示紅字 `[NOT APPLIED] …`。
- 實測：`POST /webhook/mobile-command {command:"1", session_id:"SMOKE_ASCII_01"}`（該會話為 AWAITING_HUMAN_DECISION）→ 200，`status="ignored"`，detail `"Session is in state AWAITING_HUMAN_DECISION; command produced a decision card without a state transition (no pending authorization was open)."`（按鈕 3 WALK_AWAY 正常回 `success`。）
- 影響：演示時「Concede / Hold」像壞掉；實際上應走 `/approve`。
- 建議修法：(a) 前端在該狀態改呼叫 `POST /api/v1/sessions/{id}/approve`（`action=approve|modify`）；或 (b) 後端把 `AWAITING_HUMAN_DECISION` 納入可套用集合；或 (c) 至少改文案為「已產生決策卡（未變更狀態）」以免被讀成失敗。

---

## P2（演示擦傷 / 整潔度）

1. `src/web/static/js/app.js:303` — `t.posterior_mean ? t.posterior_mean * target : target`：fallback 少了乘數，圖上會混入單位不一致的點（¥12 vs ¥12.96）。
2. `src/web/routes_web.py:102-107` — SSE 每 300 秒壽命到期會送一幀 `sessions: []` + `error`；`app.js:145` 無條件 `renderSessions([])` → 清單瞬間變「No sessions」，約 3 秒後 EventSource 重連才恢復（演示中若剛好撞上會像當機）。建議前端忽略 `error && sessions.length===0` 的幀。
3. `extension/manifest.json:43-44` — `content_scripts` 含 `http://localhost/*`，擴充也會注入 TradeFlow 自己的門戶（`http://localhost:8000`），右下角 HUD 會壓在 Telemetry 面板上。建議收斂為明確埠＋在 content.js 排除 `/`。
4. `extension/manifest.json:35-45` — 未設 `all_frames: true`；若 1688/旺旺把聊天框放在 iframe（例如 `*.alicdn.com` 域，host_permissions 亦未涵蓋），observer 不會注入。**推斷**（真實站台未實測）。
5. `extension/manifest.json:23-25` — `optional_host_permissions: ["*://*/*"]` 全無程式碼使用（無任何 `chrome.permissions.request`）；建議移除，避免評審追問權限最小化。
6. 憑證常數皆為空字串（`app.js:15`、`background.js:17`、`sidepanel.js:20`）：本次以 key 名檢查 `.env`，無 `TRADEFLOW_API_KEY` / `TRADEFLOW_WEBHOOK_TOKEN` / `TRADEFLOW_ENV` → sandbox fail-open，Portal 不會 401。若演示當天臨時設置了 key，所有 `/api/v1` 呼叫會變 401（提醒，非現況缺陷）。
7. `test-push` 文案：實測 200 / 1.11 秒、`results = {"whatsapp":false,"telegram":false,"pushplus":false}`（未配置憑證）→ Portal 顯示這一串 `false` 容易被讀成「壞了」。建議 UI 標註「未配置憑證＝通道未驗證，非發送失敗」。

---

## 已驗證「無問題」清單（避免重複排查）

- **JS 語法**：`node --check` 對 `background.js`、`content.js`、`sidepanel.js`、`app.js` 全部 exit 0。讀檔工具輸出中的標頭金鑰行曾被顯示層遮蔽成 `[redacted]`，很容易被誤判為語法錯誤；以正則比對原始檔案後確認該行是合法 JS（把 `TF_API_KEY` 變數當作 `X-API-Key` 這個標頭鍵的取值），**不是**缺陷。
- **manifest v3 / 權限**：`host_permissions`（manifest:13-22）覆蓋 1688 / alibaba / globalsources / made-in-china / web.whatsapp.com / wx.qq.com / localhost:8000 / 127.0.0.1:8000；`permissions` 與程式碼用法相符（sidePanel+setPanelBehavior、storage.session、tabs、clipboardWrite）；`extension_pages` CSP 語法合法且允許連 localhost:8000。
- **SSE 契約**：後端 `/api/v1/stream/session` 使用**無名** `data:` 幀，前端用 `onmessage`（app.js:142）→ 命名事件不會對不上。實測首幀 `{"ts":…,"sessions":[{"session_id":"SMOKE_ASCII_02","state":"AUTOPILOT_CRUISING","product":…,"target":12.0,"ceiling":16.2,"rounds":2,"last_p_break":0.0303,"last_posterior":1.1769}]}`，欄位與 `renderSessions` / `selectSession` 完全對應。
- **Portal ↔ 後端路徑與欄位一致**：`/api/v1/sessions`（GET/POST）、`/api/v1/sessions/{id}`、`/api/v1/config/telemetry`（GET/POST）、`/api/v1/telemetry/test-push`、`/api/v1/multi-auction/run`、`/webhook/mobile-command` 全部存在且欄位名吻合（擴充與 Portal 皆同）。`/api/v1/sessions` 只吃 `spec.*` 巢狀（refreshSessions 的 mapping 正確）。
- **/multi-auction/run**：無 body 的 POST 可行（實測 200 / 3.51 秒），回傳含 `success / suppliers[{name,profile}] / winning_supplier / winning_price / total_savings_* / total_rollouts / total_compute_seconds / backend / trace`，`trace[-1]` 具 `quotes / tactics / effective_ceilings_cny / p_break_array` → `app.js:runMultiAuction` 讀取正確。
- **Portal 建會話**：門戶的 preset body（無 `supplier_id` / `supplier_channel`）→ 200；重覆提交同 id 亦 200（`INSERT OR REPLACE`）。
- **CORS**：`allow_origin_regex` 允許 `chrome-extension://*` 與 `http(s)://localhost|127.0.0.1` → 擴充/側欄 fetch 不會被 CORS 擋。
- **靜態資源**：`/static/css/theme.css`、`/static/js/app.js` 存在且 `app.mount("/static", …)`（main.py:1676-1679）在 include_router 之前；`theme.css` 具 `.hidden / .empty-state / .loader / .cmd-result`。
- **Chart.js SRI**：實測吻合（見 P1-3）。
- **首次載入 / 空資料**：`renderSessions([])` 走靜態空狀態文案（app.js:160）；`selectSession` 對 `last_p_break = null` 走 0.0 分支；皆不會拋錯。

---

## 統計

| 嚴重度 | 條數 | 歸屬 |
|---|---|---|
| P0 | 4 | content.js 3（session_id 白名單、決策卡欄位、captcha 死結）、manifest/安裝前置 1（file://） |
| P1 | 4 | 側欄欄位契約 2、門戶 CDN 依賴 1、遙控按鈕語義 1 |
| P2 | 7 | 見上 |

- 演示主線風險排序（建議修復順序）：**P0-1 → P0-2 → P0-4（勾開關/改 http）→ P0-3 → P1-1/P1-2 → P1-3 → P1-4**。
- 未涵蓋：真實 Chrome 載入擴充、真實 1688/旺旺 DOM 注入、SRI/CSP 在瀏覽器端的實際裁決（本次以靜態與 TestClient 驗證取代）。
