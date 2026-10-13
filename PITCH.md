# TradeFlow AI — Pitch Deck (10 slides)

> AMD Developer Hackathon: ACT III · Reinvent Commerce + Evolus Business Agents on AMD
> Render-ready markdown; feed to `office-pptx` skill later to export .pptx.

---

## Slide 1 — Problem
- 采购议价靠经验，没有量化框架。
- 主赛事 brief 明写：*“explain how the prediction was calculated. Do not use a language model as the only forecasting method.”*
- 当前 AI 采购助手 = 聊天机器人，决策黑箱、不可验证。

## Slide 2 — One-liner
**TradeFlow**：在不確定性下，用贝氏信念 + 在 AMD MI300X 上向量化蒙地卡羅搜尋，
算出采购谈判的**最优让步策略**；LLM 只写/读/解释，决策核心是数学引擎。

## Slide 3 — Why not a chatbot
- 决策 = 贝氏信念更新 + 向量化 MCTS，每一步可解释、可检验。
- 命中主赛事评分标的：「LLM 不能當唯一預測法」。
- 信念分布可视化 = 全计划最有价值的 demo 资产。

## Slide 4 — Architecture
(见 README 的 Mermaid 图)
- 引擎 decision core；Evolus 语言层 + 工作流 + 文件解析。
- 4 个 Evolus building blocks + BYO vLLM + 容器化 + 公開 repo。
- GPU 跑引擎内部批量 rollout（对手术样本 × 480 策略），非聊天 token。

## Slide 5 — Demo: 3 Wows
1. **Belief update**：5 轮谈判，供应商回价后底价后验 1.08→1.25，推荐开价同步 1.03→1.19。
2. **CPU vs MI300X**：9.6M 次 rollout，CPU 1.12s vs MI300X ~0.4s（投影 16×）。
3. **Robustness**：引擎策略 vs 固定 5%/轮基準，破局率 6.7% vs 28.4%；prior ±50% 敏感度量化的。

## Slide 6 — Business value
- 行业基准：专业采购议价常省 **5–10%**（CAPS/Hackett）。
- **100 万美元订单省 5% = 5 万美元**。
- Demo 垂直：自动雨伞出口（HS6601），price anchor 拉真实 FOB 区间。

## Slide 7 — AMD technology
- 引擎在 AMD MI300X 上批量评估对手术样本 × 策略族。
- vLLM on ROCm（`rocm/vllm:rocm10.1.0...`）+ Qwen2.5-14B + `--tool-call-parser hermes`。
- 诚实叙事：CPU 纯 loop ~46s（聊天像卡死）→ MI300X 0.4s（即时）。

## Slide 8 — Evolus integration
- 4 blocks：Workflow（email-in）→ Document Extractor → AI Tools(email) → Knowledge Library。
- app 驱动 Evolus（REST：`X-ApiKey` + `X-Application-Id`）。
- 60 req/min 限制不沾边（GPU 百万次 rollout 在 container 内部）。

## Slide 9 — Robustness / rigor
- 主动量化「对手术模型是假设的」：prior ±50% 下推荐策略仍稳定。
- regret vs 多个固定类人基準（3/5/10/15%）皆優。
- 人類審批關卡：破局率 >15% 自動標 HUMAN_REVIEW，LLM 不发明价格。

## Slide 10 — Next steps
- 有真实谈判 log 後接 **CFR**（現為贝氏 + backward induction，正確方法）。
- MI300X 实测取代投影；多垂直（电子零件）扩展。
- 双投：主奖（Reinvent Commerce）+ 一个 partner prize（Evolus）。
