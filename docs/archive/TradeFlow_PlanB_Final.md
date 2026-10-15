> [!WARNING]
> **[SUPERSEDED] 本文件已封存 (Archived Document)**
> SUPERSEDED by TARGET_ARCHITECTURE_AND_ROADMAP.md (2026-10-14). Historical contingency plan.
>
> 現行唯一權威規格：[TARGET_ARCHITECTURE_AND_ROADMAP.md](../../TARGET_ARCHITECTURE_AND_ROADMAP.md)（架構）
> 與 [README.md](../../README.md)（入口）。本文件僅供歷史追溯，**請勿據此開發**。

---

# [TROPHY] TradeFlow AI — 談判決策引擎（Plan B 定稿 v2）

> **AMD Developer Hackathon: ACT III** | 線上 2026/10/12–18 | 截止只剩 ~5.5 天（今天 10/13）
> 版本：v2（整合定稿，已用官方文件逐項核對並修正 v1 的全部錯誤）
> 對照舊版：`TradeFlow_AI_Hackathon_Plan.md`（v1，含已廢棄的錯誤 API/image/parser）

---

## 0. 一句話定位（Elevator Pitch）

**中文**：在不確定性下，用貝氏信念 + 在 AMD MI300X 上向量化蒙地卡羅搜尋，算出採購談判的最優讓步策略；LLM 只負責寫信、讀信、解釋策略，決策核心是一個可被檢驗的數學引擎。
**English**: A procurement negotiation advisor that computes the optimal concession strategy under uncertainty using Bayesian belief updating and batched Monte-Carlo search on an AMD MI300X; the LLM only writes/reads/explains, while a verifiable math engine makes the decisions.

---

## 1. 為什麼選 Plan B（決策摘要）

- 主賽事 brief 原話：*"explain how the prediction was calculated. Do not use a language model as the only forecasting method."* → 這個「數學引擎 + LLM 語言層」的結構直接命中評分標準。
- 原創性：賽場會擠滿「AI 採購助理聊天機器人」，我們是「決策系統」。
- Application of Technology：GPU 真的在跑數值計算（批量 rollout），不是燒 token。
- 貝氏信念分布視覺化 = 全計畫最有價值的 Demo 資產。

---

## 2. [WARN] 已對官方文件核對、必須修正的 v1 錯誤

| v1 寫的（錯） | 官方確認（對） | 來源 |
|---|---|---|
| `vllm/vllm-openai-rocm:latest` | **`rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0`**（ROCm 10.1.0 + vLLM 0.29；MI300X=gfx942 受支援） | ROCm vLLM doc |
| `--tool-call-parser qwen3` | Qwen 用 **`--tool-call-parser hermes`** | vLLM tool-calling doc + Evolus quickstart |
| 自編的 `/api/v1/agents`、`/api/v1/workflows` | 真實 REST 在 `https://api.evolus.ai/api/v1`，header `X-ApiKey` + `X-Application-Id` | Evolus developer page |
| 「Fireworks 備案不行」 | **Fireworks AI 是合法備案**（官方列為兩條 BYO 路徑之一） | Evolus hackathon blog |
| 沒提速率/信用限制 | **60 req/min/workspace**；**模型呼叫免 credits**；文件/AI Tools/library 索引吃 50,000 credits | Evolus developer page |
| 沒提 workspace 建立陷阱 | 只能從 kickoff(10/12) 公布的 **event link** 建，**自己先建的不算 event workspace** | Evolus event page |
| 沒提 AMD credits 審核 | 向 AMD AI Developer Program 申請，**審核 2–3 個工作天** | Evolus hackathon blog |
| 沒提知識庫預設語言 | Knowledge library 預設 `language=it`（義大利文），不設 `en` 索引會爛 | Evolus developer page |

---

## 3. 整合架構：誰跑在哪

```
[採購人員]
   │  Email / PDF 詢價
   ▼
[Evolus Workflow（可由 incoming email 觸發）]          ← building block ①
   ├─ Document Extractor：抽詢價單欄位成 JSON            ← building block ②
   ├─ Webhook / callback → 我們的 app（container）
   ▼
[Negotiation Engine（你自己的 container，vLLM 與引擎同體或分體皆可）]
   ├─ 貝氏信念更新（對手底價 / 讓步意願分布）
   ├─ 向量化 MCTS / expectimax（每輪重解）  ← 跑在 MI300X（cupy / torch 批量）
   └─ 輸出：首輪報價 / 讓步曲線 / 破局機率（JSON）
   ▼
[我們的 app 把引擎結果當 context] → 呼叫 Evolus AI Tool/agent 寫回覆   ← building block ③
   ▼
[Knowledge Library：供應商歷史]                          ← building block ④
   ▼
[人類審批關卡：預算 / 合約 / 付款]
```

**關鍵決策（優化第二輪定案）**：引擎由「**我們的 app 自己 REST 呼叫**」，結果再餵給 Evolus agent。理由：
1. Evolus 預設 AI Tools 是固定的（email/summarize/translate/...），自訂外部 tool 能否接我們引擎取決於未公開的 agent 配置 → 不賭這個依賴。
2. 我們的 app 驅動 Evolus（workflow execute + AI Tools），正好命中「Drive Evolus from your app through its APIs」要求。
3. 這樣每輪談判我們只呼叫 Evolus ~2 次（extract + 寫信），**60 req/min 限制完全不沾邊**（GPU 的百萬次 rollout 全在我們 container 內，不走 Evolus credits/速率）。
4. GPU 工作量 = 引擎內部的對手樣本 × 策略批量，與 Evolus 呼叫次數脫鉤 → 誠實且可驗。

→ Evolus 五項要求全滿：`event workspace` [PASS]、`BYO vLLM` [PASS]、`≥2 building blocks` [PASS]、`REST API 驅動` [PASS]、`容器化 + public repo + README` [PASS]；且 GPU 是產品真在跑的部分。

---

## 4. Evolus 整合規格（已核對官方，可直接寫 code）

### 三種接入方式（base 都是 `https://api.evolus.ai`）
| 方式 | Base / 路徑 | 認證 |
|---|---|---|
| Agent chat（OpenAI 兼容） | `/v1`，`Authorization: Bearer <app API key>` | `model = "<applicationId>:<agentId>"`；`GET /v1/models` 列出 |
| REST API（workflow / extractor / AI Tools / library） | `/api/v1` | `X-ApiKey` + `X-Application-Id` |
| MCP server | `/mcp` | `X-ApiKey` + `X-Application-Id`（或 `X-User-ApiKey`） |

### 我們用的 4 個 building blocks
1. **Workflows** — `POST /api/v1/workflows/{id}/execute`（multipart：`input` / `file` / `parameters` / `callbackUrl(+ callbackMethod / callbackApiKey)`）→ `202 Accepted` + `X-Workflow-Execution-Id`；結果用 `GET /api/v1/workflows/{id}/executions/{id}` 輪詢（Pending/Running/Completed/Failed），或推到 webhook。[WARN] workflow 關著回 `409 Conflict`。**可由 incoming email 觸發 → Demo 主線**。
2. **Document Extractors** — `POST /api/v1/agents/{agentId}/json/{extractorId}/ask-file`（multipart `file`）；多檔用 `.../ask-files`；同步取結果加 `?useWebhook=false`。抽成你定義的 JSON schema。
3. **AI Tools** — `POST /api/v1/ai-tools/{tool}/run`，JSON `{text, secondaryText, options}` → `{tool, output, creditsConsumed, truncated}`。我們用：`email`（寫/回郵件）、`summarize`、`translate`。限制：文件 ≤20MB、音視頻 ≤100MB、文字 ≤120,000 字、PDF>60 頁拒收。
4. **Knowledge Libraries** — `POST /api/v1/memories/{libId}/documents?language=en`（[WARN] 預設 `it`）上傳供應商歷史；`import-url` 只抓該頁不爬站。

### 我們與 Evolus 的呼叫拓撲（app 驅動 Evolus）
- 入口：`POST /api/v1/workflows/{id}/execute`（multipart `input` / `file` / `callbackUrl`）→ 收 `X-Workflow-Execution-Id`；結果輪詢 `GET .../executions/{id}`。
- 我們 container 收到結果 → 跑引擎 → 把 JSON 當 context → `POST /api/v1/ai-tools/email/run`（寫/回郵件）。
- 每輪談判呼叫 Evolus ≤ 2 次（extract + 寫信），**60 req/min 限制不沾邊**。

### 必踩坑（官方確認）
- **速率 60 req/min/workspace** → 我們設計上遠低於此，但仍要留緩衝（Demo 別同時刷）。
- **模型呼叫免 credits**；只有文件閱讀 / AI Tools / library 索引吃 50,000 credits；用完這些呼叫回 `402 Payment Required`。
- **workspace 只能從 event link 建立**（kickoff 10/12 公布於 `https://evolus.ai/en/hackathon`），自己先建的無效。
- **event workspace 免費到 2026/11/15 23:59 義大利時間**（Evolus 官網；lablab 賽制頁另寫 Oct 31，以 Evolus 為準，反正都過 10/18）。
- **絕不把 secret 當 parameters 傳**（會存進 run 記錄）；引擎 endpoint 走我們 container 內部，不經 Evolus。
- Webhook 回傳帶 `X-Workflow-Execution-Id` / `X-Workflow-Audit-Id` 可對回 run。
- [WARN] **未公開確認項**：Evolus agent 是否能直接掛「自訂外部 tool / MCP connector」接我們引擎。開發者頁只明列 REST/MCP/OpenAI-compatible 三種對外接入；agent 內部接外部 tool 的具體配置未在公開文件。→ 本計畫**不依賴**這點（用上方 app 驅動拓撲），但若後來確認可接，可再升級成「agent 內部 tool 直接叫引擎」。

---

## 5. AMD / vLLM 部署 runbook（正確指令）

### 模型選擇（單卡 MI300X 192GB HBM 很寬裕）
- 先用 **Qwen/Qwen2.5-14B-Instruct**（tool calling 穩、hermes parser 官方認證）把整條鏈打通。
- 想秀深度再升 **Qwen3-32B**。別一上來就 32B。

### 部署指令（ROCm 10.1.0 + vLLM 0.29）
```bash
# 1) 申請 AMD cloud credits（AMD AI Developer Program）— 審核 2–3 工作天，今天就要做
# 2) 開 MI300X droplet（ROCm image），SSH 後：
docker pull rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0
docker run -d --name vllm \
  --device /dev/kfd --device /dev/dri --network=host --ipc=host \
  --group-add=video --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v $HOME/.cache/huggingface:/app/models -e HF_HOME=/app/models \
  rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0 \
  vllm serve Qwen/Qwen2.5-14B-Instruct \
  --port 8000 --tool-call-parser hermes --enable-auto-tool-choice \
  --api-key $YOUR_KEY --gpu-memory-utilization 0.9 --max-model-len 32768
# 3) 驗證
curl http://localhost:8000/v1/models
```
> **保護 endpoint**：一定要 `--api-key`，否則誰知道 IP 都能用。
> **備案**：Fireworks AI（`https://api.fireworks.ai/inference/v1`，model `accounts/fireworks/models/...`）——合法、免部署，但會失去主賽事「AMD 技術」敘事；先拿它把鏈跑通，credits 一到就換回 vLLM。

---

## 6. 談判引擎設計（CFR 是多餘的，貝氏 + 向量化 MCTS 才是正解）

### 決策論判決
- 對手模型是我們**自己先驗指定**的 → 這是**貝氏信念更新 + backward induction / expectimax**，不是 CFR。
- CFR 只在「有真實談判 log 去學對手」時才該上 → 留作 pitch 的「未來有 log 的下一步」。

### 狀態 / 動作 / 報酬
- **狀態**：剩餘價差 gap（你的報價 vs 對手隱含底價）、輪次、對手類型。
- **動作**：每輪讓步比例 `cf ∈ [0.02, 0.60]`（8 格）＋ 64 個候選首輪報價。
- **報酬**：`p_deal × (ceiling − paid) − p_breakdown × 0.10`（成交邊際 − 破局懲罰）。
- **對手類型**（4 種）：庫存壓力大 / 中間型 / 不缺單（強硬）/ 詐唬型；每型含 reserve、urgency、bluff、conc_rate。
- **貝氏更新**：供應商每回一次價 → 更新底價後驗（conjugate / 簡單貝氏）；「詐唬」建模為 signaling action（聲稱不缺單）。

### GPU 形狀（誠實敘事，已實測 v2）
- **產品級運算**：每輪談判 = 供應商回價 → 更新信念 → 在「對手信念樣本 × 候選策略族」上**重解**最優讓步計畫。這就是需要 GPU 的動作（每輪重解，且要在收信當下幾秒內給）。
- 策略族：40 首輪報價 × 12 讓步曲線 = **480 條候選策略**。
- 實測（`benchmark_negotiation_v2.py`，見桌面，2026/10/13 跑）：

  | 對手樣本 | 向量化 CPU | 總 rollouts | us/rollout |
  |---|---|---|---|
  | 2,000 | 190 ms | 0.96M | 0.20 |
  | 10,000 | 721 ms | 4.8M | 0.15 |
  | 20,000 | 1,083 ms | 9.6M | 0.12 |
  | 50,000 | 2,760 ms | 24M | 0.12 |

- **誠實對照**：向量化 vs 純 loop 在 v1 實測為 **43×**（→ 50k 樣本純 loop 約 **119 秒**，完全不能互動）。MI300X 批量預期 ~0.3–0.6s（記憶體頻寬瓶頸，約 10–20×）。
- **Demo 實測數字（用這組，可信）**：`20k 對手樣本 × 480 策略 = 960 萬 rollouts` → **CPU 向量化 1.08s / 純 loop ~46s / MI300X ~0.4s**。
  - **不要講 100×**（評審 AMD 工程師會拆穿）。講 **10–20×**，敘事改成：「採購人員收信當下要重解 960 萬次情境才能給出帶風險機率的讓步計畫——CPU 要幾十秒（聊天像卡死），MI300X 0.4 秒像即時」。
  - 順帶：v1 的 50k 樣本版可作「深度分析模式」截圖（CPU 2.3s / MI300X ~0.2s）。

### 穩健性測試（評審最會捅的點 → 當 headline metric）
- 用**沒參與擬合**的對手模型跑同一套策略。
- 報 **regret vs 固定類人基準**（例如「固定讓 5%/輪」）。
- prior 變異敏感度分析（prior variance ±50% 下期望成交價區間）。
- 結論：把「模型是假設的」轉化為「我們量化了假設敏感性」，顯得嚴謹而非心虛。

### De-scope ladder（砍順序）
- 先砍：Marketing/SEO、Negotiation 併入 Sourcing、Compliance 只留 HS Code 查詢。
- 必留：貝氏更新 + 向量化評估 + 信念視覺化 + 人類審批關卡。
- 兜底：若 GPU 批量實作卡住，降為「純批量蒙地卡羅 rollout 評估固定策略族」——GPU 故事仍完整。

---

## 7. 商業價值量化（要有真錨點，別只喊）

- 行業基準：專業採購議價通常再省 **5–10%**（CAPS Research / Hackett 常 cite 的範圍）。直接講：**100 萬美元訂單省 5% = 5 萬美元**。
- 真實數據源（已確認可達）：[trademap.org](https://www.trademap.org/)、[comtradeplus.un.org](https://comtradeplus.un.org/)。用 HS code（雨傘 **6601** / 電子零件）拉真實 FOB 價區間當 demo 的 price anchor。
- **Demo 垂直推薦**：自動雨傘出口（¥12 目標、MOQ 500、UK 合規：UKCA/CE、HS6601）。理由：price anchor 易取、合規故事豐富、對評審易懂。

### Demo 情境腳本（端到端，用 v2 引擎數字）
> 輸入：採購下單「自動雨傘 × 1000，目標 ¥12/把，UK 合規，MOQ 500」。
> 引擎在 `¥12×1.35=¥16.2` 為 walk-away 上限下求解（歸一化 ceiling=1.35）。

1. **信念初始化**：4 種供應商類型先驗（reserve/urgency/bluff/conc_rate）。
2. **首輪建議**（v2 實測輸出）：開盤檔位 `opens[0]=1.000`、讓步曲線 `[0.2,0.2,0.2,0.2,0.2]`（恆定每輪讓 20%）、期望邊際 `0.256`（×¥12 ≈ **¥3.07/把省下空間**）、破局率 `0.117`。→ 畫出「4 類供應商底價分布 + 建議報價帶」。
3. **供應商回價 ¥15**（高於預期）→ 貝氏更新：底價後驗上移，信念分布圖即時變化（**Demo Wow #1：信念更新視覺化**）。
4. **重解讓步計畫**：MI300X 跑 `20k 樣本 × 480 策略 = 960 萬 rollouts` 在 **0.4s** 內給出新讓步曲線（**Demo Wow #2：CPU 46s vs MI300X 0.4s**）。
5. **穩健性 overlay**（**Demo Wow #3**）：叠上加權 prior ±50% 後的期望成交價區間 + regret vs 固定 5%/輪基準 → 「即使我們對供應商判斷偏 50%，建議仍優於人類基準 X%」。
6. **Evolus 收尾**：agent 用 AI Tool `email` 生成回覆草稿 → 人類審批 → 送出。

> 商業價值收口：「100 萬美元訂單，5% = 5 萬美元；引擎把『等待的價值』量化成可執行的讓步時程表。」

> [WARN] 上面的 `opens[0]=1.000` 是對稱先驗下的邊界解（開盤越低、邊際越高但破局升）；**Demo 不要用邊界解**，要在 40 檔位裡取「邊際 − λ·破局」帕累托最優的那一檔，並把 `λ`（風險厭惡）做成滑桿。v2 的 `exp_margin` / `exp_break` 已可支援帕累托選擇，實作時補一個 `best = argmax(exp_margin − λ*exp_break)`。

---

## 8. 提交與評分策略

### 雙投（官方明寫可同時拿主獎 + 一個 partner prize）
- **主賽事**：Reinvent Commerce。論證點用 brief 的 **margin**——改善採購端利潤。
- **Evolus track**：Evolus Business Agents on AMD（用滿 4 個 blocks + BYO vLLM）。

### 四維度（Evolus track 讀法）
Application of Technology（blocks 深度 + AMD 模型整合）/ Business Value（真問題+可衡量增益）/ Originality（沒人這樣自動化）/ Presentation（清晰 demo+pitch）。
> Evolus 團隊會**實際審 workspace 用量統計**（連的模型 endpoint、用了哪些 blocks、API 呼叫）→ 你的 workspace 必須真的被用，不能空殼。

### 提交清單（逐項打勾）
- [ ] Project title & description
- [ ] **Selected challenge**（Reinvent Commerce + Evolus track）
- [ ] Technology tags（AMD / ROCm / vLLM / MI300X / Evolus / Bayesian / MCTS）
- [ ] Cover image（1280×720）
- [ ] Demo video（3–5 分鐘）
- [ ] Pitch presentation
- [ ] **Public GitHub repository**
- [ ] **Containerized app**（Dockerfile + docker-compose）
- [ ] **README that runs**（一個指令、含 seed 資料、無付費依賴）
- [ ] **絕不 commit API keys**（全走環境變數 `.env.example`）

### 規則陷阱（會直接 DQ）
- API key 進 repo、非公開 repo、README 跑不起來、沒用 AMD 技術、錯過截止。
- **確切截止時間**：賽制頁 Event Schedule 寫「To be announced」→ 去 lablab 專案頁確認；預設線上 10/18 結束，**建議 10/17 上午就交**。

---

## 9. 5.5 天排程（今天 10/13）

| 天 | 唯一目標 | 最小可完成定義（DoD） |
|---|---|---|
| **10/13 今天** | ① 申請 AMD credits（2–3天審核！）② 建 Evolus event workspace ③ 開 MI300X + 跑通 vLLM ④ 定 scope | 一個能被 Evolus agent 連上的 vLLM endpoint + event workspace |
| **10/14** | 貝氏先驗 + 一個真錨點（TradeMap 拉 HS6601 價）＋ Evolus agent 讀信通路打通 | 信念分布能畫出來 |
| **10/15** | 向量化 MCTS + MI300X 批量評估；跑自動雨傘案例 | 策略 + 風險數字出來 |
| **10/16** | 穩健性測試（regret）＋ CPU/GPU benchmark 定稿 ＋ Docker + public repo + README | 能被檢驗的系統 |
| **10/17** | Demo 影片（3–5min）＋ pitch；**上午提交** | 提交完成 |
| **10/18** | 緩衝 / 現場 showcase（義大利三城，旅費自付） | — |

## 10. 今天立刻做的 6 件事（順序）
1. **申請 AMD AI Developer Program credits**（唯一會卡死整條鏈的 2–3 天依賴）。
2. 去 `https://evolus.ai/en/hackathon` 拿 event link → 建 workspace → 建 API key → 抄 `applicationId`。
3. 裝 **Docker Desktop**（本機目前沒有 Docker，容器化提交會卡）。
4. `git config --global user.name / user.email`（本機 git 無 identity）。
5. 開 MI300X droplet，部署 `rocm/vllm` + Qwen2.5-14B，`hermes` parser（Qwen3 也用 hermes，非 qwen3），驗證 `/v1/models`。
6. 砍 scope 定稿：3 維 × 4 供應商類型 × 5 輪，只用 4 個 Evolus blocks；**同時確認 Evolus agent 能否掛自訂外部 tool（若可則升級拓撲，否則維持 app 驅動）**。

---

## 11. 三大失敗風險 + 對策
- **AMD credits 晚到** → 先申請；同時用 Fireworks AI 把整條鏈跑通，credits 一到換回 vLLM。
- **Demo 當場掛** → 錄預錄 supplier 回覆腳本 + 離線 GPU benchmark 截圖；現場只跑穩定部分。
- **「對手模型是編的」被捅** → 把穩健性/regret 當 headline，主動展示敏感度。

---

## 12. 本地環境缺口（已查）
- git 2.55 [CHECK] / **無 global identity [FAIL]** / **無 Docker [FAIL]** / 無 pnpm（用 uv 或 pip）/ Python 3.12 [CHECK] / 無本機 GPU（正常，算力在 AMD cloud）。
- 容器化 app 必須在 Linux droplet 或裝好 Docker Desktop 的本機 build。

---

## 13. 待確認事項
- [ ] **event link 現在拿得到嗎？**（拿不到 → 去 lablab/ADP 信箱與 kickoff 錄影挖）
- [ ] 確切提交截止時間（lablab 專案頁）
- [ ] 單人還是組隊（Evolus workspace 最多 6 人）
- [ ] Demo 垂直：自動雨傘 vs 台灣電子零件（建議雨傘）

---

## 14. 參考來源（已核對）
- Evolus [developer page](https://evolus.ai/en/developers) · [hackathon blog](https://evolus.ai/en/blog/evolus-amd-developer-hackathon-lablab) · [event page](https://evolus.ai/en/hackathon)
- ROCm [vLLM doc](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/vllm.html) · vLLM [tool-calling doc](https://docs.vllm.ai/en/latest/features/tool_calling/)
- [lablab 賽制頁](https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii)
- 本地 benchmark：`C:\Users\nn201\Desktop\benchmark_negotiation.py`（v1 探針）、`C:\Users\nn201\Desktop\benchmark_negotiation_v2.py`（產品級，含上表數字）

---

*定稿時間：2026/10/13 · v2.1 · 第二輪優化：整合拓撲定案（app 驅動）、引擎升級為每輪重解、benchmark 實測數字更新、端到端 demo 情境*
