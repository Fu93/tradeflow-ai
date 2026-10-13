"""Benchmark + 視覺化 smoke 測試：確保 benchmark 與三張圖都能跑。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine import benchmark, robustness, viz


def test_benchmark_runs():
    rows = benchmark.run(n_list=(2000,))
    assert rows[0]["rollouts"] > 0
    assert rows[0]["seconds"] > 0
    assert rows[0]["mi300x_projected_seconds"] > 0


def test_figures_no_error():
    # 回歸：不寫檔也應可產生 figure 物件
    rows = benchmark.run(n_list=(2000, 10000))
    fig1 = viz.plot_benchmark(rows)
    assert fig1 is not None
    _, rob = robustness.report(n_samples=2000)
    fig2 = viz.plot_robustness(rob)
    assert fig2 is not None


if __name__ == "__main__":
    test_benchmark_runs()
    test_figures_no_error()
    print("ALL BENCHMARK/VIZ TESTS PASSED")
