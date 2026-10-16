# TradeFlow Multi-Supplier Reverse Auction Coordination Benchmark

- Product: Custom Carbon Auto Umbrella (MOQ: 10,000 units)
- Target Price: ¥12.00
- Walk-away Ceiling: ¥16.20
- Winning Supplier: Supplier A (敏捷讓步型)
- Settled Price: ¥12.60
- Procurement Savings: 22.2% (¥36,000.00 total)
- Tensor Grid Cells: 28,800,000 (= K x S x N_open x N_sched parameterised product, NOT a measured rollout/iteration count)

| Round | Supplier | Quote (¥) | Tactic Classification | Dynamic BATNA Ceiling (¥) | Risk (%) | Counter-Offer (¥) |
|---|---|---|---|---|---|---|
| Round 1 | Supplier A (敏捷讓步型) | ¥13.26 | LEAN_IN | ¥15.63 | 3.8% | ¥12.00 |
| Round 1 | Supplier B (對等互惠型) | ¥15.60 | SIDELINE | ¥13.32 | 11.3% | ¥13.67 |
| Round 1 | Supplier C (強硬死守型) | ¥15.92 | SIDELINE | ¥13.32 | 11.2% | ¥14.08 |
| Round 1 | Supplier D (高價溢價型) | ¥16.80 | SIDELINE | ¥13.32 | 15.0% | ¥14.71 |
| Round 2 | Supplier A (敏捷讓步型) | ¥12.87 | LEAN_IN | ¥14.65 | 3.8% | ¥12.00 |
| Round 2 | Supplier B (對等互惠型) | ¥14.58 | SIDELINE | ¥12.93 | 8.9% | ¥13.88 |
| Round 2 | Supplier C (強硬死守型) | ¥15.52 | SIDELINE | ¥12.93 | 10.7% | ¥14.29 |
| Round 2 | Supplier D (高價溢價型) | ¥16.68 | SIDELINE | ¥12.93 | 13.2% | ¥15.54 |
| Round 3 | Supplier A (敏捷讓步型) | ¥12.60 | LEAN_IN | ¥14.25 | 4.0% | ¥12.00 |
| Round 3 | Supplier B (對等互惠型) | ¥14.27 | SIDELINE | ¥12.67 | 7.8% | ¥13.88 |
| Round 3 | Supplier C (強硬死守型) | ¥14.16 | SIDELINE | ¥12.67 | 9.2% | ¥14.29 |
| Round 3 | Supplier D (高價溢價型) | ¥16.44 | SIDELINE | ¥12.67 | 13.9% | ¥15.96 |
