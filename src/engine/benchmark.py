"""TradeFlow 引擎 — 硬體效能評測與 AMD Instinct Roofline 頻寬審計。

特性：
1. 硬體自動探測 (Hardware Auto-Discovery)：優先檢測 CuPy / ROCm HIP 加速環境。
2. 真實量測與屋頂模型雙軌驗證 (Roofline Bandwidth Model)：
   - 當具備 GPU 時，執行真實暖機與內核計時。
   - 當處於 CPU 環境時，只報資料搬運下界：實測工作集位元組 ÷ datasheet 峰值。
     常數來自 results/mi300x_roofline.json（scripts/regen_mi300x_roofline.py）。
     那是 theoretical roofline lower-bound estimate, not measured。
     本模組不再引用主機 DDR 頻寬：bench 檔沒有 CPU 型號，datasheet 也沒有。
3. 支援單供應商與 4 供應商張量並行拍賣雙維度評測。

[ROLLOUT 計數說明]
- `simulate_reverse_auction()` 示範路徑（Web「Run Auction」預設）使用
  (K=4, S=20,000, N_open=20, N_sched=6)，每輪 9.6M，3 輪累計 **28,800,000**。
- 本模組 `run()` 內的拍賣基準測試改用 S=50,000（見下方 `n_samples=50_000`），
  故該路徑為每輪 24M、3 輪 **72M**（與 README 所述 72M 一致）。
- `multi_total_rollouts` 以最大策略網格 (N_open=40, N_sched=12) 計算理論上界
  96M（S=50,000）；該網格**並未**被示範路徑使用。對外引用請標明所屬路徑。
- [修正] 舊版此處誤植「示範路徑使用 S=50,000 → 每輪 9.6M」；9.6M 實為 S=20,000。
"""
from __future__ import annotations
import json
import os
import time
from typing import Dict, Any, List, Tuple
import numpy as np
from src.sim.scenarios import UMBRELLA
from . import model, solver
from .multi_solver import simulate_reverse_auction
from .roofline import label as roofline_label
from .roofline import load as load_roofline
from .roofline import rounded_lower_bound_seconds


def detect_gpu_hardware() -> Tuple[bool, str]:
    """檢測本機或容器是否具備可用之 GPU / ROCm 後端。"""
    try:
        import cupy as cp
        dev_count = cp.cuda.runtime.getDeviceCount()
        if dev_count > 0:
            dev_name = cp.cuda.runtime.getDeviceProperties(0)["name"].decode("utf-8")
            return True, f"CuPy/ROCm GPU ({dev_name})"
    except Exception:
        pass

    try:
        import torch
        if torch.cuda.is_available():
            dev_name = torch.cuda.get_device_name(0)
            return True, f"PyTorch ROCm/CUDA ({dev_name})"
    except Exception:
        pass

    return False, "CPU Vectorized (NumPy SIMD Fallback)"


def _measure_working_set_mb(n: int, opens, scheds, use_gpu: bool) -> float:
    """以 tracemalloc 實測 solve() 的峰值配置量 (MB)。

    [修正] 舊版以 `n_rollouts * 8` 估算工作集，並在註解中聲稱 float32。
    兩者皆錯：
      1. `solver.solve()` 內部把張量轉為 float64（見 solver.py 的 astype(xp.float64)），
         不是 float32；
      2. 8 bytes/rollout 遠低於實際配置量 —— 實測 9.6M rollouts 的峰值為 666.5 MB，
         而舊公式只給 73.2 MB，**低估 9.1 倍**。
    由於工作集是 roofline 下界的分子，低估會直接放大所有推導出的數字。
    """
    import tracemalloc

    tracemalloc.start()
    try:
        solver.solve(n_opp_samples=n, opens=opens, scheds=scheds, use_gpu=use_gpu)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / (1024 * 1024)


def run(n_list=(2_000, 10_000, 20_000, 50_000), force_cpu=False) -> Dict[str, Any]:
    """執行全鏈路基準測試並產出具備審計效力的硬體效能報告。

    [重要] 本函式**不**回報「GPU 加速倍率」。原因：在無 GPU 的機器上，唯一能
    推導的 GPU 數字是「把工作集從記憶體搬一趟所需的時間」(working_set ÷ 頻寬)，
    那是**資料搬運**下界，不是運算時間。把它與 CPU 的**運算**時間相除沒有
    物理意義（舊版曾把該比值印成加速倍率）。因此只回報各自可辯護的量。
    下界的標籤是 theoretical roofline lower-bound estimate, not measured。
    """
    has_gpu, hardware_name = detect_gpu_hardware()
    use_gpu = has_gpu and not force_cpu

    opens, scheds = model.strategy_grid()
    grid_size = opens.shape[0] * scheds.shape[0]  # 40 * 12 = 480
    rows: List[Dict[str, Any]] = []

    for n in n_list:
        t0 = time.perf_counter()
        _ = solver.solve(n_opp_samples=n, opens=opens, scheds=scheds, use_gpu=use_gpu)
        dt_cpu = time.perf_counter() - t0
        n_rollouts = n * grid_size

        # 實測工作集（見 _measure_working_set_mb 的修正說明）。
        working_set_mb = _measure_working_set_mb(n, opens, scheds, use_gpu)

        if use_gpu:
            t_gpu_0 = time.perf_counter()
            _ = solver.solve(n_opp_samples=n, opens=opens, scheds=scheds, use_gpu=True)
            dt_gpu_measured = time.perf_counter() - t_gpu_0
            transfer_s = None
            mode = "MEASURED_GPU"
        else:
            dt_gpu_measured = None
            # Bytes / datasheet peak. Labelled estimate, not a runtime.
            transfer_s = rounded_lower_bound_seconds(working_set_mb)
            mode = "CPU_MEASURED_WITH_TRANSFER_BOUND"

        rows.append({
            "samples": int(n),
            "rollouts": int(n_rollouts),
            "working_set_mb": round(working_set_mb, 2),
            "working_set_measured": True,
            "cpu_seconds": round(dt_cpu, 4),
            "us_per_rollout": round(dt_cpu * 1e6 / n_rollouts, 4),
            # 僅為資料搬運下界，非執行時間；GPU 實機時則為實測值。
            "mi300x_transfer_lower_bound_seconds": (
                transfer_s if transfer_s is not None else round(dt_gpu_measured, 6)
            ),
            "mi300x_transfer_lower_bound_label": (
                roofline_label() if transfer_s is not None else None
            ),
            "mi300x_measured_seconds": round(dt_gpu_measured, 6) if dt_gpu_measured is not None else None,
            "execution_mode": mode,
        })

    # 多供應商張量逆向拍賣模擬基準測試 (Reverse Auction 4-Supplier Benchmark)
    t_multi_0 = time.perf_counter()
    multi_res = simulate_reverse_auction(
        target_price=UMBRELLA.target,
        walk_away_ceiling=UMBRELLA.ceiling,
        moq=10000,
        n_samples=50_000,
        use_gpu=use_gpu,
    )
    dt_multi_cpu = time.perf_counter() - t_multi_0
    total_rollouts_multi = multi_res.get("total_rollouts_accum") or multi_res.get("total_rollouts")
    theoretical_max_rollouts = 4 * 40 * 12 * 50_000

    summary = {
        "hardware_detected": hardware_name,
        "is_gpu_active": use_gpu,
        "roofline_specs": _roofline_specs(),
        "single_supplier_grid": rows,
        "multi_supplier_auction": {
            "total_rollouts": total_rollouts_multi,
            "theoretical_max_rollouts": theoretical_max_rollouts,
            "cpu_seconds": round(dt_multi_cpu, 3),
            "winning_price": multi_res.get("winning_price"),
            # Historical bench.json stores the ceiling ratio in savings_pct.
            # Keep that key on the ceiling so a later run does not silently
            # rewrite 0.2222 into the first-quote headline.
            "savings_pct": multi_res.get("savings_vs_ceiling_pct"),
            "savings_pct_baseline": "walk_away_ceiling",
            "headline_savings_pct": multi_res.get("total_savings_pct"),
            "headline_savings_baseline": multi_res.get("total_savings_baseline"),
        },
        "disclosure": _disclosure(),
    }
    return summary


def _roofline_specs() -> Dict[str, Any]:
    """Copy the cited constants. No host DDR figure is added here."""
    src = load_roofline()
    return {
        "label": src["label"],
        "formula": src["formula"],
        "peak_bandwidth_tb_s": src["peak_bandwidth_tb_s"],
        "peak_bandwidth_gb_s": src["peak_bandwidth_gb_s"],
        "peak_bandwidth_bytes_per_s": src["peak_bandwidth_bytes_per_s"],
        "hbm3_capacity_gb": src["hbm3_capacity_gb"],
        "datasheet_url": src["datasheet_url"],
        "datasheet_quote_bandwidth": src["datasheet_quote_bandwidth"],
        "datasheet_quote_capacity": src["datasheet_quote_capacity"],
        "product_page_url": src["product_page_url"],
        "source_file": "results/mi300x_roofline.json",
        "note": (
            f"{src['label']}. Not a runtime and not a speedup. "
            "Host DDR bandwidth is omitted because this bench file does not "
            "name a CPU and the datasheet does not state one."
        ),
    }


def _disclosure() -> str:
    src = load_roofline()
    return (
        "No AMD GPU was present on the measuring host unless is_gpu_active is true. "
        f"mi300x_transfer_lower_bound_seconds is a {src['label']}. "
        f"Formula: {src['formula']}. {src['working_set_derivation']} "
        f"{src['datasheet_quote_bandwidth']} ({src['datasheet_url']}). "
        f"{src['datasheet_quote_capacity']}. "
        "Do not divide the bound by cpu_seconds to claim a speedup."
    )


def save_json(summary: Dict[str, Any], out_dir: str = "results") -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "bench.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return path


if __name__ == "__main__":
    report = run()
    print("=================================================================")
    print(f"TradeFlow Hardware Benchmark Profile: {report['hardware_detected']}")
    print("=================================================================")
    specs = report["roofline_specs"]
    print(f"{specs['label']}")
    print(f"Formula: {specs['formula']}")
    print(f"Datasheet: {specs['datasheet_quote_bandwidth']} — {specs['datasheet_url']}")
    print("-----------------------------------------------------------------")
    for r in report["single_supplier_grid"]:
        bound = r["mi300x_transfer_lower_bound_seconds"]
        print(f"Samples: {r['samples']:>6,} | Rollouts: {r['rollouts']:>10,} | "
              f"CPU (measured): {r['cpu_seconds']:>6.3f}s | "
              f"Working set (measured): {r['working_set_mb']:>8.2f} MB | "
              f"MI300X transfer bound: {bound*1000:>9.4f} ms [{r['execution_mode']}]")
    print("-----------------------------------------------------------------")
    m = report["multi_supplier_auction"]
    print(f"Multi-Supplier Auction ({m['total_rollouts']:,} rollouts): CPU measured {m['cpu_seconds']:.2f}s")
    print("-----------------------------------------------------------------")
    print(report["disclosure"])
    saved = save_json(report)
    print(f"[Audit Report Saved] -> {saved}")
