"""引擎冒煙測試：確保 solve / recommend / bayes_update 在 CPU 上可跑且輸出合理。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine import solve, recommend, bayes_update, model, proposal


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


def test_solve_edge_cases():
    # 極端 prior：std=0（確定對手）不應 NaN/炸
    res = solve(n_opp_samples=1000, reserve_mean=1.1, reserve_std=0.0, use_gpu=False)
    assert res["exp_margin"].shape == (40, 12)
    assert not (res["exp_margin"] != res["exp_margin"]).any()  # 無 NaN
    # 極小樣本
    res2 = solve(n_opp_samples=10, use_gpu=False)
    assert res2["exp_margin"].shape == (40, 12)


def test_bayes_update():
    m, s = bayes_update(1.08, 0.10, 1.25)  # 供應商回高價 → 後驗上移
    assert m > 1.08 and s < 0.10


def test_proposal():
    p = proposal.build_proposal({"target_price": 12.0, "n_samples": 1000}, observed_offer=1.25)
    assert len(p["round_by_round_prices"]) == model.ROUNDS + 1
    assert p["round_by_round_prices"][0] == p["opening_offer_price"]
    assert p["risk_flag"] in ("APPROVE", "HUMAN_REVIEW")
    assert p["advice_text"]


if __name__ == "__main__":
    test_strategy_grid()
    test_solve_runs()
    test_solve_edge_cases()
    test_bayes_update()
    test_proposal()
    print("ALL ENGINE SMOKE TESTS PASSED")
