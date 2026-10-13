"""TradeFlow 引擎 — 讓步提案生成器（決策核心的產品化輸出）。

引擎只給數字（開盤/曲線/邊際/破局）；這層把它轉成：
1. 逐輪 counter-offer 時間表（給採購人員直接照表執行）。
2. 人類可讀建議文案（英文，給 Evolus agent 當 context / 給人看）。
3. 風險標註（破局率超過門檻就提醒人類審批）。
"""
from __future__ import annotations
import numpy as np
from . import model, solver


def build_proposal(spec: dict, observed_offer: float | None = None):
    """spec: 同 /negotiate 的欄位。回傳 dict（含逐輪 price 表 + 文案）。"""
    post_mean = spec.get("prior_reserve_mean", 1.08)
    post_std = spec.get("prior_reserve_std", 0.10)
    if observed_offer is not None:
        post_mean, post_std = solver.bayes_update(post_mean, post_std, observed_offer)
    res = solver.solve(
        n_opp_samples=spec.get("n_samples", 20000),
        lambda_break=spec.get("lambda_break", 1.0),
        reserve_mean=post_mean, reserve_std=post_std,
    )
    rec = solver.recommend(res, target_price=spec.get("target_price", 12.0))
    tp = spec.get("target_price", 12.0)
    open_norm = rec["opening_offer_norm"]
    curve = rec["concession_curve"]

    # 逐輪報價：開盤後每輪依 curve 讓步（相對 gap 的線性近似，便於人執行）
    offers = [open_norm]
    cur = open_norm
    for c in curve:
        cur = cur + c * (model.YOUR_CEILING - open_norm)  # 朝上限方向讓（漲價=讓步）
        # 注意：對採購方，「讓步」= 提高出價；朝 ceiling 收斂
        offers.append(min(cur, model.YOUR_CEILING))
    offers_px = [round(o * tp, 2) for o in offers]

    risk = "APPROVE" if rec["breakdown_prob"] < 0.15 else "HUMAN_REVIEW"
    text = (
        f"Recommended opening ¥{offers_px[0]:.2f} (target ¥{tp:.2f}, walk-away ceiling "
        f"¥{model.YOUR_CEILING*tp:.2f}). Concede {curve[0]*100:.0f}% per round per the schedule. "
        f"Expected margin ¥{rec['expected_margin_price']:.2f}/unit, breakdown risk "
        f"{rec['breakdown_prob']:.1%}. Decision: {risk}."
    )
    return {
        "opening_offer_price": offers_px[0],
        "round_by_round_prices": offers_px,        # 含開盤共 ROUNDS+1 格
        "concession_curve": [round(float(c), 3) for c in curve],
        "expected_margin_price": round(rec["expected_margin_price"], 2),
        "breakdown_prob": round(float(rec["breakdown_prob"]), 4),
        "posterior_reserve_mean": round(float(post_mean), 3),
        "posterior_reserve_std": round(float(post_std), 3),
        "risk_flag": risk,
        "advice_text": text,
    }


if __name__ == "__main__":
    spec = {"target_price": 12.0, "n_samples": 20000}
    p = build_proposal(spec, observed_offer=1.25)
    import json
    print(json.dumps(p, ensure_ascii=False, indent=2))
