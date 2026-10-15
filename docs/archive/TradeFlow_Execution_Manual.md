> [!WARNING]
> **[SUPERSEDED] 本文件已封存 (Archived Document)**
> SUPERSEDED by TARGET_ARCHITECTURE_AND_ROADMAP.md (2026-10-14). Historical execution manual for Plan B.
>
> 現行唯一權威規格：[TARGET_ARCHITECTURE_AND_ROADMAP.md](../../TARGET_ARCHITECTURE_AND_ROADMAP.md)（架構）
> 與 [README.md](../../README.md)（入口）。本文件僅供歷史追溯，**請勿據此開發**。

---

# [TOOL] TradeFlow AI — 逐條執行手冊（Part B，配合 Plan B 定稿）

> 配套策略文件：[TradeFlow_PlanB_Final.md](TradeFlow_PlanB_Final.md)（v2.1）
> 今天 2026/10/13，離線上截止 ~5.5 天。本手冊每個動作都有驗收閘門（Gate）。
> 原則：**今天只解最大風險（算力 + workspace），其餘按天推進，每天都有可交付物。**

---

## 0. 研究充分性判斷（開始前先確認）

| 項目 | 狀態 | 來源 |
|---|---|---|
| Evolus API / building blocks | [PASS] 已核對官方開發頁 | evolus.ai/en/developers |
| Evolus 五項硬性要求 + 雙投策略 | [PASS] 已核對 | lablab 賽制頁 + Evolus blog |
| vLLM on ROCm 正確 image / parser | [PASS] 已核對 | ROCm vLLM doc + vLLM tool-calling doc |
| AMD $100 credits 流程 | [PASS] 已核對 | developer.amd.com/ai-developer-program |
| 引擎數學 + 誠實 GPU 數字 | [PASS] 已實測 | benchmark_negotiation_v2.py（桌面） |
| 商業價值錨點 | [WARN] 部分：行業 5–10% 基準確認，真實 HS6601 價需 Day 2 拉 | trademap.org / comtradeplus |
| Evolus agent 能否掛自訂外部 tool | [WARN] 未確認（不依賴，app 驅動拓撲已繞過） | — |
| 確切提交截止時間 | [WARN] 賽制頁寫 To be announced，去 lablab 專案頁確認 | — |
| MI300X 對免費會員是否可用 | [WARN] 未確認（Fireworks 備案兜底） | — |

**結論**：核心技術與賽制已足夠開始；剩 3 個「[WARN]」都不阻塞開發（有備案）。可以照本手冊動手。

---

## 1. 今天 Day 0/1（10/13）— 解最大風險：算力 + workspace

### 1.1 申請 AMD 算力（最長路徑，先做）
1. 開 https://developer.amd.com/ai-developer-program/ → **Join**（免費）→ 完成 profile 到 100%。
2. 在 Member Perks 領取 **$100 AMD Developer Cloud credits**。
3. **預期 2–3 個工作天核准** → 這之後才能開 MI300X droplet。記錄申請時間。
> 兜底：同時註冊 Fireworks AI（見 1.3），不依賴 AMD credits 也能先把整條鏈跑通。

### 1.2 拿 Evolus event link + 建 workspace（15 分鐘）
1. 開 https://evolus.ai/en/hackathon → 點 kickoff 公布的 **event link**。
2. 用 GitHub / Google 登入 → 建立 team workspace（**只能從 event link 建**，自己先建的不算）。
3. Settings 建立 **Application API key** → 抄下 `EVOLUS_API_KEY` 與 `EVOLUS_APP_ID`。
4. 到 "Your models" 掛一個 OpenAI-compatible endpoint（先用 Fireworks，credits 到後換 vLLM）。

### 1.3 Fireworks 備案（30 分鐘，讓鏈先動）
1. 註冊 https://fireworks.ai → 拿 API key。
2. 選模型 `accounts/fireworks/models/qwen3-32b` 或 `qwen2.5-14b-instruct`（Fireworks 有 Qwen 系列）。
3. 在 Evolus "Your models" 填：`https://api.fireworks.ai/inference/v1` + key。
> 這步完成後，Evolus agent 就能用 AMD 相容的開源模型（Fireworks 託管在 AMD 上）→ Evolus track 資格先達標，AMD credits 一到再換成自己 vLLM。

### 1.4 本機前置（30 分鐘）
```powershell
# 裝 Docker Desktop（官網下載安裝，重啟）
# 設 git identity（本機目前無）
git config --global user.name "你的名稱"
git config --global user.email "你的信箱"
# 建公開 repo：github.com/<you>/tradeflow-ai （public）
```

### 1.5 開 MI300X droplet + 部署 vLLM（credits 核准後，~1 小時）
```bash
docker pull rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0
docker run -d --name vllm --device /dev/kfd --device /dev/dri --network=host \
  --ipc=host --group-add=video --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v $HOME/.cache/huggingface:/app/models -e HF_HOME=/app/models \
  rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0 \
  vllm serve Qwen/Qwen2.5-14B-Instruct --port 8000 \
  --tool-call-parser hermes --enable-auto-tool-choice \
  --api-key $YOUR_KEY --gpu-memory-utilization 0.9 --max-model-len 32768
# 驗證
curl http://<droplet-ip>:8000/v1/models
```
> 保護 endpoint：務必 `--api-key`；對外只用 SSH tunnel 或反向代理，別裸奔。

**Gate 1**：`/v1/models` 回 200 且能對模型發一次 chat。否則不進 Day 2。

---

## 2. Day 2（10/14）— Evolus 通路 + 貝氏先驗 + 真錨點

### 2.1 Evolus REST client（Python，`src/evolus/client.py`）
```python
import os, requests
BASE = "https://api.evolus.ai"
H = {"X-ApiKey": os.environ["EVOLUS_API_KEY"],
     "X-Application-Id": os.environ["EVOLUS_APP_ID"]}

def execute_workflow(wf_id, input_text, file_path=None, callback=None):
    files = {"input": (None, input_text)}
    if file_path: files["file"] = open(file_path, "rb")
    if callback: files["callbackUrl"] = (None, callback)
    r = requests.post(f"{BASE}/api/v1/workflows/{wf_id}/execute",
                      headers=H, files=files)
    return r.headers.get("X-Workflow-Execution-Id"), r.status_code

def get_models():  # agent chat 用
    return requests.get("https://api.evolus.ai/v1/models",
                       headers={"Authorization": f"Bearer {os.environ['EVOLUS_API_KEY']}"}).json()

def run_email_tool(notes, reply_to=None, tone="commercial"):
    body = {"text": notes, "options": {"mode": "write" if not reply_to else "reply", "tone": tone}}
    if reply_to: body["secondaryText"] = reply_to
    r = requests.post(f"{BASE}/api/v1/ai-tools/email/run", headers=H, json=body)
    return r.json().get("output")
```

### 2.2 在 Evolus UI 建 workflow：email-in → extractor → callback
- 觸發：incoming email。步驟 1：Document Extractor（PDF/圖片 → JSON，欄位：product/qty/target_price/moq/compliance）。步驟 2：webhook 打到我們 container 的 `/webhook/evolus`。
- 用 `GET /executions/{id}` 輪詢結果（或等 callback）。

### 2.3 貝氏先驗 + 真錨點
- `src/engine/model.py`：定義 4 對手類型（reserve/urgency/bluff/conc_rate），見 Plan §6。
- 去 https://www.trademap.org/ 或 https://comtradeplus.un.org/ 拉 **HS 6601（雨傘）** 真實 FOB 價區間，存 `seed_data/price_anchor.json` 當 prior 校準。

**Gate 2**：一封測試信進 workflow → 抽出 JSON → 我們 app 印出 4 類供應商底價分布圖（matplotlib 存 png）。

---

## 3. Day 3（10/15）— 引擎核心（決策心臟）

### 3.1 `src/engine/solver.py`（以 benchmark_negotiation_v2.py 為骨架）
- `strategy_grid()`：40 開盤 × 12 讓步曲線。
- `solve(n_samples, opens, scheds)`：向量化評估 → 回 `exp_margin, exp_break, best`。
- `bayes_update(prior, observed_offer)`：供應商回價後更新底價後驗（conjugate / 簡單貝氏）。
- **帕累托選擇**：`best = argmax(exp_margin − λ*exp_break)`，`λ` 風險滑桿。

### 3.2 MI300X 批量版
- CPU 版用 numpy（已實測）。GPU 版：若裝得了 cupy（`cupy-cuda`/ROCm 版 `cupy-rocm`）就換陣列；否則 torch on ROCm。
- 同一份 `solve()`，陣列類型抽成參數，CPU/GPU 共享邏輯。

### 3.3 自動雨傘案例串
- `src/app/demo_data.py`：預錄供應商回價序列（穩定 demo 用）。
- `src/cli.py`：`python -m src.cli --spec umbrella` 跑端到端，印建議 + 存信念圖。

**Gate 3**：引擎 CPU 版跑通，輸出 首輪報價 / 讓步曲線 / 期望邊際 / 破局率；雨傘案例能端到端。

---

## 4. Day 4（10/16）— 穩健性 + 容器化 + 提交骨架

### 4.1 穩健性測試 `src/engine/robustness.py`
- 用**沒擬合**的對手模型跑同策略 → 報 regret vs 固定 5%/輪基準。
- prior ±50% 敏感度 → 期望成交價區間。
- 輸出一段文字 + 圖，供 Demo Wow #3。

### 4.2 benchmark 定稿 + 截圖
- `src/engine/benchmark.py`：跑 20k/50k 樣本，記 CPU 向量化 / 純 loop / MI300X 三組，存 `results/bench.json` + 截圖。
- 截圖：`rocm-smi`、`vllm` 啟動 log、`bench.json` 表格。

### 4.3 容器化 + 提交骨架
- `Dockerfile`：`python:3.12-slim` → `pip install -r requirements.txt` → `COPY src` → `CMD uvicorn src.app.main:app`。
- `docker-compose.yml`：app + （可選）vLLM sidecar 指向 AMD droplet。
- `README.md`：一個指令 `docker compose up` + seed 資料，無付費依賴。
- `.env.example`：所有 key 走環境變數，絕不 commit 真 key。
- push public repo。

**Gate 4**：README 一個指令跑起來；容器能 build；`tests/test_engine.py` 綠燈。

---

## 5. Day 5（10/17）— Demo + Pitch + 提交（上午交）

### 5.1 錄 3–5 分鐘 demo（腳本見 Plan §7）
- Wow#1 信念更新視覺化 → Wow#2 CPU 46s vs MI300X 0.4s → Wow#3 穩健性 overlay。
- 預錄 supplier 回覆 + 離線 GPU 截圖兜底，現場只跑穩定段。

### 5.2 Pitch deck（8–10 頁）
1. 問題（採購議價靠經驗、缺量化）
2. 一句話方案（貝氏 + MI300X 批量搜尋）
3. 為何不是聊天機器人（命中「LLM 不能當唯一預測法」）
4. 架構圖
5. Demo 三個 wow
6. 商業價值（100 萬訂單省 5% = 5 萬）
7. AMD 技術（MI300X 真在算）
8. Evolus 整合（4 blocks）
9. 穩健性（regret）
10. 下一步（CFR + 真實 log）

### 5.3 提交（lablab 專案頁）
- 填 title/description、Selected challenge = **Reinvent Commerce + Evolus track**、tech tags、cover image、demo video、pitch、public repo、containerized app、README。
- **上午就交**，別卡 10/18 最後一刻。確認截止時間（專案頁）。

**Gate 5**：提交完成 + 錄影上傳 + 收到確認。

---

## 6. Day 6（10/18）— 緩衝 / 現場 showcase
- 若錯過 10/17：今天最後補交。
- 現場（義大利三城）需事先申請批准，旅費自付，非必要。

---

## 7. 文件樹（建立 repo 時照此）
```
tradeflow-ai/
├── README.md
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── seed_data/price_anchor.json
├── src/
│   ├── engine/{__init__,model,bayes,solver,robustness,benchmark}.py
│   ├── evolus/client.py
│   ├── app/{main.py,demo_data.py}
│   └── cli.py
└── tests/test_engine.py
```

## 8. requirements.txt（初版）
```
requests>=2.31
numpy>=1.26
matplotlib>=3.8
fastapi>=0.110
uvicorn>=0.29
# 可選 GPU: cupy-rocm (或 torch rocm build) — 裝不上就走 numpy CPU
```

## 9. 立即動作總檢（今天）
- [ ] 申請 AMD ADP + $100 credits（**最長路徑**）
- [ ] Evolus event link 建 workspace + API key
- [ ] Fireworks 備案（先讓鏈動）
- [ ] 裝 Docker Desktop + git identity + 建 public repo
- [ ] （credits 到後）MI300X + vLLM + hermes 驗證 `/v1/models`

---
*執行手冊 v1 · 2026/10/13 · 配合 Plan B 定稿 v2.1*
