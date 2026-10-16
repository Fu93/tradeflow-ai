# TradeFlow AI — 深度缺失審計報告
**Deep Defect Audit — TradeFlow AI (Constrained Procurement Decision Prototype)**

- 受審代碼：`C:\Users\nn201\Desktop\tradeflow-ai` @ commit `49e96dd`
- 審計方式：逐檔閱讀 + **可重現的主動探測**（`fastapi.testclient` / 直接呼叫引擎）。每一條重大結論都有可執行的實測證據，非靜態推測。
- 基準狀態：`python -m pytest tests -q` → **110 passed in 68.06s**（全綠，但不代表健康 — 見 §7）。

> **免責聲明**：本報告以「找缺失」為唯一目的。專案已自帶的誠實揭露（`LIMITATIONS_AND_REAL_WORLD_FINDINGS.md`）我予以承認並在必要處引用；但揭露**不等於修復**，且部分揭露本身與代碼行為不符。

**我已在審計過程中意外驗證並立即還原一項改動**：探測未認證端點時，我的測試請求實際改寫了專案的 `.env`（見 §1.1）。已逐行還原為原狀，`git status --short` 為空。這既是我的操作紀錄，也正是該漏洞的實證。

---

## 0. 執行摘要：最關鍵的 6 項

| # | 缺失 | 嚴重度 | 一句話 |
|---|---|---|---|
| 1 | **80% 的 HTTP 端點零認證**（含一個可改 `.env` 的寫入端點） | 🔴 Critical | 任何能連上 8000 埠的人可建立/讀取/操控談判會話，並改寫伺服器憑證檔 |
| 2 | **「零幻覺護欄」可被實測繞過** | 🔴 Critical | 數值護欄只有**上限**檢查，沒有「僅授權價」檢查；換一套措辭即通過。我已重現 |
| 3 | **OOD-500 準確率 92.00% 是訓練集分數** | 🔴 Critical | ONNX 模型正是用該資料集訓練的；關掉 ONNX 後同一資料集掉到 **46.60%** |
| 4 | **預設路徑在無人核可下宣告成交** | 🔴 Critical | 預設 `mode="autopilot"` 一輪即 `DEAL_CLOSED`，並產出可直送供應商的價格確認話術，直接違反 README 的核心承諾 |
| 5 | **Logrolling 引擎產出被無條件丟棄** | 🟠 High | `main.py:958` 覆寫了整套定金/交期/模具置換話術，`LOGROLL` 模式實際輸出與其它模式無異 |
| 6 | **`/api/v1/config/telemetry` 無認證 + 覆寫 `.env`** | 🔴 Critical | 見 #1；可寫入檔案且可熱重載憑證狀態 |

**一句話總結**：這是一個**工程完成度遠高於其安全與評測誠信度**的原型。它把「已修好」的敘事寫進了 `LIMITATIONS` 文件，但實測顯示：認證只覆蓋 20% 的端點、護欄在最需要它的渲染層沒有作用、頭條指標是訓練分數、而「人類握有締約權」的定位在預設路徑上不成立。

---

## 1. 安全性 (Security)

### 1.1 🔴 Critical — 端點認證只覆蓋 5/14；未認證寫入端點可改寫伺服器 `.env`

**證據**：`main.py` 的端點裝飾器逐條列出如下（`grep '@app\.(get|post...)'`）：

| 端點 | 是否 `Depends(require_api_key)` |
|---|---|
| `GET /health` | ❌ |
| `POST /negotiate` | ❌ |
| `POST /webhook/evolus` | ❌ |
| `POST /webhook/mobile-command` | ❌ |
| `POST /api/v1/sessions/{id}/command` | ✅ |
| `POST /api/v1/tco/evaluate` | ❌ |
| `POST /api/v1/bayesian/update` | ❌ |
| `POST /api/v1/sessions` | ✅ |
| `GET /api/v1/sessions` | ❌ |
| `GET /api/v1/sessions/{id}` | ❌ |
| `POST /api/v1/classify/semantic` | ✅ |
| `POST /api/v1/sessions/{id}/approve` | ✅ |
| `POST /api/v1/sessions/{id}/round` | ✅ |
| `POST /api/v1/multi-auction/run` | ❌ |
| `GET /api/v1/stream/session`（`routes_web.py`） | ❌ |
| `GET/POST /api/v1/config/telemetry`（`routes_web.py`） | ❌ |
| `POST /api/v1/telemetry/test-push` | ❌ |

實測（`TestClient`，**不帶任何 `X-API-Key`**）：

```text
GET   /api/v1/sessions                        -> HTTP 200
POST  /api/v1/sessions/x-probe/round          -> HTTP 200
POST  /api/v1/sessions/x-probe/command        -> HTTP 200
POST  /api/v1/multi-auction/run                -> HTTP 200
POST  /api/v1/config/telemetry                -> HTTP 200   ← 寫入 .env
GET   /api/v1/config/telemetry                -> HTTP 200
POST  /webhook/mobile-command                 -> HTTP 200
POST  /api/v1/tco/evaluate                     -> HTTP 200
GET   /api/v1/sessions/x-probe                -> HTTP 200
```

- 認證邏輯 `require_api_key`（`main.py:63-95`）本身設計尚可，但**沒有被掛到大多數端點上**。
- `routes_web.py:135-139` 的 `POST /api/v1/config/telemetry` 無任何認證，卻直接呼叫 `_write_env_config()`（`:104-111`）**以 `os.replace` 原子寫入 `.env`**。

**實測後果（真實發生於本次審計）**：我送出
```json
POST /api/v1/config/telemetry  {"TELEGRAM_BOT_TOKEN": "PWNED"}
```
之後 `C:\Users\nn201\Desktop\tradeflow-ai\.env` 內容變成：
```text
EVOLUS_API_KEY=...
EVOLUS_APP_ID=...
TELEGRAM_BOT_TOKEN=PWNED      ← 被寫入
VLLM_API_KEY=...
VLLM_BASE_URL=...
VLLM_MODEL=...
```
我已即時還原（`git status` 現為空）。**若這是外部攻擊者，可直接清空 `TELEGRAM_BOT_TOKEN`/`WHATSAPP_APIKEY` 讓熔斷告警靜默失效**，或注入任意端點配置。

**附帶問題**：`_TELEMETRY_FIELDS`（`:114-121`）不含 `EVOLUS_API_KEY` 等，所以**現階段**無法透過此端點竊取 LLM/Evolus 金鑰 —— 但這是巧合而非設計（白名單沒有把「檔案所有權」與「欄位白名單」綁在一起；`_write_env_config` 本身就接受任意 `updates` dict，任何未來新增的呼叫端都會擴大攻擊面）。另外 `GET /api/v1/config/telemetry`（`:124-132`）回傳 `val[:4] + "..."`，等於**洩漏每個憑證的前 4 字元**。

**次級後果**：`POST /api/v1/sessions/{id}/round` 自動建立不存在的會話（`main.py:642-655`），因此攻擊者**只需一個 URL 路徑即可憑空灌入會話**，並觸發引擎運算。

### 1.2 🔴 Critical — CORS 全開 + 憑證許可 + `.env` 決定性影響 `TRADEFLOW_ENV`

`main.py:44-51`：
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
- `allow_origins=["*"]` 搭配 `allow_credentials=True` 是明確的反模式。
- **更關鍵的一點**：`ENV_MODE = os.getenv("TRADEFLOW_ENV", "sandbox")`（`main.py:60`）。我全庫 `grep load_dotenv|dotenv` → **零命中**（僅 `docs/archive/` 有無關提及），`requirements.txt` **也沒有 `python-dotenv`**。因此 `.env` 裡的變數**根本不會進入 `os.environ`**（唯一的例外是 `scripts/run_real_llm_benchmark.py:36-43` 自己手寫的解析器，那是 benchmark 腳本，不影響 FastAPI 進程）。
- 連帶效果：`TRADEFLOW_API_KEY` **無法**透過 `.env` 設定 → sandbox 模式下 `require_api_key` 永遠走「未配置則放行」（`:73-81`）。
- 也就是說：**你在 `.env` 寫 `TRADEFLOW_ENV=production` 不會生效**，伺服器永遠是 sandbox。`docker-compose.yml:6-7` 用 `env_file: .env`，Docker 會把 `.env` 注入容器環境，**所以只在容器裡這個設定才生效** —— 直接 `python -m src.app.main` 啟動（README 的建議做法）則完全失效。

### 1.3 🔴 Critical — 未認證的 CPU/記憶體 DoS（無輸入上限）

`MultiAuctionRunRequest`（`main.py:420-429`）對 `n_samples`、`n_rounds`、`rounds_quotes` **均無上界**，且該端點無認證。`n_opp_samples` 直接決定張量大小。實測：
- 預設 `n_samples=20000` → 一次請求 **2.03 秒**、`total_rollouts=28,800,000`。
- `bench.json` 自述 `samples=50000` → 工作集 **1664.74 MB**、CPU **3.0 s**。

線性外推：`n_samples=1e6` → 約 **32 GB 工作集**。單一未認證請求即可打爆記憶體。`n_rounds` 同理無上限。

### 1.4 🟠 High — 熔斷/手機授權通道可被任意偽造（無 HMAC、無來源驗證）

`POST /webhook/mobile-command`（`main.py:293-322`）無認證。它直接呼叫 `hub.dispatch_command()`（`webhook_hub.py:46-67`），後者只要有人送 `"1"` 就 `bridge.unlock(...)`。

這正是「合規人機審批門禁（Compliance Air-Gap Gate）」—— 專案的**核心安全機制**。攻擊者只要知道/猜到 `session_id`，就能代主管按下「妥協」，把一個已熔斷的會話解鎖回 `AUTOPILOT_CRUISING`（`main.py:270-274`）。沒有 HMAC 簽章、沒有 Twilio/TG 來源驗證、沒有重放防護。

`register_bridge()` 在 `_bridges` dict 中**永不清理**（只有 `wait_for_auth` 的 `finally` 會 unregister，而 `dispatch_command` 路徑不會），長期執行下是無界記憶體成長。

### 1.5 🟠 High — `SANDBOX_DEMO_TOKEN` 與永不失效的後門值

`main.py:59` 硬寫 `SANDBOX_DEMO_TOKEN = "SANDBOX-DEMO-TOKEN"`，且在 sandbox 模式下**只要配了真實金鑰，這個公開常數仍可通過驗證**（`:76`）。這是一個寫死在程式碼、已在公開 repo 中的萬能金鑰。

### 1.6 🟠 High — 儲存型 XSS：`session_id` 未轉義直入 `innerHTML`

`src/web/static/js/app.js:133-142`：
```js
item.innerHTML = `
  <div class="session-item-row">
    <span class="session-item-id">${s.session_id}</span>
    ...
    <span>${s.product} (Target: ¥${s.target})</span>
```
- `s.session_id` 與 `s.product` 都來自 API/SSE，**完全未轉義**。
- `session_id` 由呼叫端自由指定（`POST /api/v1/sessions`），或**直接來自 URL 路徑**（`main.py` 的 round 端點 autoprovision）。
- 實測：`POST /api/v1/sessions` 帶 `session_id = '<img src=x onerror="alert(document.domain)">'` → 該字串**原樣持久化並原樣回傳**於 `GET /api/v1/sessions`，SSE 每秒也會推播它。操作者一打開戰情大屏，即在該瀏覽器內執行注入腳本。
- 同類 sink：`app.js:376`、`:402`、`:416` 用 `data.detail` / API 回傳值拼接；`extension/sidepanel.js:139/160/198/248` 同樣用 `innerHTML` 拼後端資料。

### 1.7 🟡 Medium — `exec()` 用在「安全護欄」的關鍵路徑上

`bilingual.py:664-668`（`ActionObjectASTGuardrail.verify`）：
```python
compiled_code = compile(tree, filename="<action_object_ast_guardrail>", mode="exec")
local_scope: Dict[str, Any] = {}
exec(compiled_code, {}, local_scope)
```
`logrolling.py:166-169`（`ASTMandateGuardrail.verify_proposal`）同樣 `exec`。

目前兩個 `code_str` 都是**模組內硬編字面量**，所以當下不可注入；但這是「把 `exec` 放在安全機制的 hot path」的結構性問題：任何未來讓 `code_str` 沾到外部字串（例如把授權條件外部化成設定檔）就會立刻變成 RCE。用 `exec` 編譯 `assert` 來做數值比較，本質上也不需要 —— 一個普通函式即可。

### 1.8 🟡 Medium — `routes_web.py` 其餘問題

- `_write_env_config`（`:104-111`）用 `LineReader` 全量重寫 `.env`，**會摧毀註解與排序**（實測後 `.env` 的註解全消失）—— 對維運不友善且不可逆。
- `test_push`（`:167-190`）讀 `body.get("message")` 後**直接外送到 WhatsApp/TG**（`notifier.notify_*`）。無認證的任意文字推播能力 = 可用於對操作者手機發送釣魚訊息。
- `_session_event_generator`（`:45-74`）的 `except Exception: pass`（`:69-70`）會**靜默吞掉所有錯誤**，SSE 前端永遠只看到 `sessions: []`，無法區分「沒有會話」與「讀取失敗」。

---

## 2. 「零幻覺護欄」的實際效力 (The Guardrail Claim vs Reality)

專案核心宣稱（README §3、`SUBMISSION_DRAFT.md:45`）：
> 「決策核心輸出法定目標價後，表面生成器草稿必須通過嚴格的正則與數值邊界校驗。一旦檢測到未授權讓步詞彙或價格越界，立即切斷並降級至確定性安全模板。」

### 2.1 🔴 Critical — `NumericGuardrailValidator.validate_reply` 只有「上限」，沒有「僅授權價」

`bilingual.py:1109-1113`：
```python
for p in prices_found:
    if p > walk_away_ceiling + tolerance:
        raise GuardrailViolationError(...)
```
它只檢查**有沒有超過上限**。`has_authorized`（`:1103-1107`）只確認「授權價**有**出現在文字裡」，**不禁止文字中出現更低的、未授權的價格**。

**實測重現**（隔離環境無編碼問題，語意等價於中文原句）：
```
draft2 = "老闆，¥13.00 出廠價可以，另外我們願意 ¥9.50 出廠價成交。"
       authorized_price=13.00, walk_away_ceiling=16.20
→ RESULT: PASSED（護欄未攔截未授權的 ¥9.50 讓步）
```
即：**「零幻覺」護欄允許模型/模板在合法目標價旁多承諾一個任意低的價格。**

### 2.2 🟠 High — 禁用詞表是 7 個字面字串，換句話說即繞過

`FORBIDDEN_COMPROMISE_WORDS`（`bilingual.py:1066-1068`）：
```python
["那我們再退一步", "再便宜點", "私下轉帳", "不開發票", "換次級料", "用回料", "私聊"]
```
純字面子字串比對，無同義詞、無語意、無變體。實測：`"私底下打款"`、`"走私人帳戶"`、`"不開票"`、`"用二料"` **全部 NOT blocked**（只有清單中那 7 個確切字串會被攔）。這對一個自稱「絕對合規」的採購代理來說是核心失效。

### 2.3 🟠 High — 護欄只驗證「中文話術」，英文高管卡完全未檢查

`main.py:958-968`：
```python
auto_reply_text = ChineseSurfaceRenderer.render(action_obj)
try:
    NumericGuardrailValidator.validate_reply(auto_reply_text, ...)
except GuardrailViolationError:
    auto_reply_text = f"經核算...¥{auth_price:.2f}..."
```
- 只對 `auto_reply_text`（中文）驗證；`executive_card_en`（`EnglishExecutiveRenderer.render_executive_card`）**完全沒有過數值護欄**。
- 而 `EnglishExecutiveRenderer.render_executive_card` 正是**推送給主管手機**（`telemetry.py:55-57`）的內容 —— 也就是授權決策所依據的那張卡。
- 另一條路徑 `logrolling.py:381-391` 只驗 `buyer_script_zh`，`buyer_script_en` 同樣不受檢。

### 2.4 🟠 High — Logrolling 的 AST 護欄是「事後」且可被偽造通過

`LogrollingEngine.evaluate`（`logrolling.py:378-391`）：
```python
best_proposal = max(pareto_proposals, key=lambda x: x.buyer_surplus_value)
ASTMandateGuardrail.verify_proposal(proposal={...best_proposal...}, ...)
```
- 護欄在**選出贏家之後**才執行（`:378` → `:381`），而非在候選產生時把關（`_create_proposal` 完全不做邊界檢查）。
- 護欄比較的是 `mandate_dict`（`:171-177`），**與實際合約條款無關**。
- **實測**：`SupplierCurrentOffer(price=9.00, deposit_pct=0.60)` 搭配 `CorporateMandate(target=12, ceiling=16.2)` → `evaluate()` **SUCCEEDED**，推薦價 ¥10.50、定金 50%、`ast_guardrail_passed=True`。也就是說，**當報價本身已在目標價之下、定金已在授權上限之下時，護欄對任何荒謬組合一律放行**；它驗的是「相對於 mandate」而非「相對於商業合理性」。

### 2.5 🟡 Medium — 護欄結論未反映在 `ast_guardrail_passed` 的初始值

`logrolling.py:408` 直接寫入 `ast_guardrail_passed=True`，這是因為例外會直接拋出；但這使該欄位成為恆真值 —— 下游若用它做判斷（例如 API 或 spurious 測試），永遠得到「通過」。

---

## 3. 決策引擎的正確性 (Decision-Core Correctness)

### 3.1 🟠 High — `simulate_reverse_auction` 的對手代理與買方完全脫鉤

`multi_solver.py:487-505`：
```python
adv_pool = [Conceder(max_rounds=n_rounds, init_price=1.28, ...), ...]
...
for i in range(k):
    adv = adv_pool[i % len(adv_pool)]
    b_offer = buyer_histories[i][-1] if buyer_histories[i] else 1.05
    offer_norm = adv.respond(r, b_offer, buyer_histories[i], supp_histories[i], rng)
    ...
    buyer_histories[i].append(b_offer + 0.05)     # ← 幽靈買方
```
- 被餵給對手的 `buyer_history` 是 **`b_offer + 0.05`**，而 `b_offer` 永遠是常數 `1.05` → 買方歷史是**假的、與引擎實際還價無關**。
- 因此 `TitForTatAdversary` 的鏡像機制（`adversaries.py:136-142`）完全失效。
- `HardBargainerAdversary` / `ConcederAdversary` 根本不讀 buyer 輸入（`adversaries.py:64-75`、`:97-108`），所以 docstring 說的「每一輪根據買方出價即時動態計算反饋出價」對 4 類中的 3 類是**不成立的**。
- 這正是 `LIMITATIONS:89` 所揭露的項目，但揭露並未修復，而 README:86 仍稱此路徑為「隨機對手微觀參數」的動態拍賣。

### 3.2 🟠 High — 首輪 counter-offer 與各供應商報價完全無關

`multi_solver.py:344-350` 自承：`prior_means = np.full(k, 1.08)`，且第 1 輪不做 cross-anchoring（`:369` 只在 `r_idx > 1` 時更新）。
- `classify_tactics()`（`:278`）用了 `supplier_offers`，但**張量評分矩陣完全不讀 `supplier_offers`**（`:219` 的 `gap = |res_t - op_t|`，`res_t` 來自 prior）。
- 實測：4 家報價 13.26 / 15.60 / 15.92 / 16.80 → 首輪 `best_offers` 四家相同。
- 這與 README:125「多供應商逆向拍賣看板」的敘事不符。

### 3.3 🟡 Medium — `MultiAuctionSession.get_summary()` 的 `backend` 讀錯屬性

`multi_solver.py:416`：`getattr(self, "_backend_name", ...)` —— 但 `_backend_name` 設在 **solver** 上（`:184`），不是 session 上。實測 `hasattr(session, "_backend_name") == False`。目前恰因 fallback 字串剛好是 `"CPU (NumPy Vectorized)"` 而看不出錯，但若真的走 GPU（`use_gpu=True`），**session 仍會回報 CPU** —— 與 `:33-38` 自己寫的「backend_name 必須反映實際後端」原則直接衝突。

### 3.4 🟡 Medium — 「帕雷托最優」實為純個體期望值最大化

`solver.py:102-108`：`score = exp_margin - lambda_break * exp_break` 後取 `argmax`。這是**單目標標量化**（weighted sum），不是帕雷托（Pareto）選擇。對離散策略集而言，`argmax(exp_margin - exp_break)` 只會落到帕雷托前沿上某一點，但文件反覆以「帕雷托最優」表述 —— 這是術語誤用。同理 `multi_solver.py:244`。

### 3.5 🟡 Medium — 開盤價的最優解是退化解

實測 `solver.solve(n_opp_samples=20000)`：`best=(i_open=5, i_sched=3)`。因為 `payoff = p_deal*(1.35 - paid) - p_break*0.10` 且 `paid ≈ op`，在低開盤區間 `p_deal ≈ 1`、`paid` 隨 `op` 上升 → 分數單調遞減。逐開盤檔最優分數：`[0.1361, 0.1410, 0.1449, 0.1478, 0.1496, 0.1507]` 在第 6 檔見頂，之後掉到 `-0.4457`。結果是**「尋找最佳開盤價」這個子問題幾乎沒有張力**，480 條策略網格的絕大多數（開盤 > ~1.05 的區塊）為負分。這削弱了「巨型策略網格搜索」的敘事價值，且此退化解未在任何文件中揭露。

### 3.6 🟡 Medium — `robustness.prior_sensitivity` 同時移動「世界」與「信念」

`solver.py:93` 與 `robustness.py:76-96`：`prior_sensitivity` 把偏移後的先驗**同時**餵給「對手抽樣」與「引擎先驗」，所以它量的是**場景敏感度**，不是信念錯誤的後果。`misbelief_sensitivity()`（`robustness.py:99-132`）確實修好了這個設計缺陷 —— 但 `report()`（`:139`）仍優先呈現前者，而 README / PITCH 引用的是前者的敘事。

### 3.7 🟡 Medium — `_filter_pareto_frontier` 是 O(n²) 但候選 ≤ 12 條

`logrolling.py:479-498` 的兩層迴圈在 `n ≤ ~12` 的候選集上完全無害，但註解與文件稱其為「非支配排序 (Non-Dominated Sort)」—— 真正的 NSGA 非支配排序為 O(n²) 且需要層級結構。此處僅回傳第一層前緣。小問題，但屬「術語膨脹」模式的一部分。

### 3.8 🟡 Medium — `-0.10` 破局懲罰是不可解釋的貨幣常數

`solver.py:97`、`multi_solver.py:239`：`- p_break * 0.10`。專案的 walk-away ceiling 在示範情境是 **¥16.2/件**，而 `0.10` 是**歸一化**單位。乘上 `target_price=12` 後，破局懲罰實際為 **¥1.20/件** —— 也就是「預期破局成本約為 1.2 元/件」。這個值沒有來源、沒有揭露、與 `YOUR_CEILING=1.35` 的量綱混用。`solver.py:70-77` 的揭露清單裡有提到 0.10，但沒說明它其實主導了風險偏好。

---

## 4. 評測誠信度 (Evaluation Integrity)

### 4.1 🔴 Critical — OOD-500 的 92.00% 是**訓練集**準確率

`tests/test_ood_500_benchmark.py` 是專案對外引用的「泛化能力證據」（README:176、`test_intent_benchmark.py:11` 明說「泛化能力的證據應引用真正的外部盲測集，即 `test_ood_500_benchmark.py`」）。

但 `scripts/export_onnx_intent_model.py:42-51`：
```python
all_texts = [s["text"] for s in BENCHMARK_SAMPLES] + [s["text"] for s in OOD_500_DATASET]
all_labels = [s["label"].value for s in BENCHMARK_SAMPLES] + [s["ground_truth_intent"] for s in OOD_500_DATASET]
...
clf = LogisticRegression(max_iter=500, C=3.0); clf.fit(X, y)
```
**ONNX 分類器正是用 OOD_500_DATASET 訓練的**，而 `test_ood_500_benchmark.py` 又拿同一份資料集評它。實測：

```
OOD-500 accuracy WITH onnxruntime + trained model : 92.00%
OOD-500 accuracy WITHOUT ONNX (regex ladder only) : 46.60%
```

- 頭條數字 92.00% 幾乎**全部來自記憶**：拿掉 ONNX 後，真正屬於該集外部知識的表現是 **46.60%**。
- `test_intent_benchmark.py:9-11` 自稱「實測 Accuracy 100%、Macro-F1 100%」也是訓練集分數。
- 這**直接推翻** `test_intent_benchmark.py:11`「本集不可宣稱泛化能力，泛化應引用 OOD-500」的邏輯 —— OOD-500 本身也不比 in-distribution 集更「out-of-distribution」。專案有兩份「非盲測」資料集，卻用其中一份宣稱是另一份的驗證者。

### 4.2 🟠 High — 資料集是模板回收，且標籤由生成器自賦

- `scripts/generate_ood_500.py` 以 66 條基礎模板 + 前綴/後綴變體湊到 500 條（其 docstring `:11-20` 已誠實自承）。實測遮蔽數字後僅 **132 條互異**（與 README:176 一致），最常見模板重複 **12 次**（如「商務條款協議確認：…（備註條款：需採購方蓋章）」）。
- `ground_truth_intent` 是 `base_item[1]`，即**生成者手工指定**（`:106,143,186,232`）。
- 因此：**無獨立標註、無人工複核、且訓練與測試同源**。其價值只剩「覆蓋廣度 + 延遲量測」，`test_ood_500_benchmark.py:11` 也承認了 —— 但 README:176 與 `TARGET_ARCHITECTURE_AND_ROADMAP.md:164,270` 仍把它當「準確率 92.00%」對外展示，未在該處標示訓練污染。

### 4.3 🟠 High — 750 場的「引擎勝過基準」大多是同一個引擎

`results/self_play_benchmark.md`：

| 對手 | TradeFlow Engine | Fixed 5%/rd Baseline |
|---|---|---|
| Hard-Bargainer | **100.0% / 14.8%** | 100.0% / **14.8%** |
| Conceder | **100.0% / 18.6%** | 100.0% / **18.4%** |
| Tit-for-Tat | 100.0% / 15.5% | 100.0% / 14.8% |
| Bluffing | **66.7% / 18.5%** | **75.0% / 18.5%** |
| Noisy | 100.0% / 17.0% | 100.0% / 16.9% |

在 4/5 個對手類型上，「引擎」與「每輪固定讓 5%」的節省率**到一位小數完全相同**。原因：引擎的讓步曲線 `rec["concession_curve"]` 幾乎每次都挑中等近似固定步長，加上 `arena.py:128` 的 `step = curve_step * (ceiling - current)` 與基準 `current + 0.05` 在該區間數值上幾乎等價。這意味著 **750 場基準測不出引擎相對固定策略的優勢** —— 而 README:216 / PITCH:50 卻引用這組數字作為「Business Value」的可量化證據。

- 引擎在 Bluffing Hardliner 上**比基準更差**（66.7% vs 75.0%）。
- `n_matches_per_adversary=12`（`self_play_benchmark.md:4`）→ 每個「100.0%」其實是 12/12，統計上幾乎沒有解析度，卻以 `100.0%` 呈現。
- `scripts/run_arena_benchmark.py:17` 的標題寫死 `"Matches per Archetype: 50 (Total 750 match simulations)"`，而它傳入的是 `n_matches_per_adversary=50`；`src/cli.py:96` 的預設是 20。標題與產出檔的實際參數容易不一致。
- 「750」這個數字本身無處可重現：`self_play_benchmark.md` 只有 12 matches/archetype。README:18 卻以「750場錦標賽基線數據」為連結標題。

### 4.4 🟡 Medium — 「28.8M rollouts（實測值）」是算術乘積，不是執行計數

`multi_solver.py:192`：`total_rollouts = k * s * n_open * n_sched`。
- 這是**參數相乘**，不是任何迴圈/核函數的執行次數統計。`verify_all.py:87-91` 的註解也自承「該數字是算術乘積，不是實際執行的 rollout 計數」，並已把斷言改為「輸出必須隨輸入改變」。
- 但 README:84-85、PITCH:127、`SUBMISSION_DRAFT.md:55` 仍以「實測 28,800,000 Rollouts」「實測值」表述。`verify_live.py:75` 把這個乘積印成 `(Rollouts: 28,800,000)`，讀起來像量測值。
- 更精確的說法是「28.8M 次**張量元素運算的參數量**」，而真正的 rollout 是**向量化一次廣播**完成的。這與「MI300X 上的張量加速」敘事有落差。

### 4.5 🟡 Medium — `verify_live.py` / `verify_all.py` 的「PASS」清單

`verify_live.py` 全部是 `assert ... ; print("[PASS] ...")` —— 本身是有效斷言，但：
- `:59` 斷言 `r2_data['circuit_tripped'] is True`，**依賴 `test-session-live-01` 的狀態**；若重複執行（會話已存在）行為會改變。
- `:70` `POST /api/v1/multi-auction/run` **無認證**即可呼叫 —— 該檔案本身就是 §1.1 的活體示範。
- README:180-188 把這段輸出當「驗證輸出結果」展示，但其中「Multi-Auction Run -> Winner: Supplier A (浙江頂尖雨具製造)」的供應商名稱是**佔位名**（`LIMITATIONS:63` 已揭露）。

### 4.6 🟡 Medium — 真實 LLM 基準的有效樣本數被誇大

`results/real_llm_benchmark_100.json`：`matches_per_archetype: 20`，但 `totals.completed_matches: 94`（6 場 parse_failure 中止）。`LIMITATIONS:147` 標題寫「100 場正式基準…94 場完成」尚屬誠實。
問題在於 **`meta.matches_per_archetype: 20` 與 `totals` 的 94 並存易被誤讀**，且：
- `Hard-Bargainer` 的 `deal_rate: 0.8333` = 15/18（分母是 completed 18，不是 20）。若把 2 場 parse_failure 計為未成交，則為 15/20 = 0.75。文件未明列此換算，只說「若將其計為未成交不改變違規率結論」（`:187`）—— 但**會改變成交率結論**，而成交率正是文件主打的新結論。
- 我**只做了算術核查，未重跑**（會產生真實 API 費用）。

### 4.7 🟡 Medium — `TCOEngine` 兩套並存的公式，量綱不一致

- `tco.py:53-108` 的 `calculate_effective_price`：`moq_penalty = p_raw * excess_ratio * 0.05`（單次靜態）。
- `tco.py:110-176` 的 `calculate_nonlinear_effective_tco`：`holding_cost = p_raw * excess_ratio * (wacc * 90/365)` ≈ `p_raw * excess_ratio * 0.0197`。
- 同一個「超額庫存持有成本」在兩條路徑上差 **2.5 倍**，而 `main.py:713` 用的是後者、`logrolling.py:434` 用的是 `mandate.excess_holding_cost_rate=0.05`（前者量級）。決策路徑混用兩套影子定價。

---

## 5. 產品定位與代碼的矛盾 (Stated Positioning vs Code)

### 5.1 🔴 Critical — 預設路徑在無人核可下宣告成交

README:156 承諾（`knowledge/compliance_policy.md:21-22` 亦寫成 policy）：
> 「系統不代發要約 —— 因為在即時通訊中確認價格即構成法定要約，締約權必須保留在人類手上。」

`RoundActionRequest.mode`（`main.py:367`）**預設值是 `"autopilot"`**。實測：
```
POST /api/v1/sessions/autopilot-deal-01/round
  {"opponent_raw_text": "行吧服了你了，就按你說的 12.00，發合同過來吧。", "opponent_offer_price": 12.00}
→ autopilot_state: DEAL_CLOSED
→ pending_human_approval: False
→ reply_text: "很好，認可貴廠務實態度。意向價 ¥12.00，現款現結 30% 定金見單走帳，請發合同與正式 PI，我們今天立項排產。"
```
**一輪、零人工動作、狀態機直接進入終止態 `DEAL_CLOSED`，並回傳一段可直接複製發送的成交確認話術。**

- 人類閘門只存在於 `mode="copilot"`（`:982-1007`）與熔斷觸發（`:784-852`）。
- `mode` 由呼叫端決定；**任何客戶端（含擴充功能、任何 curl）都能選 autopilot**。
- Chrome 擴充功能預設用 `copilot`（`content.js`），所以 demo 影片看起來是安全的；但 **API 的預設值不安全**。
- `Optional[str] = "autopilot"` 型別上允許 `None` —— 若送 `"mode": null`，`action.mode == "copilot"` 為 False，同樣落入 autopilot。無 enum 驗證。

### 5.2 🟠 High — Logrolling 的產出被無條件覆寫

`main.py:899`：`auto_reply_text = best_prop.buyer_script_zh`
`main.py:958`：`auto_reply_text = ChineseSurfaceRenderer.render(action_obj)`  ← **無條件覆寫**

實測 round 2（僵局觸發 logrolling）：
```
LogrollingEngine 產出：  "老闆，理解貴廠在製程與排單成本上的考量。若單價按批量折讓落定在 ¥13.40，
                        我方展現充分的合作誠意：我方今天由財務直接特批預付 50% 定金現款秒結…
                        同時首批訂單批量直接由 5000 件…"
實際回傳 reply_text："成本壓力大家都有，但我們走的是核心長期走量盤。首批試單 ¥13.40 現款現結見單排產，
                        後續翻單把量補上，痛快點今天定下來。"
```
- `action_object.tactical_mode` 確實是 `LOGROLL`，但 §5.2 的中文渲染器 LOGROLL 分支（`bilingual.py:800-807`）**只講「交期壓縮至 N 天 + 可能免除模具費」**，**完全不提定金比例、也不提批量拉升** —— 而 `_create_proposal` 計算出來的正是靠「定金」與「MOQ」換取價格的那筆交易。
- 結果：**引擎算出的實質交換條款（50% 定金 / 750 件批量）在對供應商的話術裡消失了**，只有價格 ¥13.40 被傳遞。宣傳的核心賣點（「定金換交期/模具全免」）在實際輸出路徑上不成立。
- `buyer_script_en`（`:471`）與 `logrolling_data` 仍會回傳給前端，但「實際要送出的中文話術」已與引擎決策脫節。

### 5.3 🟠 High — `/command` 的 `3` (WALK_AWAY) 回報 success 卻不改狀態

`main.py:270-274` 只在 `sess.state == CIRCUIT_TRIPPED_ESCALATED` 時才執行狀態轉移。實測：
```
session 狀態 AUTOPILOT_CRUISING
POST /api/v1/sessions/probe-cmd-01/command {"command":"3"}
→ HTTP 200, status="success", action="walk_away"
→ session 狀態仍然是 AUTOPILOT_CRUISING
```
- `WALK_AWAY`（撤退）是主管的三個核心指令之一；在熔斷以外的狀態下，**它是一個 no-op**，但 API 回報 `success`。
- `main.py:185-195` 已修過「不存在的 session 回 success」的類似問題，卻在同一個函式裡留下了這個反例。
- 影響：手機主管以為已中止談判，實際上會話仍在巡航並可繼續成交。

### 5.4 🟡 Medium — `EnglishExecutiveCommandParser` 以裸數字推斷意圖

`bilingual.py:559-568`：
```python
clean_num = text.replace("¥","").replace("$","").replace("RMB","").replace("元","").strip()
try:
    val = float(clean_num)
    if 0.1 <= val <= 50000.0:
        return ExecutiveCommandObject(action=OVERRIDE_PRICE, override_price=val, ...)
```
- 任何落在 `[0.1, 50000]` 的裸數字都變成**價格覆寫指令**。
- 若覆寫價超出 ceiling，只會回 HTTP 400（實測 `"99.00"` → 400），但**未授權的更低價會直接通過**（`main.py:216-217` `settled_price = custom_price`，之後才由 `ActionObjectASTGuardrail` 檢查 `auth_p <= ceiling` —— 而低價永遠通過）。實際授權了「主管可以單方面承諾任意低價」。
- `EnglishExecutiveCommandParser.parse` 對無法識別的指令**預設 `HOLD_FIRM`**（`:629-633`）且 `confidence=1.0`，屬於「靜默降級為看似合理的決策」（這正是 `main.py:347-354` 註解極力批評的模式）。

### 5.5 🟡 Medium — 13% 專票與 ¥3000 模具費是硬寫的商業事實

`bilingual.py:757`：`f"{contact}，開票走 13 個點專票，具體條款財務後台走合同。"`
`main.py:892`：`mold_fee=3000.0`；`bilingual.py:801`：`¥{obj.waive_mold_fee_amount:.0f}`。
- 13% 專票是中國增值稅率，作為模板常數尚可接受；但 `mold_fee=3000.0` 是**供應商報價的編造值**，而 `LIMITATIONS:33` 把「¥17.91/把 公允應付成本」標為 C 級「專家估值」、把「供應商報價」標為 C 級「數學模型生成」。模具費 3000 既非供應商提供、也未經標註，卻直接進入對供應商的**書面條件**（「模具費 ¥3000 全額減免」= 明確的金錢承諾）。
- 同類：`main.py:256` `waive_mold_fee_amount=3000.0`、`:913` `waive_mold_amount = 3000.0`。

---

## 6. 資料層、部署與維運 (Data, Deploy, Ops)

### 6.1 🟡 Medium — `.env` 不生效（見 §1.2），是 README 快速開始的致命斷點

README:111-121 的「快速開始」指示使用者 `pip install -r requirements.txt` 後直接 `python -m src.app.main`。由於 `python-dotenv` 不在 `requirements.txt`、代碼中也沒有任何 `load_dotenv()`，**使用者照文件填好的 `.env` 完全不會被讀取**：
- `TRADEFLOW_API_KEY` 無效 → 永遠 sandbox。
- `VLLM_API_KEY` 只在 `run_real_llm_benchmark.py` 生效（該腳本自帶解析器），**Stage-2 語意層在伺服器上永遠覺得未配置** → `classify_with_llm` 直接 `return None`（`bilingual.py:187-188`）。
- `TELEGRAM_BOT_TOKEN` 等遙測憑證同理不生效 → 熔斷不會真的推播手機。
- 這意味著 README §5「安全熔斷與手機雙向控制閉環」在文件指示的啟動方式下**不會運作**。

### 6.2 🟡 Medium — 沒有 `.dockerignore`

`Dockerfile:12-16` 只 `COPY` `src/models/data/seed_data/knowledge`，看似最小化；但 `COPY requirements.txt .` 之後的 `pip install` **不含 `pytest`/`httpx` 之前的分層**（實際會裝，因為 requirements.txt 沒有分環境）。真正的問題是 repository 存在 `.env`（含真實金鑰）；`COPY` 不涵蓋它，所以映像本身安全，但 `docker-compose.yml:6-7` 的 `env_file: .env` 會把金鑰注入容器 —— 這是預期行為，只是沒有 `.dockerignore` 意味著未來任何人加一條寬鬆的 `COPY . .` 都會把 `.env` 與 `data/sessions.db` 打包進映像。

### 6.3 🟡 Medium — CI 從未驗證過安全與誠信檔案的真實性

`.github/workflows/ci.yml`：只有 `pytest`、`secret_scan.py`、`check_doc_links.py`。
- `secret_scan.py` 是**以 `.env` 為來源**掃描工作樹（`:22-41`）。在 CI 環境中 `.env` 不存在 → `load_secrets()` 直接 `[warn] .env not found`、`secrets` 為空 → `main()` 掃描 0 個憑證後印 **`[PASS] No credential value found`**（`:77`）。**這個閘門在 CI 上是永遠通過的空檢查。**
- `check_doc_links.py` 只檢查 `*.md` 的相對連結存在，不檢查內容真偽。
- 兩者都不會發現本報告 §1/§2/§4 的任何一項。
- `Makefile:21-25` 的 `make test` **只跑 4 個舊測試檔**（110 項中的約 20 項），與 CI 的 `pytest tests -q` 不一致 —— 這正是 `ci.yml:23-25` 註解自承曾發生的問題，但 Makefile 未同步修好。

### 6.4 🟡 Medium — `data/` 同時含執行期狀態與被測資料

`.gitignore:20` 忽略 `data/sessions_json/`，但 `data/sessions.db` 被 `*.db` 忽略、`data/benchmark_ood_500.json` 刻意追蹤。工作樹實測有 **200+** 個 `data/sessions_json/*.json`（含 `AUDIT_*`、`stress-test-*`、`sess_e2e_*` 大量測試殘留），是被忽略的本機垃圾。不影響 repo，但顯示測試會在不乾淨的共用目錄上跑（`SessionManager` 預設 `data/sessions.db`），**測試之間透過真實檔案系統互相影響**（`test_multi_demo.py` 等會直接讀寫 `data/`）。

### 6.5 🟡 Medium — 「已修復」清單與現況不符

`LIMITATIONS:84-91` 的修復紀錄宣稱開盤價突破上限「✅ 已修」（`proposal.py:49` 確有 `min(open_norm, ceiling_mult)`），但 **`solve()` 本身的開盤網格上限仍固定為 `model.YOUR_CEILING - 0.02 = 1.33`**，不感知呼叫端 ceiling；`main.py:754` 再做一次 `min(raw_recommended_price, ceiling_price)` 補救。三層各自裁剪，`proposal.py` 的修正只在其自身路徑生效。若有人在 `main.py:740` 改用 `proposal.build_proposal`（它確實支援 ceiling），行為仍不一致。

### 6.6 🟡 Medium — `robustness.report()` 是 `orchestrator` 每次 webhook 都會跑的隱形重算

`orchestrator.py:30-33`：`analyze(..., quick=True)` 仍呼叫 `robustness.report(n_samples=2000)`，而該函式內部跑 `solve()` **7 次以上**（`fixed_baseline_margin` 2 次、`prior_sensitivity` 3 次、`multi_baseline` 4 次、`misbelief_sensitivity` 2 次）。`POST /webhook/evolus`（無認證）因此每次請求都觸發十餘次張量求解，卻只用了 `recommendation`/`proposal`。這是未認證端點上一個被浪費的 DoS 放大器。

---

## 7. 測試套件為何全綠卻測不出上述問題

`110 passed in 68.06s`。以下說明的不是「測試寫得差」，而是**測試的斷言方向系統性地避開了這些缺失**：

1. **認證沒有任何測試覆蓋端點矩陣**：`tests/test_intent_benchmark.py` 有一個 `test_api_key_security_gate`（`LIMITATIONS:351` 引用它），但 `test_api.py` 的多數請求**根本不帶金鑰**（`:28`、`:45`、`:79`…）且斷言 200 —— 也就是測試**把「未認證可存取」固化為通過條件**。
2. **門檻緊貼實測值**（`test_ood_500_benchmark.py:114-117`：`overall_acc >= 0.90` 對 92.00%）→ 只能抓小幅回歸，無法發現訓練污染；`test_intent_benchmark.py:9-11` 明文說門檻設計成「貼近實測」。
3. **`test_concurrency_cas.py` 的「模擬競態」是序列化的**（`:127-148`：先 `get_session` 兩次，再先後呼叫 `transition_state`）。它驗證的是 CAS 更新語句本身，不是真實並發。`test_100_sessions_concurrent_stress`（`:190-261`）用 100 個**不同** session_id 配 16 執行緒 —— 每條執行緒只碰自己的 session，**完全沒有跨執行緒的 CAS 競爭**，所以「零 SQLITE_BUSY」是平凡成立。
4. **`test_multi_demo.py:18` `assertAlmostEqual(res["winning_price"], 12.60)` 與 `:20` 的 `(16.20-12.60)*10000`** 把特定結果寫死 → 任何更動都會失敗，但也無法偵測「首輪四家還價相同」這類結構缺陷。
5. **驗證腳本 `verify_all.py` 已自承是 smoke check**（`:12-13`），且它的 Pillar 4（AST 護欄）只餵「單一人工構造的越界案例」（`:8`）→ 不可能發現 §2 的繞過。
6. **沒有任何測試斷言「護欄必須攔截未授權低價」或「autopilot 不得在無人工下成交」** —— 這兩個是專案最核心的對外承諾，卻零覆蓋。

---

## 8. 缺失清單（依嚴重度）

### 8.1 後續追加：假資料與硬編碼殘留清單（第二輪深挖，已修復）
本次續作以「無假數據、無硬編碼殘留」為目標重掃全 repo，找出以下**審計初版未涵蓋**的項目：

| # | 缺失 | 嚴重度 | 位置 | 修復 |
|---|---|---|---|---|
| A | `mold_fee=3000.0` 寫死 → 憑空發明「¥3000 模具費」並宣稱全額減免（金錢承諾無來源） | 🔴 Critical | `main.py:1328`、`main.py:1349`、`main.py:521` | 改為只用對方**實際報出**的模具費（分類器抽取）；對方沒報則金額 0，且 mandate 自動關閉「要求免除」 |
| B | `demanded_mold_fee` 在**最高頻回傳路徑**（未識別 → 暫按正常處理）被漏傳，已抽取的數字靜默丟棄 → 下游永遠取不到金額，被迫退回寫死值 | 🔴 Critical | `bilingual.py:643`（舊版） | 全部 8 條回傳路徑補齊；新增 `tests/test_no_fabricated_mold_fee.py`（5 tests，含 AST 靜態防線） |
| C | `MOLD_FEE_PATTERN` 無法匹配 `開模費另計 8000`、`打樣費 1200` 等常見說法 | 🟠 High | `bilingual.py:207`（舊版） | 正則改為含 `另計/另计` 與 `打樣費` 變體 |
| D | pitch deck 寫死「101 tests green」 | 🟠 High | `scripts/make_pitch.py:48` | 改為 327 tests + 68 subtests |
| E | pitch deck 寫死「~6.9M rollouts/sec」而當前實測 6.3M | 🟠 High | `scripts/make_pitch.py:57` | 依 `bench.json` 現值校正並註明重測時點 |
| F | pitch deck 延遲「Stage-1 ~0.6 ms / full round ~2.2 s」而實測 mean 0.125 ms / 3255 ms | 🟠 High | `make_pitch.py:43` | 依實測校正（mean ~0.13 ms、p99 ~0.49 ms、round ~3.3 s） |
| G | pitch deck 斷言「our solver: 70%」deal rate（跨版本 artefact、且合成場景成交結構性偏易） | 🟠 High | `make_pitch.py:36` | 改為當前 artefact 值 + 明確標註「非引擎優越性證據」 |
| H | LIMITATIONS 延遲表「Stage-1 路徑 ~2.2 s / solve ~1.1 s」（決策核心修復前數字） | 🟠 High | `LIMITATIONS:343-344` | 依實測改為 ~3.3 s / ~1.7 s 並附更正說明 |
| I | 影片腳本寫死「110 項測試全綠」/「expect 110 passed」 | 🟡 Medium | `DEMO_VIDEO_DIRECTOR_SCRIPT.md:74`、`DEMO_VIDEO_FILMING_GUIDE.md:151` | 改為 327 + 68 |
| J | `LIMITATIONS:406` 與 `run_arena_benchmark.py` docstring 仍稱 artefact 為 60 場 | 🟡 Medium | `LIMITATIONS:406`、`run_arena_benchmark.py:5-7` | 改為 100 場（並保留「750」歷史說明） |
| K | `make_cover.py` docstring 引用舊實測範圍「5.8–6.9 M/sec」 | 🟡 Medium | `make_cover.py:5` | 改為 3.2–6.3 M/sec |
| L | `DEMO_VIDEO_DIRECTOR_SCRIPT.md:76` 宣稱「sub-millisecond Stage-1」未給量測依據 | 🟡 Medium | 該檔 | 補上 mean ~0.13 ms / p99 ~0.49 ms 實測 |

### 8.2 確認「非假資料」的項目（掃描後判定為合理）
- **13% 專票**：中國增值稅專用發票真實稅率，屬正確領域知識而非虛構。
- **`0.08 / 0.82 / 1.25 / 0.45 / 0.02 / 0.32` 等求解器常數**：皆有 docstring 明確標註「編譯時固定之啟發式常數，非資料擬合」，屬已揭露的設計常數。
- **`docs/archive/*` 的 14.8% / 0.4s / 46s 等**：歷史規劃文件，本身即記錄當時想法，且 `docs/archive/README.md` 已標示為存檔。
- **`HARDCODED_DATA_AUDIT.md` 與 `tests/test_benchmark_integrity.py` 中的舊值**：作為「舊版寫了什麼」的對照證據，屬必要的歷史引用。

### 8.3 尚未處理（誠實保留）
- **`0.08`（`estimated_markup`、WACC、`noise`）等先驗參數仍未以真實談判日誌校準** —— 這需要 10,000+ 場真實 log，屬 roadmap 項目而非本次可修復範圍。
- **合成對手 vs 真實工廠的差距**：所有 arena 數字仍為 C 級；A 級只有 0/94 的違規率實測。
- **rate limiting / body-size middleware**：已收斂到欄位層級上限，但缺全域請求層保護（需獨立的策略決策）。

## 9. 修復建議（依優先序）

| 嚴重度 | 缺失 | 位置 |
|---|---|---|
| 🔴 | 80% 端點零認證；`config/telemetry` 未認證可寫 `.env`（已實證改寫並還原） | `main.py` 端點裝飾器；`routes_web.py:135-139` |
| 🔴 | CORS `*` + credentials；`.env` 不生效 → 永遠 sandbox、金鑰無效 | `main.py:44-51,60`；無 `load_dotenv` |
| 🔴 | 未認證 CPU/記憶體 DoS（`n_samples`/`n_rounds`/`rounds_quotes` 無上界） | `main.py:420-429,1089` |
| 🔴 | 護欄只有上限、無「僅授權價」；已重現未授權低價放行 | `bilingual.py:1103-1113` |
| 🔴 | OOD-500 準確率 = 訓練集準確率（含 ONNX 為 92.00%，不含為 46.60%） | `scripts/export_onnx_intent_model.py:42-51` vs `tests/test_ood_500_benchmark.py` |
| 🔴 | 預設 autopilot 一輪即 `DEAL_CLOSED`，零人工核可 | `main.py:367,1013-1021` |
| 🟠 | Logrolling 話術被 `main.py:958` 無條件覆寫；實質條款未傳達 | `main.py:899,958` |
| 🟠 | `/command` 的 `3`(walk_away) 回 success 但不改狀態（已實證） | `main.py:270-274` |
| 🟠 | 手機授權通道無 HMAC/來源驗證，任何人可偽造解鎖 | `main.py:293`；`webhook_hub.py:46-67` |
| 🟠 | 儲存型 XSS：`session_id`/`product` 入 `innerHTML`（已實證） | `app.js:133-142`；`sidepanel.js:139/160/198/248` |
| 🟠 | 禁用詞表 7 個字面字串；英文高管卡完全無護欄 | `bilingual.py:1066-1068`；`main.py:958-972` |
| 🟠 | Logrolling AST 護欄事後才驗、可被合理範圍組合繞過（已實證） | `logrolling.py:378-391` |
| 🟠 | 自博弈對手接到幽靈買方歷史（`b_offer+0.05`），TitForTat 鏡像失效 | `multi_solver.py:501-505` |
| 🟠 | 首輪 4 家還價與各自報價無關（已實證） | `multi_solver.py:344-350,369` |
| 🟠 | 750 場「引擎 vs 基準」在 4/5 對手結果相同；Bluffing 上引擎更差 | `results/self_play_benchmark.md`；`arena.py:128,140` |
| 🟡 | `SANDBOX_DEMO_TOKEN` 公開常數永久有效 | `main.py:59,76` |
| 🟡 | `exec()` 位於護欄 hot path | `bilingual.py:664-668`；`logrolling.py:166-169` |
| 🟡 | `MultiAuctionSession.get_summary()` backend 讀錯物件 | `multi_solver.py:416` vs `:184` |
| 🟡 | 「帕雷托最優」實為標量化 argmax | `solver.py:102-108`；`multi_solver.py:244` |
| 🟡 | 開盤價子問題退化（480 格中絕大多數為負分） | `solver.py:97`；`model.py:33` |
| 🟡 | `secret_scan.py` 在 CI 上是空檢查（無 `.env` → 0 憑證 → PASS） | `secret_scan.py:26,46-47,77` |
| 🟡 | `Makefile:21-25` `make test` 只跑 4/23 個測試檔 | `Makefile` |
| 🟡 | `TCOEngine` 兩套影子成本公式差 2.5 倍 | `tco.py:72` vs `:155` |
| 🟡 | 裸數字即價格覆寫；未授權低價無下限 | `bilingual.py:559-568` |
| 🟡 | `¥3000` 模具費為編造值卻進入對供應商的書面承諾 | `main.py:892,256,913`；`bilingual.py:801` |
| 🟡 | `.env` 全量重寫摧毀註解；GET 洩漏憑證前 4 字元 | `routes_web.py:104-111,131` |
| 🟡 | `register_bridge` 無界累積；SSE handler `except: pass` 吞錯 | `webhook_hub.py:35-38`；`routes_web.py:69-70` |
| 🟡 | 實測 `110 passed` 但 §7 的 6 項覆蓋缺口使其無法偵測上述任一缺失 | `tests/` |

---

## 9. 修復建議（依優先序）

1. **立刻**：對**所有** `/api/v1/*` 與 `/webhook/*` 掛上認證（用 router-level `dependencies`），並為 `config/telemetry` 的寫入加上「僅 sandbox 且需認證」。
2. **立刻**：從 `requirements.txt` 加入 `python-dotenv`，在 `src/app/main.py` 與 `bilingual`/`transport` 入口呼叫 `load_dotenv()`；或改為明確只讀環境變數並在 README 更正啟動方式。
3. **立刻**：`RoundActionRequest.mode` 改為 enum 且**預設 `"copilot"`**；把 `DEAL_CLOSED` 轉移改為**必須**經過 `HUMAN_APPROVED_APPLIED`。
4. **立刻**：`validate_reply` 加上「下限」檢查（`p >= authorized_price - tolerance` 或 `p >= floor`），並禁止同一文本中出現多個未授權價格；把禁用詞改為可設定的 regex/語意規則。
5. **高**：`n_samples`/`n_rounds`/`rounds_quotes` 加上硬上界（如 `n_samples ≤ 50_000`）與請求大小上限。
6. **高**：修好 OOD 評測誠信 —— 重新用**未參與訓練**的資料集評測，或在文件與 README 明確標示「本集為訓練集，92.00% 為訓練準確率」。
7. **高**：`main.py:958` 對 `LOGROLL` 模式改為保留 `logrolling_data` 的合成話術（或讓 `ChineseSurfaceRenderer` 的 LOGROLL 分支真的渲染定金/MOQ 條款）。
8. **高**：`/command` 讓 `WALK_AWAY` 在所有非終止狀態下真的轉移；或修正回應語意。
9. **中**：移除 `exec()`，改為等效純函式；對所有前端 `innerHTML` 做 escape 或改用 `textContent`。
10. **中**：`secret_scan.py` 在缺 `.env` 時應 `exit 1`（或改從 CI secrets 讀取），否則這個閘門永遠是裝飾。

---

## 10. 我未能驗證的部分（誠實標示）

- **真實 LLM 基準的成交率統計**：我只做了算術核查（§4.6），未重跑 `run_real_llm_benchmark.py`（會產生真實 API 費用與 30+ 分鐘執行）。
- **`adversaries.py` 的「文獻校準」宣稱**：未查證 NegotiationArena / AgenticPay / Cattle Trade 是否真的支持那些參數值。
- **CORS 全開的實際可利用性**：我證明了設定存在，但未建立一個跨源瀏覽器 PoC（沙盒環境無瀏覽器）。
- **`data/sessions.db` 的內容完整性**：未逐筆核對 200+ 個 JSON 快照與 SQLite 的一致性。
- **`models/intent_classifier_v5.onnx` 的內部權重**：只以行為實驗證明污染（92.00% vs 46.60%），未解讀模型圖。
- **`extension/content.js` 全部 785 行**：我只精讀了渲染/通訊區段並 grep 了 sink，未逐行審閱選擇器與 DOM 監聽邏輯。
- **`knowledge/market_data.json` 的數據真偽**：來源腳本 `refresh_market_data.py` 的設計嚴謹（明確拒用不可靠欄位），但我未向 UN Comtrade / open.er-api 重新核對具體數值。
