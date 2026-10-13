# 🏆 TradeFlow AI — 談判決策引擎

AMD Developer Hackathon: ACT III (lablab.ai, 2026/10/12–18) 參賽專案。

**一句話**：在不確定性下，用貝氏信念 + 在 AMD MI300X 上向量化蒙地卡羅搜尋，
算出採購談判的最優讓步策略；LLM 只負責寫信、讀信、解釋策略。

- 公開倉庫：https://github.com/Fu93/tradeflow-ai
- 策略文件：[TradeFlow_PlanB_Final.md](../TradeFlow_PlanB_Final.md)
- 執行手冊：[TradeFlow_Execution_Manual.md](../TradeFlow_Execution_Manual.md)
- 提交清單：[SUBMIT.md](SUBMIT.md)

## 快速開始（無 GPU、無外部依賴也能跑）

```bash
pip install -r requirements.txt
python -m src.cli                   # 端到端 demo（5 輪自動雨傘談判）
python -m src.cli --robustness      # 穩健性報告（regret vs 固定基準 + prior ±50% 敏感度）
python -m src.cli --figures         # 產 4 張圖 + bench.json 到 results/
python -m src.app.orchestrator      # 離線 orchestrator 自檢（模擬供應商回價）
```

`--figures` 會產出：
- `results/posterior.png`     — 末輪底價後驗分布 + 目標/上限/推薦開盤
- `results/belief_update.png` — 5 輪後驗均值±1σ 與推薦開盤同步演變
- `results/benchmark.png`     — CPU vs MI300X solve time（對數軸）
- `results/robustness.png`    — 引擎 vs 固定基準 + prior 敏感度
- `results/bench.json`        — 各 sample 數的耗時 / 投影

## 元件

| 路徑 | 職責 |
|---|---|
| `src/engine/model.py` | 4 對手類型 + 480 策略網格 |
| `src/engine/solver.py` | 向量化 MCTS + 貝氏更新 + 帕累托選擇 |
| `src/engine/robustness.py` | regret vs 固定基準 + prior 敏感度 |
| `src/engine/benchmark.py` | CPU 實測 + MI300X 投影 + JSON |
| `src/engine/viz.py` | 4 張視覺化（headless Agg） |
| `src/evolus/client.py` | Evolus REST client（workflow / AI Tools / agent chat） |
| `src/app/main.py` | FastAPI：`/negotiate`、`/webhook/evolus` |
| `src/app/orchestrator.py` | 膠水層：收 webhook → 跑引擎 → 叫 agent 寫信（離線 stub 兜底） |
| `src/app/demo_data.py` | 預錄供應商回價（穩定 demo） |
| `src/cli.py` | 一個指令三種模式 |
| `scripts/fetch_price_anchor.py` | best-effort 拉 HS6601 真實 FOB 區間 |
| `tests/` | engine / evolus / benchmark 三套 smoke test |

## 啟動 API

```bash
python -m src.app.main       # http://localhost:8000/docs
curl -X POST localhost:8000/negotiate -H 'Content-Type: application/json' \
  -d '{"product":"auto umbrella","target_price":12.0,"n_samples":20000}'
curl -X POST localhost:8000/webhook/evolus -H 'Content-Type: application/json' \
  -d '{"extracted":{"target_price":12.0},"offer":1.25,"email_body":"We can do ¥15, MOQ 500."}'
```

## 評估（雙 benchmark）

| 模式 | 命令 | 說明 |
|---|---|---|
| 邏輯 | `python tests/test_engine.py` | 模型/求解/貝氏 |
| API mock | `python tests/test_evolus.py` | Evolus client (mocked requests) |
| 性能 | `python tests/test_benchmark.py` | benchmark + 4 張圖能跑 |

## 容器化（提交用）

```bash
docker compose up --build   # 一個指令跑起；.env 提供 key，絕不 commit
```

## 評分對照

- **Reinvent Commerce（主賽事）**：改善採購端 margin。
- **Evolus Business Agents on AMD**：event workspace + BYO vLLM + ≥2 building blocks + REST API 驅動。
- **AMD 技術**：引擎批量 rollout 在 MI300X 上跑（CPU 約 1s / 純 loop ~46s / MI300X ~0.4s）。

## 資料錨點（price anchor）

- `seed_data/price_anchor.json` 目前為**佔位**：UN Comtrade Plus / OEC 現已改為需登入 session，自動抓取（見 `scripts/fetch_price_anchor.py`）未取得即時 FOB 區間。
- 手動步驟已寫在該檔的 `market.manual_steps`；在 Demo 前填入 HS6601 真實 low/high 即可校準 prior。

## 注意

- 所有 key 走 `.env`（見 `.env.example`），`.gitignore` 已擋 `.env`。
- 引擎運算量 = 對手樣本 × 策略族（40×12=480 策略）；20k 樣本 = 960 萬 rollout。
- 商業價值錨點：專業採購議價常省 5–10%；100 萬美元訂單省 5% = 5 萬美元。
