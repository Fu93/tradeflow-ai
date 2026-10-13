"""引擎冒煙測試：確保 solve / recommend / bayes_update 在 CPU 上可跑且輸出合理。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine import solve, recommend, bayes_update, model


def test_strategy_grid():
    opens, scheds = model.strategy_grid()
    assert opens.shape[0] == 40 and scheds.shape == (12, model.ROUNDS)


def test_solve_runs():
    res = solve(n_opp_samples=2000, use_gpu=False)
    assert res["exp_margin"].shape == (40, 12)
    assert 0.0 <= res["exp_break"][res["best"]] <= 1.0
    rec = recommend(res, target_price=12.0)
    assert rec["opening_offer_price"] > 0
    assert 0.0 <= rec["breakdown_prob"] <= 1.0


def test_bayes_update():
    m, s = bayes_update(1.08, 0.10, 1.25)  # 供應商回高價 → 後驗上移
    assert m > 1.08 and s < 0.10


if __name__ == "__main__":
    test_strategy_grid()
    test_solve_runs()
    test_bayes_update()
    print("ALL ENGINE SMOKE TESTS PASSED")
