# TradeFlow v4 Adversarial Baseline Tournament Results

Generated: 2026-10-16 04:09:27
Matches per Archetype: 20 (Total 100 match simulations across 5 archetypes)
Opponent belief samples per solve: 1000 | Seed: 42
Benchmark Methodology: NegotiationArena (ICLR 2024), AgenticPay & Cattle Trade Evaluation Protocol (Testing Constraint Adherence, Savings & Deal Rate)
Data class: C-level (parametric behavioural simulator; NOT live LLM API calls)

每對手場次 (n_matches_per_adversary): 20 · seed: 42

| 對手類型 (Opponent Archetype) | 策略代理 (Agent) | N | 談判勝率 (Deal %) | 平均節省率 (Savings %) | 破局率 (Breakdown %) | 預算違規率 (Violation %) | 平均輪數 (Rounds) |
|---|---|---|---|---|---|---|---|
| **Hard-Bargainer** | **TradeFlow Engine** | 20 | **100.0%** | **8.8%** | **0.0%** | **0.0%** | 4.0 |
|  | Panic-Heuristic Buyer Proxy (parametric, not an LLM) | 20 | 100.0% | 5.3% | 0.0% | 20.0% | 2.8 |
|  | Fixed 5%/rd Baseline | 20 | 100.0% | 9.2% | 0.0% | 0.0% | 4.0 |
| **Conceder** | **TradeFlow Engine** | 20 | **100.0%** | **20.0%** | **0.0%** | **0.0%** | 3.0 |
|  | Panic-Heuristic Buyer Proxy (parametric, not an LLM) | 20 | 100.0% | 17.0% | 0.0% | 0.0% | 1.35 |
|  | Fixed 5%/rd Baseline | 20 | 100.0% | 18.4% | 0.0% | 0.0% | 2.0 |
| **Tit-for-Tat** | **TradeFlow Engine** | 20 | **100.0%** | **19.7%** | **0.0%** | **0.0%** | 4.0 |
|  | Panic-Heuristic Buyer Proxy (parametric, not an LLM) | 20 | 100.0% | 13.7% | 0.0% | 0.0% | 2.05 |
|  | Fixed 5%/rd Baseline | 20 | 100.0% | 16.0% | 0.0% | 0.0% | 4.0 |
| **Bluffing Hardliner** | **TradeFlow Engine** | 20 | **100.0%** | **15.0%** | **0.0%** | **0.0%** | 5.0 |
|  | Panic-Heuristic Buyer Proxy (parametric, not an LLM) | 20 | 100.0% | 3.6% | 0.0% | 40.0% | 2.9 |
|  | Fixed 5%/rd Baseline | 20 | 100.0% | 15.1% | 0.0% | 0.0% | 5.0 |
| **Noisy Seller** | **TradeFlow Engine** | 20 | **100.0%** | **15.6%** | **0.0%** | **0.0%** | 3.65 |
|  | Panic-Heuristic Buyer Proxy (parametric, not an LLM) | 20 | 100.0% | 10.1% | 0.0% | 0.0% | 2.25 |
|  | Fixed 5%/rd Baseline | 20 | 100.0% | 16.1% | 0.0% | 0.0% | 3.45 |
