# 部署 Runbook — TradeFlow on AMD Developer Cloud

> 目標：拿到 **Application URL**（lablab 主賽事提交必填）＋ **Evolus BYO vLLM endpoint**。
> 下列步驟是 runbook；執行時機 = credits 審核過 + Evolus key 到手後。

---

## 0. 前置
- AMD Developer Cloud credits 已核可（2–3 工作天）。
- Evolus event workspace 已用 kickoff event code 建立，key 已抄進 `.env`：
  ```
  EVOLUS_API_KEY=...
  EVOLUS_APP_ID=...
  ```

## 1. 開 MI300X droplet
在 AMD Developer Cloud console：
1. 選 MI300X（或 MI325X 等 Instinct GPU 實例）。
2. 選官方 ROCm image（ubuntu 24.04 + ROCm 10.x）。
3. **記住 30 天到期日**（啟用即開始倒數）——別太早 activate。

## 2. 部署 vLLM（BYO model）
```bash
docker pull rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0

docker run -d --rm \
  --device /dev/kfd --device /dev/dri \
  --security-opt seccomp=unconfined \
  --shm-size 32g \
  -p 8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rocm/vllm:rocm10.1.0_ubuntu24.04_py3.14-pytorch_2.13.0_vllm-0.29.0 \
  serve Qwen/Qwen2.5-14B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --api-key $VLLM_API_KEY \
  --tool-call-parser hermes
```
- 下載模型需 HF token（Qwen2.5-14B 是公開權重）。
- 驗證：`curl http://<ip>:8000/v1/models`
- [WARN] `hermes` 解析器（不是 `qwen3_xml`，那是 Qwen3-Coder 專用）。

## 3. 設定 Evolus
1. Evolus workspace → **Models / Your models** → 新增自訂 endpoint：
   base url `http://<mi300x-ip>:8000/v1`，API key = 上面 `$VLLM_API_KEY`。
2. 建 Workflow：email-in 觸發 → Document Extractor → HTTP callback 到
   `http://<mi300x-ip>:8080/webhook/evolus`。
3. AI Tool：加一個 "send email" tool。
4. **≥2 building blocks 要真的在 usage stats 出現**（Evolus 評審會查）。

## 4. 部署 TradeFlow app
```bash
# 在 droplet 上
git clone https://github.com/Fu93/tradeflow-ai.git && cd tradeflow-ai
cp .env.example .env   # 填 EVOLUS_API_KEY / VLLM_BASE_URL 等
docker compose up -d   # FastAPI 對外 :8080
```
- **Application URL** = `http://<mi300x-ip>:8080`（主賽事提交用）。
- 如需對外穩定 URL：加 nginx/https 或用 Cloudflare tunnel。

## 5. 接線完成後的驗證
```bash
curl -X POST http://localhost:8080/negotiate \
  -H 'content-type: application/json' \
  -d '{"target_price": 12.0, "observed_offer": 1.25}'
```
- 回傳應含 `proposal.round_by_round_prices` + `risk_flag`。
- 檢查 Evolus workspace usage stats：endpoint connected / API calls 有紀錄。
- 若 GP 可用：把 `bench.json` 的 `mi300x_projected_seconds` 換成**實測值**。

## 6. 提交前最後確認
- [ ] Application URL 可連（點得開 `/health`）
- [ ] demo_video.mp4 已上傳，Scene 4 是真錄影
- [ ] cover_1280x720.png / pitch.pptx 已附
- [ ] 公開 repo 無任何 key（`git log -p | grep -i key` 清單）
- [ ] lablab 主賽事 + Evolus 賽道都選

---

## 故障排除
| 症狀 | 處理 |
|---|---|
| vLLM 起不來 | `rocm-smi` 確認 GPU 可用；`-p 8000:8000` 別忘記 `--device /dev/kfd` |
| tool call 沒觸發 | 確認 `--tool-call-parser hermes`（Qwen2.5-Instruct 用 hermes） |
| Evolus callback 收不到 | droplet firewall 開 8080；或先用 `curl` 手動打一次 |
| 記憶體 OOM | `--max-model-len 8192`、`--gpu-memory-utilization 0.85` |
