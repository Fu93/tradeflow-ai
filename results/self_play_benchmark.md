# TradeFlow v4 Adversarial Baseline Tournament Results

Generated: 2026-10-14 01:33:03
Matches per Archetype: 50 (Total 750 match simulations)
Benchmark Methodology: NegotiationArena (ICLR 2024), AgenticPay & Cattle Trade Evaluation Protocol (Testing Constraint Adherence, Savings & Deal Rate)

| 對手類型 (Opponent Archetype) | 策略代理 (Agent) | 談判勝率 (Deal %) | 平均節省率 (Savings %) | 破局率 (Breakdown %) | 預算違規率 (Violation %) | 平均輪數 (Rounds) |
|---|---|---|---|---|---|---|
| **Hard-Bargainer** | **TradeFlow Engine** | **100.0%** | **14.8%** | **0.0%** | **0.0%** | 5.0 |
|  | Vanilla LLM Agent (Parametric) | 100.0% | 5.3% | 0.0% | 12.0% | 2.78 |
|  | Fixed 5%/rd Baseline | 100.0% | 14.8% | 0.0% | 0.0% | 5.0 |
| **Conceder** | **TradeFlow Engine** | **100.0%** | **18.6%** | **0.0%** | **0.0%** | 2.0 |
|  | Vanilla LLM Agent (Parametric) | 100.0% | 17.2% | 0.0% | 0.0% | 1.44 |
|  | Fixed 5%/rd Baseline | 100.0% | 18.4% | 0.0% | 0.0% | 2.0 |
| **Tit-for-Tat** | **TradeFlow Engine** | **100.0%** | **15.4%** | **0.0%** | **0.0%** | 4.0 |
|  | Vanilla LLM Agent (Parametric) | 100.0% | 13.5% | 0.0% | 0.0% | 2.0 |
|  | Fixed 5%/rd Baseline | 100.0% | 14.8% | 0.0% | 0.0% | 4.0 |
| **Bluffing Hardliner** | **TradeFlow Engine** | **70.0%** | **18.5%** | **30.0%** | **0.0%** | 5.0 |
|  | Vanilla LLM Agent (Parametric) | 100.0% | 3.7% | 0.0% | 44.0% | 2.96 |
|  | Fixed 5%/rd Baseline | 68.0% | 18.5% | 32.0% | 0.0% | 5.0 |
| **Noisy Seller** | **TradeFlow Engine** | **100.0%** | **16.4%** | **0.0%** | **0.0%** | 3.74 |
|  | Vanilla LLM Agent (Parametric) | 100.0% | 10.1% | 0.0% | 0.0% | 2.18 |
|  | Fixed 5%/rd Baseline | 100.0% | 15.7% | 0.0% | 0.0% | 3.52 |
