"""TradeFlow 引擎 — Benchmark（CPU 實測 + MI300X 預測）。

CPU 實測：numpy 向量化解。
MI300X 預測：基於 vLLM/ROCm 大量運算以記憶體頻寬為瓶頸的經驗，
單精度批次 GPU 約比 CPU 向量化快 10–20×。取中間值 15× 作為投影。

輸出：
- run()        : 在各 sample 數下量 CPU 秒數，回傳 rows
- save_json()  : 寫 results/bench.json
- 與 viz.plot_benchmark 配合產 Wow#2 圖
"""
from __future__ import annotations
import json
import os
import time
import numpy as np
from . import model, solver

# 經驗投影：MI300X 對大批次 numpy 同構運算約快 15×（10–20× 區間中值）
MI300X_SPEEDUP = 15.0


def run(n_list=(2_000, 10_000, 20_000, 50_000), use_gpu=False):
    """量 solve() 在各 sample 數下的耗時。"""
    rows = []
    opens, scheds = model.strategy_grid()
    for n in n_list:
        t0 = time.perf_counter()
        res = solver.solve(n_opp_samples=n, opens=opens, scheds=scheds, use_gpu=use_gpu)
        dt = time.perf_counter() - t0
        n_rollouts = n * opens.shape[0] * scheds.shape[0]
        rows.append({
            "samples": int(n),
            "rollouts": int(n_rollouts),
            "seconds": round(dt, 4),
            "us_per_rollout": round(dt * 1e6 / n_rollouts, 4),
            "mi300x_projected_seconds": round(dt / MI300X_SPEEDUP, 4),
        })
    return rows


def save_json(rows, out_dir="results"):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "bench.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"speedup_assumption": MI300X_SPEEDUP, "rows": rows}, f, indent=2)
    return path


if __name__ == "__main__":
    rows = run()
    for r in rows:
        print(f"samples={r['samples']:>7,}  rollouts={r['rollouts']:>12,}  "
              f"CPU={r['seconds']:>7.3f}s  MI300X~{r['mi300x_projected_seconds']:.3f}s")
    print("saved:", save_json(rows))
