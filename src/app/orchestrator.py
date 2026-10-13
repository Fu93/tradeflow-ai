"""TradeFlow Orchestrator — 我們 app 驅動 Evolus 的膠水層。

流程（對應計畫 §3 的 app 驅動拓撲）：
  Evolus workflow (email-in → extractor) → 回呼我們 /webhook/evolus
    → 我們跑引擎 → 把 JSON 結果當 context
    → 呼叫 Evolus AI Tool (email) 讓 agent 寫回覆草稿
    → 人類審批 → 送出

設計：Evolus 呼叫走 EvolusClient（需 key）。若沒有 key，自動降為
offline 模式（印出草稿而不真呼叫），保證 demo 在任何環境能跑。
"""
from __future__ import annotations
import os
from ..engine import solve, recommend, bayes_update, model, robustness, viz, proposal
from .demo_data import DEMO_SPEC


def analyze(spec: dict, observed_offer: float | None = None):
    """跑引擎，回傳建議 + 穩健性摘要 + 讓步提案。"""
    post_mean = spec.get("prior_reserve_mean", DEMO_SPEC["prior_reserve_mean"])
    post_std = spec.get("prior_reserve_std", DEMO_SPEC["prior_reserve_std"])
    if observed_offer is not None:
        post_mean, post_std = bayes_update(post_mean, post_std, observed_offer)
    res = solve(n_opp_samples=spec.get("n_samples", DEMO_SPEC["n_samples"]),
                lambda_break=spec.get("lambda_break", DEMO_SPEC["lambda_break"]),
                reserve_mean=post_mean, reserve_std=post_std)
    rec = recommend(res, target_price=spec.get("target_price", DEMO_SPEC["target_price"]))
    _, rob = robustness.report(target_price=spec.get("target_price", DEMO_SPEC["target_price"]),
                              n_samples=spec.get("n_samples", DEMO_SPEC["n_samples"]))
    prop = proposal.build_proposal(spec, observed_offer=observed_offer)
    return {"recommendation": rec, "posterior_reserve_mean": post_mean,
            "posterior_reserve_std": post_std, "robustness": rob, "proposal": prop}


def draft_email(notes: str, reply_to: str | None = None) -> str:
    """用 Evolus agent 起草回覆；offline 模式回傳 local 草稿。"""
    try:
        from ..evolus import EvolusClient
        client = EvolusClient()
        out = client.run_email_tool(notes, reply_to=reply_to)
        return out or notes
    except Exception as e:
        # offline 兜底：不依賴外部 key
        prefix = "[DRAFT reply]" if reply_to else "[DRAFT]"
        return f"{prefix} {notes} (offline stub: {e})"


def on_webhook(payload: dict) -> dict:
    """Evolus workflow 回呼入口。

    payload: {extracted: {product, target_price, qty, ...}, offer: 歸一化回價?}
    """
    spec = dict(DEMO_SPEC)
    spec.update({k: v for k, v in payload.get("extracted", {}).items()
                 if k in ("target_price", "n_samples", "lambda_break",
                          "prior_reserve_mean", "prior_reserve_std")})
    offer = payload.get("offer")
    result = analyze(spec, observed_offer=offer)
    rec = result["recommendation"]
    notes = (f"Supplier offer analysis (product={spec.get('product')}): "
             f"recommended opening ¥{rec['opening_offer_price']:.2f}, "
             f"concession curve {rec['concession_curve']}, "
             f"expected margin ¥{rec['expected_margin_price']:.2f}/unit, "
             f"breakdown prob {rec['breakdown_prob']:.1%}. "
             f"Draft a professional procurement reply.")
    draft = draft_email(notes, reply_to=payload.get("email_body"))
    result["draft_email"] = draft
    return result


if __name__ == "__main__":
    # 離線自檢：模擬一封供應商回價 ¥15 的信進來
    payload = {"extracted": {"product": "auto umbrella", "target_price": 12.0},
               "offer": 1.25, "email_body": "We can do ¥15 per unit, MOQ 500."}
    out = on_webhook(payload)
    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))
