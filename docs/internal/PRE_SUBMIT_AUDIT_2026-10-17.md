# 提交前稽查報告 — TradeFlow AI（黑客松最後 1.5 天）

> 稽查方式：主會話實機驗證（啟動、端點、E2E、控制組對照）＋ 2 路子代理並行審查（extension、文檔一致性），
> 所有關鍵結論皆由我**獨立複驗**後才動手。修正已提交：`96360e4` → **`572031c`**。

---

## 一、最重要的一件事：repo 原本「一鍵啟動」是壞的（已修）

### 症狀（可 100% 重現）
README 與 SUBMIT.md 記載的啟動指令 `python -m src.app.main`，在任何乾淨環境下**直接崩潰**：

```
ImportError: cannot import name 'api_v1' from partially initialized module
'src.web.routes_web' (most likely due to a circular import)
```

### 根因
`main.py` 檔尾 `from ..web.routes_web import ...`，而 `routes_web.py` 又
`from ..app.main import require_api_key` —— 互相 import。

- 測試走 `from src.app import main`（**模組匯入**）→ 循環剛好解得開 → **332 項測試全綠**。
- 但 `python -m src.app.main` 時 main 的身分是 `__main__`，`sys.modules` 裡沒有
  `src.app.main` 這個鍵 → routes_web 去載入一份半初始化的 main → 拋錯。

**這是對 CI 完全隱形的破口**：測試全綠，但評審的第一步就死。

### 修法
把認證原語抽到新的 `src/app/security.py`（不依賴任何 app/web 模組），
`routes_web` 改從那裡匯入，`main.py` 以同名 re-export 維持既有介面。

### 驗證（控制組對照）
| 版本 | `python -m src.app.main` |
|---|---|
| 修復前 `96360e4` | ❌ ImportError，exit 1 |
| 修復後 `572031c` | ✅ 啟動成功、lifespan 預熱完成 |

**乾淨 clone 的完整 E2E 已通過**：`/health` 200、`/` 200、建立 session、
實際跑一輪談判 → intent `CRY_POVERTY_COST_HIKE`、建議價 ¥13.02、後驗 1.132。

- 驗證 `require_api_key` 邏輯與原版**逐行等價**（僅 `_env_mode`→`env_mode` 改名）。
- 相容別名 `SANDBOX_DEMO_TOKEN` / `webhook_token_header` 仍可解析。
- 全量測試：**332 passed + 68 subtests，零回歸**。

---

## 二、文檔誠信修正（評審會交叉核對 repo）

| 嚴重度 | 檔案 | 問題 | 已修正為 |
|---|---|---|---|
| **P0** | `LIMITATIONS` §四 | 寫「665.9 MB」卻引註 `bench.json` —— 該檔實際是 **678.1 MB**；比例「9.1 倍」對應 665.9，實際 678.1/73.24 = **9.26** | 678.1 MB、約 9.3 倍（PITCH 早已正確，此為漏改的舊值） |
| **P0** | `README:31` | 「開盤 ¥14.80 → 達成 ¥13.00，合計省 **¥18,000**」—— 全 repo 找不到任何產生這個數字的檔案（最接近的實錄是 `real_stress_test_log.json` 的 **¥13.02**）；腳註還宣稱是「Expectimax 基準模擬輸出」，但該 artefact 不存在 | 改用可追溯數字：¥17,800（= (14.80−13.02)×10,000，出處 `real_stress_test_log.json`） |
| **P1** | `README:31` | 「零回扣、**零幻覺**」為絕對宣稱，與專案自述「AST 層無法理解自然語言語義」相衝 | 改為「語言層為模板化輸出並經 AST 與正則數值雙重校驗（邊界見 LIMITATIONS §五）」 |
| **P1** | `LIMITATIONS:384,292` | 兩處殘留舊延遲「2.2 秒 / 1.1 秒」，與同檔 §6.1 的「3.3 秒 / 1.7 秒」自相矛盾（bench.json：20K→1.6825s） | 統一為 3.3 秒 / 1.7 秒 |
| **P1** | `COMPETITION_STRATEGY:55,131` | 「破局 6.7% vs **28.4%**、**+¥1.95/unit**」—— LIMITATIONS 明文標示此為**修復前已淘汰**的數字 | 我重跑 robustness 實測：增益 ≈ **+¥0.40/件**、破局 ≈ 28%，已改並註明數值隨取樣波動 |
| **P1** | `HARDCODED_DATA_AUDIT` ×2、`DEMO_VIDEO_FILMING_GUIDE` | 測試數舊值 **326** | 332 |
| **P2** | `SUBMISSION_DRAFT:20` | `[Insert Loom / YouTube / Drive link...]` 模板殘留 | 改為誠實說明（demo 影片仍待錄） |

> 我**沒有**改動已驗證一致的部分（28.8M rollout 標註、100 場 = 20×5 seed 42、
> real-LLM 348 calls / $0.108、OOD 305/195 切分、AMD 誠實揭露等），避免無謂破壞。

---

## 三、Extension（評審現場第一步）

extension 本身**無 P0**；HTTP 契約欄位、response 讀取路徑、session_id 白名單、
CORS、MV3 設定皆正確。已修：

- **P1：靜態 HTML 捏造數字**。`sidepanel.html` 在首屏直接顯示寫死的
  `live-session-001 / ¥12.00 / ¥16.20 / 1.080 / 0.095 / 4.8% / Round 1 / 5`
  與一則假 timeline —— JS 早已清洗成 `--`，但 HTML 被漏掉。評審第一眼就會看到。
  已全部清為 `--`（與 JS 渲染字串一致，HTML 結構驗證通過）。
- **P1：`MI300X MCTS` 標籤不實**。後端示範路徑實際是 `solve(use_gpu=False)` 的
  NumPy CPU；專案從未在 AMD 硬體上實測。已改為 `Expectimax + Bayesian v2`。
- **P2：** `manifest.json` 補上 `minimum_chrome_version: 114`（`chrome.sidePanel` 需求）。

### ⚠️ 我**否決**了一條子代理的建議（重要）
子代理主張 `manifest.json` 的 `"http://127.0.0.1/*"` 只匹配 port 80、會導致
「HUD 完全不注入」，並建議改成 `"http://127.0.0.1:*/*"`。

我查證 [Chrome 官方 match patterns 文件](https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns) 後確認**該建議是錯的**：

> 「端口（可選）……**默认情况下，此值会被视为通配符，其行为与 `:*` 相同**」
> 「匹配模式**会匹配所有端口**，除非明确指定了端口。」

所以現有寫法已涵蓋所有 port，`http://localhost/*` 官方更明言「matches any localhost port」。
若照建議改成 `:*` 形式，反而可能產生無效 pattern 而弄壞 manifest。
**結論：此處不需修改，維持原狀。**

---

## 四、目前狀態

- 全量測試：**332 passed + 68 subtests**，零回歸（耗時約 12.5 分鐘）。
- `verify_live.py`：**7/7 PASS**（含熔斷、手機解鎖、拍賣）。
- 拍賣端點：2.4–2.5 秒，winner 12.60、節省 22.2%。
- 中文渲染正常（先前的亂碼確認是 PowerShell 主控台解碼假象，非伺服器問題）。
- 已提交並**驗證乾淨 clone 可用**。

## 五、提交前你仍須親自完成（按阻塞排序）

1. **錄 3–5 分鐘旁白 Demo 影片**（現有僅 ~15 秒無聲）—— 照 `DEMO_SCRIPT.md`；
   務必讓 Human-in-the-loop 審批畫面入鏡。
2. **輪換兩把 Evolus API key**（開發期曾明文共用）。
3. **`python scripts/refresh_market_data.py`**（需有網路的機器；`market_anchor` 仍為舊版）。
4. `python scripts/make_pitch.py` 重跑一次，確保 pptx 與修訂後 PITCH.md 一致。
5. 平台表單：tagline、標籤、封面圖。

> 另：`debug_findings/` 與 `FINAL_DEBUG_REPORT.md` 為未入 git 的工作檔，提交後可刪。

---

## 六、最終再驗證（同日稍後、修完第二輪缺陷之後）

上面「四、目前狀態」是第二輪修復**前**的快照。修完後的實測：

- 全量測試：**370 passed + 98 subtests，EXIT=0，248 秒**（新增 `tests/test_audit_2026_10_17_fixes.py`
  9 項與 `tests/test_reset_demo_sessions.py` 5 項回歸鎖，覆蓋本輪每個修正點）。
- 套件耗時從 **14 分 23 秒 → 5 分 24 秒**：原因是 SSE 端點生命週期硬寫 300 秒，而
  Starlette 1.7 的 TestClient 會把整個串流緩衝到結束才回傳，導致每個 SSE 測試靜默等 5 分鐘。
  改為 `TRADEFLOW_SSE_MAX_SECONDS`（預設 300 不變），測試端設 1 秒；斷言未放寬。
- `verify_live.py`：7/7 PASS（重跑確認）。
- 文件中的測試數已同步為 370 / 98（README、SUBMIT、SUBMISSION_DRAFT、HARDCODED_DATA_AUDIT、
  DEMO_VIDEO_FILMING_GUIDE、DEMO_VIDEO_DIRECTOR_SCRIPT）。

## 七、測試不再污染本機示範資料

實測：跑一輪 `pytest tests` 之後，`data/sessions.db` 會多出 `TEST_PRESET_UMBRELLA`、
`TEST_PRESET_CNC_CASE`、`TEST_PRESET_BATTERY`、`async-regression-01`、
`mold-guard-nofee`、`mold-guard-quoted`、`auth-test-sess-01` —— 全部是測試留下的
真實會話（含輪次），評審一開儀表板就看見一排殘骸。`verify_live.py` 也同樣寫進
`test-session-live-01`。

修法是把隔離變成**預設**而非每支測試各自記得：

| 位置 | 修正 |
|---|---|
| `tests/conftest.py`（新增） | 每個測試模組自動拿到臨時 DB 與 store，結束後還原全域 manager |
| `tests/test_category_preset.py` | 再加上一道守衛：manager 路徑必須在 `tf_test_*` 臨時目錄下 |
| `verify_live.py` | 預設跑臨時 store（`setdefault`，尊重使用者自己的環境變數） |
| `scripts/reset_demo_sessions.py`（新增） | 清掉舊殘骸，保留 `DEMO_*`；預設 dry run，要 `--apply` 才刪 |

驗證：清理後 DB 為 3 個 `DEMO_*` / 0 turns；再跑一整輪全套測試與 `verify_live.py`，
DB 仍是 3 個 `DEMO_*` / 0 turns。另在 `.github/workflows/ci.yml` 加了一個具名步驟
跑 `tests/test_audit_2026_10_17_fixes.py`，讓回歸失敗以步驟名義出現在 CI。

## 八、介面重做（Deal Dossier）

原版面是「近黑底 + 五個圓角面板 + 霓虹綠」的金融終端預設值，跟這個產品真正的內容
（授權額度、對方報價、擬稿、簽核）沒有關係。改成採購卷宗：紙面、規線、表格化數字、
一枚紅色印章，紅色只代表授權上限與風險。

- 主角是**價格階梯**：在同一條軸上畫出 target / ceiling / 對方開價 / 引擎建議，
  刻度用整數級距。其他元素全部收斂到規線與小字。
- 版面改成「主文件（有框）+ 頁邊註記（無框）」的不對稱結構，而不是六張同尺寸卡片。
- 沒有資料時一律 `—`、標記隱藏；圖表無資料時用一句話蓋住（原本 Chart.js 會畫
  出 0–1 的假軸）；沒有報價時階梯收合。
- 新增「Copy draft」：擬稿本來就要人工貼回聊天室，之前只能手動選字。
- 擴充 HUD / 側邊欄同步同一組 token；HUD 左緣一條狀態色，決策卡改為規線清單。
- 設計理由與 token 表：`docs/DESIGN.md`。

驗證：Playwright 在 1440 / 1024 / 430 三種寬度確認無水平溢出、階梯標記不超出軸、
刻度落在軸內、無 page error；擴充以真 Chromium 載入後量測 HUD / 側邊欄的 computed
style（紙面 `#f2f2ec`、印章紅 `#a33a2a`）與實際文字；`tests/test_extension_e2e.py`
仍 24/24、全套 370 passed + 98 subtests。
- 儀表板最後一個盲點：copilot 模式不寫 `buyer_agent` 輪次，生成話術只活在 `/round`
  的 HTTP 回應裡 → 一重新整理，「Latest Generated Reply (human sends manually)」
  就變 `—`，而那正是使用者要貼回聊天室的文字。現已持久化到對手輪
  `metadata.generated_reply`，並以實際瀏覽器重新載入驗證看得到。
