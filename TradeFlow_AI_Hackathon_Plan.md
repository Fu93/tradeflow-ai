# 🏆 TradeFlow AI — 跨國貿易多 Agent 自動化系統

> **AMD Developer Hackathon: ACT III** | 2026/10/12-18 | 獲勝策略完整方案

---

## 📋 目錄

1. [專案定義](#專案定義)
2. [技術架構](#技術架構)
3. [多 Agent 設計](#多-agent-設計)
4. [每日執行計劃](#每日執行計劃)
5. [技術實現細節](#技術實現細節)
6. [評分標準對照](#評分標準對照)
7. [風險管理](#風險管理)
8. [提交清單](#提交清單)
9. [資源連結](#資源連結)

---

## 專案定義

### 目標

建立一個 **多 Agent 跨國貿易自動化系統**，涵蓋：

| 模組 | 功能 | 輸入 | 輸出 |
|------|------|------|------|
| **① 選品 Agent** | 市場分析、競爭對手監控、定價策略 | 產品需求 | 市場分析報告 |
| **② 供應商 Agent** | RFQ 發送、報價比較、協商條款 | 產品規格 | 供應商清單 + 報價 |
| **③ 客戶 Agent** | 回覆詢價、產生報價單 | 客戶需求 | 報價單 + 訂單 |
| **④ SEO/GEO Agent** | 內容生成、結構化數據、AI 搜索引擎優化 | 產品資訊 | 優化內容 + Schema |
| **⑤ 进出口 Agent** | HS code 分類、報關文件、物流追蹤 | 訂單資訊 | 報關文件 + 物流追蹤 |

### 範圍控制（關鍵！）

**不要做 90% 的業務，做一個特定產業的完整 end-to-end 流程**

**推薦利基市場**：台灣小型電子零件出口商（或你熟悉的產業）

**完整業務流程**：

```
輸入：「我需要出口 1000 個 USB-C 充電器到美國」
  ↓
① 選品 Agent → 分析市場需求、競爭對手、定價策略
  ↓
② 供應商 Agent → 發送 RFQ、比較報價、協商條款（Email）
  ↓
③ 客戶 Agent → 回覆客戶詢價、產生報價單（Email/WhatsApp）
  ↓
④ SEO/GEO Agent → 優化產品頁面、生成內容
  ↓
⑤ 进出口 Agent → 生成報關文件、追蹤物流
  ↓
輸出：完整的貿易方案（供應商選擇、報價、報關文件、物流追蹤）
```

---

## 技術架構

### 整體架構圖

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TradeFlow AI (你的 App)                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │           Evolus Workspace (Orchestrator)                      │  │
│  │  - Workflows (業務流程)                                        │  │
│  │  - Document Extractors (文件處理)                              │  │
│  │  - AI Tools (工具呼叫)                                         │  │
│  │  - Knowledge Libraries (知識庫)                                │  │
│  │  - MCP Connectors (外部系統整合)                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                           ↓                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │           vLLM on AMD Developer Cloud                           │  │
│  │  - Qwen3-32B (主模型，服務所有 Agent)                           │  │
│  │  - ROCm 優化                                                  │  │
│  │  - OpenAI-compatible API                                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 技術棧

| 層級 | 技術 | 用途 |
|------|------|------|
| **GPU 算力** | AMD Instinct MI300X | 模型推理 |
| **模型服務** | vLLM + ROCm | OpenAI-compatible API |
| **Agent 平台** | Evolus | 多 Agent 編排 |
| **應用層** | Python + FastAPI | REST API |
| **前端** | React/Next.js | 用戶介面 |
| **部署** | Docker + AMD Developer Cloud | 雲端部署 |

---

## 多 Agent 設計

### Agent 架構

| Agent | 負責 | 输入 | 输出 | Evolus Building Block |
|-------|------|------|------|----------------------|
| **Orchestrator** | 協調所有 Agent | 用戶需求 | 任務分配 | Workflow |
| **Sourcing Agent** | 選品 + 搜尋供應商 | 產品需求 | 供應商清單 | Webhook + AI Tool |
| **Negotiation Agent** | 供應商洽談 | 供應商報價 | 協商結果 | Email + Document Extractor |
| **Sales Agent** | 客戶接洽 | 客戶詢價 | 報價單 | Email + AI Tool |
| **Marketing Agent** | SEO + GEO | 產品資訊 | 優化內容 | AI Tool + Knowledge Library |
| **Compliance Agent** | 进出口處理 | 訂單資訊 | 報關文件 | Document Extractor + Workflow |

### Agent 協作流程

```
[用戶輸入]
    ↓
[Orchestrator Agent]
    ├── 分析需求 → 分配任務
    ├── [Sourcing Agent] → 搜尋供應商
    ├── [Negotiation Agent] → 協商条款
    ├── [Sales Agent] → 產生報價單
    ├── [Marketing Agent] → 優化內容
    └── [Compliance Agent] → 生成報關文件
    ↓
[輸出完整貿易方案]
```

---

## 每日執行計劃

### Day 0（今天 10/11）— 準備階段

| 時間 | 任務 | 產出 |
|------|------|------|
| 2h | 定義業務流程圖 | 完整流程圖 |
| 1h | 設定 Evolus Workspace | 測試環境 |
| 2h | 測試 vLLM on AMD | Qwen3-32B 可跑 |
| 1h | Create GitHub repo | 專案結構 |

### Day 1（10/12）— Kickoff + 基礎建設

| 時間 | 任務 | 產出 |
|------|------|------|
| 1h | 參加 Kickoff | 確認活動要求 |
| 2h | 設定 AMD Developer Cloud | GPU instance 可跑 |
| 3h | 部署 vLLM | Qwen3-32B API 可呼叫 |
| 2h | 建立 Evolus Workflow | 第一個 workflow 測試 |

### Day 2（10/13）— 核心 Agent 開發

| 時間 | 任務 | 產出 |
|------|------|------|
| 3h | 開發 Orchestrator Agent | 任務分配邏輯 |
| 3h | 開發 Sourcing Agent | 供應商搜尋 |
| 2h | 整合 Evolus Workflow | 第一個完整流程 |

### Day 3（10/14）— 業務 Agent 開發

| 時間 | 任務 | 產出 |
|------|------|------|
| 3h | 開發 Negotiation Agent | Email 協商 |
| 3h | 開發 Sales Agent | 客戶回覆 |
| 2h | 整合 Document Extractors | 文件處理 |

### Day 4（10/15）— 端到端整合

| 時間 | 任務 | 產出 |
|------|------|------|
| 4h | 整合所有 Agent | End-to-end flow |
| 2h | 測試完整流程 | 發現 bug |
| 2h | 修復關鍵問題 | 穩定版本 |

### Day 5（10/16）— 優化 + Demo 準備

| 時間 | 任務 | 產出 |
|------|------|------|
| 3h | 優化 UX | 更好的互動 |
| 2h | 录制 Demo 影片 | 3-5 分鐘影片 |
| 3h | 準備簡報 | 投影片 |

### Day 6（10/17）— 最終測試 + 部署

| 時間 | 任務 | 產出 |
|------|------|------|
| 3h | 完整測試 | 所有功能正常 |
| 2h | 部署 App | Live URL |
| 3h | 準備提交資料 | GitHub repo + 文件 |

### Day 7（10/18）— 提交

| 時間 | 任務 | 產出 |
|------|------|------|
| 2h | 最終檢查 | 提交清單確認 |
| 2h | 提交 | 完成提交 |
| 2h | 準備 Demo | 現場 demo（如有） |

---

## 技術實現細節

### 1. AMD Developer Cloud 設置

```bash
# 建立 GPU Droplet
# 選擇：AMD Instinct MI300X
# 選擇：ROCm Software Image
# 添加 SSH key

# 登入
ssh root@<YOUR_DROPLET_IP>

# 驗證 GPU
rocm-smi

# 拉取 vLLM Docker image
docker pull vllm/vllm-openai-rocm:latest

# 啟動 vLLM 服務 Qwen3-32B
sudo docker run -d \
  --name vllm \
  --ipc=host \
  --network=host \
  --privileged \
  --device=/dev/kfd \
  --device=/dev/dri \
  -v $HOME/.cache/huggingface:/root/.cache/huggingface \
  --entrypoint /bin/bash \
  vllm/vllm-openai-rocm:latest \
  -c "python3 -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3-32B \
  --port 8000 \
  --trust-remote-code \
  --gpu-memory-utilization 0.9 \
  --max-model-len 32768 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3 \
  --host 0.0.0.0"

# 驗證
curl http://localhost:8000/v1/models
```

### 2. Evolus Workspace 設置

```python
# 建立 Evolus 連線
import requests

EVOLUS_API_KEY = "***"
EVOLUS_APP_ID = "your-app-id"
VLLM_ENDPOINT = "http://<YOUR_DROPLET_IP>:8000/v1"

headers = {
    "X-ApiKey": EVOLUS_API_KEY,
    "X-Application-Id": EVOLUS_APP_ID,
    "Content-Type": "application/json"
}

# 建立 Agent
def create_agent(name, system_prompt):
    url = "https://api.evolus.ai/api/v1/agents"
    payload = {
        "name": name,
        "system_prompt": system_prompt,
        "model": {
            "provider": "openai",
            "base_url": VLLM_ENDPOINT,
            "model": "Qwen/Qwen3-32B"
        }
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

# 建立 Workflow
def create_workflow(name, steps):
    url = "https://api.evolus.ai/api/v1/workflows"
    payload = {
        "name": name,
        "steps": steps
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()
```

### 3. Agent 開發

#### Orchestrator Agent

```python
ORCHESTRATOR_PROMPT = """
你是跨國貿易 Orchestrator Agent。
你的工作是協調所有子 Agent 完成用戶的貿易需求。

業務流程：
1. 分析用戶需求 → 分配任務給 Sourcing Agent
2. 收到供應商清單 → 分配任務給 Negotiation Agent
3. 收到協商結果 → 分配任務給 Sales Agent
4. 收到報價單 → 分配任務給 Marketing Agent
5. 收到優化內容 → 分配任務給 Compliance Agent

輸出格式：
{
  "task": "sourcing",
  "product": "USB-C 充電器",
  "quantity": 1000,
  "target_market": "美國",
  "budget": 5000
}
"""
```

#### Sourcing Agent

```python
SOURCING_PROMPT = """
你是選品 Agent。
你的工作是分析市場需求、搜尋供應商、比較報價。

輸入：產品需求
輸出：供應商清單（包含報價、交期、評分）

步骤：
1. 分析市場需求
2. 搜尋潛在供應商
3. 發送 RFQ
4. 比較報價
5. 推薦最佳供應商
"""
```

### 4. Evolus Workflow 設計

```
[Start] → [Document Extractor: 分析產品需求]
    ↓
[AI Tool: 市場分析]
    ↓
[Webhook: 搜尋供應商]
    ↓
[Document Extractors: 分析報價單]
    ↓
[AI Tool: 比較報價]
    ↓
[Choice: 是否接受報價?]
    ↓ Yes → [AI Tool: 生成報價單] → [End]
    ↓ No → [AI Tool: 重新協商] → [Webhook: 發送郵件]
```

### 5. GitHub Repo 結構

```
trade-flow-ai/
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── src/
│   ├── main.py                 # Evolus Orchestrator
│   ├── agents/
│   │   ├── orchestrator.py
│   │   ├── sourcing.py
│   │   ├── negotiation.py
│   │   ├── sales.py
│   │   ├── marketing.py
│   │   └── compliance.py
│   ├── tools/
│   │   ├── email_sender.py
│   │   ├── rfq_generator.py
│   │   ├── price_comparator.py
│   │   └── hs_code_lookup.py
│   └── workflows/
│       ├── trade_flow.py
│       └── negotiation_flow.py
├── config/
│   ├── agents.yaml
│   └── workflows.yaml
├── docs/
│   ├── architecture.md
│   ├── api_reference.md
│   └── demo_script.md
└── tests/
    ├── test_orchestrator.py
    └── test_sourcing.py
```

---

## 評分標準對照

| 向度 | 你的專案如何贏 | 具體行動 |
|------|---------------|---------|
| **Application of Technology** | 多 Agent + AMD GPU + Evolus 深度整合 | 展示 `rocm-smi`、vLLM 配置、Evolus workflow |
| **Presentation** | 展示完整業務流程 | Demo 影片展示從選品到報價 |
| **Business Value** | 解決真實問題 | 量化：省 70% 時間、降低 30% 成本 |
| **Originality** | 目前市場沒有完整解決方案 | 強調「端到端自動化」的獨特性 |

---

## 風險管理

| 風險 | 機率 | 影響 | 緩解措施 |
|------|------|------|---------|
| 時間不夠 | 高 | 高 | 只做 1-2 個 Agent 做到極致 |
| vLLM 部署失敗 | 中 | 高 | 提前測試，準備 Fireworks AI 備案 |
| Evolus API 不熟悉 | 中 | 中 | 先做一個簡單 workflow 測試 |
| 範圍太大 | 高 | 高 | 嚴格控制 scope，只做一個產業 |
| 網路問題 | 低 | 中 | 準備離線 demo 影片 |

---

## 提交清單

- [ ] Project title & description
- [ ] Technology & category tags
- [ ] Cover image（1280x720）
- [ ] Demo video（3-5 分鐘）
- [ ] Slide presentation
- [ ] **Public GitHub repository**
- [ ] Demo application URL
- [ ] README that runs（judges 會看）
- [ ] No API keys committed

---

## 資源連結

### 官方資源

- **AMD Developer Hackathon: ACT III** — https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii
- **AMD AI Developer Program** — https://developer.amd.com/ai-developer-program/
- **AMD Developer Cloud** — https://www.amd.com/en/developer/resources/rocm-hub/ai-cloud-development.html
- **Evolus** — https://evolus.ai/
- **Evolus Developer** — https://evolus.ai/en/developers

### 技術文檔

- **vLLM on ROCm** — https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/vllm.html
- **vLLM Docker** — https://docs.vllm.ai/en/latest/deployment/docker/
- **Evolus REST API** — https://api.evolus.ai/api/v1
- **Evolus MCP Server** — https://api.evolus.ai/mcp

### 參考案例

- **Alibaba Accio Work** — https://www.accio.com/
- **Paxport AI** — https://www.paxportglobal.com/
- **Export Arena** — https://exportarena.com/ai-workers

---

## 🚀 立即行動（今天 10/11）

### 優先順序

1. **定義業務流程圖**（2h）
   - 畫出完整流程圖
   - 決定要做哪個產業

2. **設定 Evolus Workspace**（1h）
   - 註冊/登入 Evolus
   - 建立測試 workspace

3. **測試 vLLM on AMD**（2h）
   - 確認 AMD Developer Cloud 額度
   - 部署 Qwen3-32B
   - 測試 API

4. **Create GitHub repo**（1h）
   - 建立專案結構
   - 寫 README

---

> **記住**：法官不看「你做了多少功能」，看「你的產品是否 work」
>
> "One complete path through the product beats four half-built paths."

---

*文件產生時間：2026/10/11*
*版本：v1.0 定稿*
