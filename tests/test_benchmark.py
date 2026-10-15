"""Benchmark + 視覺化 smoke 測試：確保 benchmark 與三張圖都能跑。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine import benchmark, robustness, viz


def test_benchmark_runs():
    res = benchmark.run(n_list=(2000,))
    grid = res["single_supplier_grid"]
    row = grid[0]
    assert row["rollouts"] > 0
    assert row["cpu_seconds"] > 0
    # [修正] 舊 schema 誤把 GPU 時間寫成一個 `mi300x_seconds`「實測值」，
    # 實際上是 CPU 計算時間 ÷ 122,425（假加速比）得來。現已改為誠實暴露為
    # 資料搬運下界：working_set ÷ 記憶體頻寬。測試改鎖定新欄位與語意。
    assert row["working_set_mb"] > 0
    assert row["working_set_measured"] is True
    # 下界僅為高速資料搬運物理下限（遠小於 CPU 計算時間），且必須是有限正數。
    lb = row["mi300x_transfer_lower_bound_seconds"]
    assert lb > 0 and lb < row["cpu_seconds"], (
        "transfer lower bound must be a physical floor below the CPU runtime"
    )
    assert row["execution_mode"] in ("ROOFLINE_ANALYTICAL_LOWER_BOUND", "MEASURED_GPU", "CPU_MEASURED_WITH_TRANSFER_BOUND")
    # 不再允許任何舊 schema 的假加速比 / 假實測秒數欄位。
    assert "speedup_factor" not in row
    assert "mi300x_seconds" not in row


def test_figures_no_error():
    # 回歸：不寫檔也應可產生 figure 物件
    rows = benchmark.run(n_list=(2000, 10000))
    fig1 = viz.plot_benchmark(rows)
    assert fig1 is not None
    _, rob = robustness.report(n_samples=2000)
    fig2 = viz.plot_robustness(rob)
    assert fig2 is not None
    # report() 必須同時回傳兩類敏感度（場景敏感度與信念錯誤）
    assert "sensitivity" in rob and "misbelief" in rob


def test_misbelief_sensitivity_structure():
    """misbelief 測試（現實固定、僅信念偏移）的結構契約。

    鎖定結構與量綱不變量，不鎖定具體數值 —— 若未來模型改進使不對稱性消失，
    本測試不應假性失敗；數值結論記錄於 LIMITATIONS §十三。
    """
    from src.engine.robustness import misbelief_sensitivity

    res = misbelief_sensitivity(n_samples=3000)
    assert len(res) == 3
    factors = [f for f, _, _, _ in res]
    assert factors == [0.5, 1.0, 1.5]

    for f, belief_mean, margin, breakdown in res:
        assert belief_mean == 1.08 * f, "belief mean must track the factor"
        assert margin == margin and abs(margin) < 10.0, "margin must be finite/sane"
        assert 0.0 <= breakdown <= 1.0, "breakdown must be a probability"

    # 信念準確（×1.0）時，規劃與現實一致：策略在現實中應有正期望邊際。
    base = next(r for r in res if r[0] == 1.0)
    assert base[2] > 0.0, "accurate belief should retain positive expected margin"

    # 無論偏移方向多大，破局率必須仍在合法範圍且不為 NaN（防回歸到壞數值）。
    assert all(b == b for _, _, _, b in res)


if __name__ == "__main__":
    test_benchmark_runs()
    test_figures_no_error()
    print("ALL BENCHMARK/VIZ TESTS PASSED")
