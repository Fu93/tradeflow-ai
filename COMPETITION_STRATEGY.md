# 比賽要求深度研究 + 獲勝方程式 + 我們計畫的落差

> 來源：lablab.ai 賽事頁（https://lablab.ai/ai-hackathons/amd-developer-hackathon-act-iii）逐字擷取。
> 日期：2026/10/13。所有評分維度為原文，未改寫。

---

## 1. 賽制與獎池

- **線上階段**：2026/10/12–18（我們在這裡）。**實體階段**：義大利 Rome/Milan/Imperia，10/17–18（僅入選者，我們不參加）。
- **總獎池 $12,000+**：
  - AMD 主賽事獎：$5,000
  - Google 合作獎：$5,000（Google 技術可選，我們不用）
  - **Evolus 合作賽道**：€1,011.50（Developer plan + credits，1st/2nd/3rd）
  - 另有 Vibe Generation 特別獎（AirPods 5）
- **雙投規則（原文）**：*"A single project may qualify for the main challenge prize, one partner prize, or both partner prizes."* → 我們主賽事 + Evolus 雙投，合法且建議。

---

## 2. 兩條賽道的評分維度（逐字）

### 主賽事（AMD challenge）— Judging criteria
1. **Application of Technology** — How effectively the chosen model(s) are integrated into the solution.
2. **Presentation** — The clarity and effectiveness of the project presentation.
3. **Business Value** — The impact and practical value, considering how well it fits into business areas.
4. **Originality** — The uniqueness and creativity of the solution, highlighting approaches and ability to demonstrate behaviors.

### Evolus 合作賽道 — Judging（四維度，同四項但讀法不同）
1. **Application of Technology** — How deeply the solution uses Evolus building blocks, and how well the AMD-hosted model is integrated.
2. **Business Value** — A real process, a clear user, a measurable gain.
3. **Originality** — A new approach, or a process nobody automated this way.
4. **Presentation** — A clear demo and pitch.
- **驗收方式（關鍵）**：*"the Evolus team reviews the configuration and usage statistics of each event workspace: the connected model endpoint, the building blocks used and the API calls."* → **必須真的用 API 驅動 Evolus，且 usage stats 要有紀錄**。離線 stub 不夠。

> 兩條賽道都是這 4 個維度、未給權重（視為等權）。差別只在「Application of Technology」在主賽事看 AMD 整合深度，在 Evolus 看 Evolus blocks + AMD-hosted model 整合。

---

## 3. 獲勝方程式（推導）

把 4 個維度當必要條件（缺一不可），獲勝 = 同時把四項做滿 + 在「可驗證的 AMD 實體算力」上堆深度。

**A. Application of Technology（最大差異化來源）**
- 主賽事：AMD 是**強制技術**（*"AMD is a required technology to be eligible for prizes"*）。我們的引擎在 MI300X 上批量 rollout（對手樣本 × 480 策略 = 960 萬次）就是「實體 AMD 工作」，不是聊天外殼。
- Evolus：要求 *"main agent must use an open model served on AMD infrastructure through your own vLLM endpoint on AMD Developer Cloud"* + ≥2 building blocks + 從 app 用 REST API 驅動。→ **這是我們的合規硬條件**（見 §5 修正）。

**B. Business Value（要有「可量化」的數字）**
- 我們已有：robustness 報告 = 引擎策略 vs 固定 5%/輪基準，**破局率 6.7% vs 28.4%**，**+¥1.95/unit**；行業錨點 5–10% 議價省幅、100 萬美元訂單省 5% = 5 萬美元。這組數字直接餵 Business Value。

**C. Originality（沒人這樣自動化的過程）**
- 採購議價用**貝氏博弈 + 向量化 MCTS** 當決策核心、LLM 只當語言層 → 直接命中主賽事 brief 的 *"explain how the prediction was calculated. Do not use a language model as the only forecasting method"*。且 procurement negotiation 極少被這樣做成可解釋引擎 + 信念視覺化。

**D. Presentation（清楚 demo + pitch）**
- 需：cover image、demo video（3–5 min）、slide presentation、README that runs、public GitHub、Application URL（可跑的 demo 平台）。我們 README/repo 已就緒，缺 cover/video/slide/URL。

**結論公式**：
> 真實業務流程（採購議價） + 可解釋數學引擎（貝氏+MCTS，非 chatbot） + 在 AMD MI300X 上實體跑批量 rollout + 用 Evolus API 真驅動 ≥2 blocks + 一組可量化增益數字 + 乾淨 demo 影片/簡報 = 雙賽道都有競爭力。

---

## 4. 我們計畫 vs 要求 對照（gap 表）

| 要求 | 我們狀態 | 缺口 |
|---|---|---|
| Public GitHub repo | ✅ 已上線（32 檔、CI 綠） | — |
| README that runs | ✅ `python -m src.cli --figures` | — |
| Containerized app | ✅ Dockerfile + compose | — |
| No API keys committed | ✅ .gitignore + .env.example | — |
| AMD 技術（實體算力） | 🟡 引擎邏輯就緒，MI300X 待 credits | 需實測 benchmark 截圖取代投影 |
| BYO vLLM on AMD Developer Cloud | 🟡 runbook 就緒 | **需 credits + 開 droplet**（見 §5） |
| Evolus event workspace | 🔴 需 kickoff event code 建 workspace | 準備 client + dry-run |
| Evolus ≥2 building blocks | 🟡 client 支援 workflow/AI Tools/extractors | 需真 API 呼叫產生 usage stats |
| 從 app 用 REST 驅動 Evolus | 🟡 有 EvolusClient | 離線 stub → 接真 key |
| Cover image 1280×720 | 🔴 未做 | 用 matplotlib 生成 |
| Demo video 3–5 min | 🔴 未錄 | 接線後錄 |
| Slide presentation | 🟡 PITCH.md 骨架 | 轉 .pptx（office-pptx） |
| Application URL（demo 平台） | 🔴 未部署 | 在 AMD droplet 跑 FastAPI 公開 IP |
| 可量化增益數字 | ✅ robustness 報告 | 搬進 pitch/video |

---

## 5. ⚠️ 重要修正：Fireworks 不合規 Evolus 賽道

計畫早期把 Fireworks 當 vLLM 備案。但 Evolus 賽道原文要求：
> *"the main agent must use an open model served on AMD infrastructure through **your own vLLM endpoint on AMD Developer Cloud**."*

Fireworks 是**託管 API**（非 AMD Developer Cloud、非你自己的 vLLM endpoint）→ **不滿足 Evolus 賽道的模型要求**。
- 因此：**Evolus 賽道強制用我們 $100 GPU credit 開的 AMD Developer Cloud vLLM**。Fireworks 只能當「AMD DC 完全掛掉時」的最後手段，且那時 Evolus 賽道即不合規。
- 好消息：我們已選 **GPU credit（非 Fireworks credit）**，方向正確。主賽事則 AMD 技術由引擎 MI300X rollout 即可滿足，vLLM 放 AMD DC 順便兩條賽道共用一 stack。
- **行動**：Fireworks 備案降級為「非合規最後手段」，不再當主要路徑；所有精力壓在 AMD Developer Cloud 開通。

---

## 6. 優先行動清單

**P0 — 卡資格/驗收（不補就無法參評）**
1. 等 AMD credits 審核 → 開 MI300X droplet → `rocm/vllm` serve Qwen2.5-14B + `--tool-call-parser hermes`。
2. kickoff 拿 Evolus event code → 建 event workspace → 抄 key 填 `.env`。
3. 把 `EvolusClient` 從 offline stub 接成真 API（workflow execute + AI Tools 寫信），讓 usage stats 有紀錄。
4. 在 AMD droplet 部署 FastAPI（Application URL 公開可連）。

**P1 — Presentation（評分项，可現在做）**
5. 生成 cover image 1280×720（matplotlib，現可做）。
6. PITCH.md → .pptx 簡報（office-pptx，現可做）。
7. 接線完成後錄 demo video（信念更新 + CPU/MI300X + 穩健性 三段）。

**P2 — 深度（加分）**
8. MI300X 實測 benchmark 截圖取代 16× 投影。
9. 確保 demo 中 Evolus ≥2 blocks 被「實際呼叫」並可見。

---

## 7. 我們的獨特賣點（pitch 一句話）

> 「別人用 LLM 當議價黑箱；TradeFlow 用貝氏博弈 + 在 AMD MI300X 上向量化蒙地卡羅，算出可解釋的最優讓步策略，LLM 只負責寫信。破局率從 28% 降到 7%，每單多賺約 ¥1.95。」
