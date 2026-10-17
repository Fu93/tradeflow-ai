"""TradeFlow 引擎 — 讓步提案生成器（決策核心的產品化輸出）。

引擎只給數字（開盤/曲線/邊際/破局）；這層把它轉成：
1. 逐輪 counter-offer 時間表（給採購人員直接照表執行）。
2. 人類可讀建議文案（英文，給 Evolus agent 當 context / 給人看）。
3. 風險標註（破局率超過門檻就提醒人類審批）。
"""
from __future__ import annotations
import numpy as np
from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD
from src.sim.scenarios import SUPPLIER_REPLIES, UMBRELLA
from . import model, solver


def build_proposal(spec: dict, observed_offer: float | None = None):
    """spec: 同 /negotiate 的欄位。回傳 dict（含逐輪 price 表 + 文案）。"""
    post_mean = spec.get("prior_reserve_mean", PRIOR_RESERVE_MEAN)
    post_std = spec.get("prior_reserve_std", PRIOR_RESERVE_STD)
    if observed_offer is not None:
        post_mean, post_std = solver.bayes_update(post_mean, post_std, observed_offer)

    tp = spec.get("target_price", UMBRELLA.target)
    # [修正/誠實揭露] 舊版把讓步軌跡的上限硬寫為 model.YOUR_CEILING (=規格目標
    # 價的 1.35 倍)，完全忽略 spec 可能傳入的 walk_away_ceiling —— 若呼叫端把
    # ceiling 設成別的值（例如 1.20 倍），proposal 仍會一路讓到 1.35 倍而破框。
    # 現在沿用 spec 的 walk_away_ceiling 並歸一化為 target_price 的倍數；缺省時
    # 才退回 model.YOUR_CEILING。求解器本身不感知 walk_away_ceiling，其固定的
    # ceiling 常數揭露見 model.YOUR_CEILING —— proposal 這層是唯一能尊重該框的位置。
    ceiling_mult = spec.get("walk_away_ceiling")
    if ceiling_mult is not None and tp > 0:
        ceiling_mult = ceiling_mult / tp
    else:
        ceiling_mult = model.YOUR_CEILING
    ceiling_mult = max(ceiling_mult, 1.0)

    res = solver.solve(
        n_opp_samples=spec.get("n_samples", 20000),
        lambda_break=spec.get("lambda_break", 1.0),
        reserve_mean=post_mean, reserve_std=post_std,
    )
    rec = solver.recommend(res, target_price=tp)
    open_norm = rec["opening_offer_norm"]
    curve = rec["concession_curve"]

    # [修正/誠實揭露] 求解器的開盤網格上限固定為 model.YOUR_CEILING(=1.35)，完全不感知
    # 呼叫端的 walk_away_ceiling。若呼叫端把 ceiling 設得比該網格上限更緊（例如 1.20 倍），
    # 舊版開盤價 open_norm 會直接沿用網格挑出的值（最高可到 1.33 倍）而不做裁剪，
    # 造成 `opening_offer_price` 突破自己宣稱的 walk-away ceiling —— 違反「AI 不越過上限」承諾。
    # 現於本層唯一能尊重該上限的位置強制裁剪開盤價，並讓「讓步起點」ref 同時用裁剪後值，
    # 使逐輪讓步也在有效範圍內收斂。
    open_norm = min(open_norm, ceiling_mult)
    ref = open_norm
    curve = rec["concession_curve"]

    # 逐輪報價：開盤後每輪依 curve 讓步（相對 gap 的線性近似，便於人執行）
    offers = [ref]
    cur = ref
    for c in curve:
        cur = cur + c * (ceiling_mult - ref)  # 朝上限方向讓（漲價=讓步）
        # 注意：對採購方，「讓步」= 提高出價；朝 ceiling 收斂
        offers.append(min(cur, ceiling_mult))
    offers_px = [round(o * tp, 2) for o in offers]

    risk = (
        "APPROVE"
        if rec["breakdown_prob"] < model.BREAKDOWN_TRIP_THRESHOLD
        else "HUMAN_REVIEW"
    )
    text = (
        f"Recommended opening ¥{offers_px[0]:.2f} (target ¥{tp:.2f}, walk-away ceiling "
        f"¥{ceiling_mult*tp:.2f}). Concede {curve[0]*100:.0f}% per round per the schedule. "
        f"Expected margin ¥{rec['expected_margin_price']:.2f}/unit, breakdown risk "
        f"{rec['breakdown_prob']:.1%}. Decision: {risk}."
    )
    return {
        "opening_offer_price": offers_px[0],
        "round_by_round_prices": offers_px,        # 含開盤共 ROUNDS+1 格
        "ceiling_ref_multiple": round(float(ceiling_mult), 3),
        "concession_curve": [round(float(c), 3) for c in curve],
        "expected_margin_price": round(rec["expected_margin_price"], 2),
        "breakdown_prob": round(float(rec["breakdown_prob"]), 4),
        "posterior_reserve_mean": round(float(post_mean), 3),
        "posterior_reserve_std": round(float(post_std), 3),
        "risk_flag": risk,
        "advice_text": text,
    }


if __name__ == "__main__":
    spec = {"target_price": UMBRELLA.target, "n_samples": 20000}
    p = build_proposal(spec, observed_offer=SUPPLIER_REPLIES[0])
    import json
    print(json.dumps(p, ensure_ascii=False, indent=2))
