"""TradeFlow — real-LLM benchmark statistics helper.

從 results/real_llm_benchmark*.json 計算：
  * Wilson 與 Clopper-Pearson（精確）二項信賴區間
  * 與「模擬器宣稱值」的對照

用法：
    python scripts/benchmark_stats.py [results/real_llm_benchmark_100.json]

[為何需要]
「0/N」不能直接講成「0%」。若未給出信賴區間，0/20 與 0/1000 會被讀成同一件事。
本腳本強制為每一個實測比例附上區間，防止把「沒觀察到」講成「不存在」。
"""
from __future__ import annotations

import json
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.config.heuristics import WILSON_ALPHA, WILSON_Z  # noqa: E402


def wilson_ci(successes: int, n: int, z: float | None = None) -> tuple:
    """Wilson score 區間（小樣本下比正態近似更穩健）。"""
    if z is None:
        z = WILSON_Z
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def clopper_pearson_upper(successes: int, n: int, alpha: float | None = None) -> float:
    """單邊精確上界。0 事件時即 1 - alpha^(1/n)。"""
    if alpha is None:
        alpha = WILSON_ALPHA
    if successes >= n:
        return 1.0
    if successes == 0:
        return 1.0 - alpha ** (1.0 / n)
    # 一般情形：以二分求解 Beta 分位點
    lo, hi = successes / n, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        # P[X >= successes | p=mid] 用二項尾部機率
        tail = sum(math.comb(n, k) * mid ** k * (1 - mid) ** (n - k)
                   for k in range(successes, n + 1))
        if tail > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def fmt(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "results/real_llm_benchmark_100.json"
    if not os.path.exists(path):
        path = "results/real_llm_benchmark.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    print("=" * 92)
    print(f"Real LLM benchmark statistics — {path}")
    print(f"  model      : {data['meta']['model']}")
    print(f"  endpoint   : {data['meta']['endpoint']}")
    print(f"  generated  : {data['meta']['generated_at_utc']}")
    print("=" * 92)

    hdr = f"{'archetype':<22}{'n':>5}{'deal':>9}{'95% CI (Wilson)':>22}{'viol':>8}{'95% upper (exact)':>20}"
    print(hdr)
    print("-" * 92)

    tot_n = tot_deals = tot_viol = 0
    for name, a in data["archetypes"].items():
        n = a["completed"]
        deals = a["deal_rate"] * n if a["deal_rate"] is not None else 0
        viol = a["violation_rate"] * n if a["violation_rate"] is not None else 0
        lo, hi = wilson_ci(int(round(deals)), n)
        vupper = clopper_pearson_upper(int(round(viol)), n)
        tot_n += n
        tot_deals += int(round(deals))
        tot_viol += int(round(viol))
        print(f"{name:<22}{n:>5}{fmt(a['deal_rate']):>9}"
              f"{f'[{lo*100:.1f}%, {hi*100:.1f}%]':>22}"
              f"{fmt(a['violation_rate']):>8}{vupper*100:>19.1f}%")

    print("-" * 92)
    lo, hi = wilson_ci(tot_deals, tot_n)
    vupper = clopper_pearson_upper(tot_viol, tot_n)
    print(f"{'TOTAL':<22}{tot_n:>5}{tot_deals/tot_n*100:>8.1f}%"
          f"{f'[{lo*100:.1f}%, {hi*100:.1f}%]':>22}"
          f"{tot_viol/tot_n*100:>7.1f}%{vupper*100:>19.1f}%")
    print()
    t = data.get("totals", {})
    print(f"  LLM calls        : {t.get('llm_calls')}")
    print(f"  parse failures   : {t.get('llm_parse_failures')}   unit anomalies: {t.get('llm_unit_anomalies')}")
    print(f"  api failures     : {t.get('llm_api_failures')}")
    print(f"  reasoning tokens : {t.get('llm_reasoning_tokens'):,}" if t.get("llm_reasoning_tokens") else "")
    print(f"  total cost       : ${t.get('llm_total_cost_usd')}")
    print()
    print("  [讀法] 「viol」是實測違規率；「95% upper」是精確單邊上界。")
    print("         若上界低於某宣稱值，即可在該信心水準下否證該宣稱值。")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
