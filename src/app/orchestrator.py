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

# [修正 §6.6] 穩健性報告快取：robustness.report() 內部會跑 10 次以上 solve()。
# 舊版在 `analyze(quick=True)`（= 每個未認證的 /webhook/evolus 請求）都重算一次，
# 使該端點成為一個被浪費的 DoS 放大器。現在：
#   1. quick=True（互動/webhook 路徑）預設**不重算**穩健性，只回傳已有快取
#      （若無快取則回傳一個明確標示為「未計算」的 placeholder，絕不回 None/拋錯）；
#   2. 完整報告只在 quick=False（離線 CLI / 圖表產生）時計算，並存入快取；
#   3. 可用環境變數 TRADEFLOW_ROBUSTNESS_ON_WEBHOOK=1 強制回到舊行為（供 demo 展示）。
_ROBUSTNESS_CACHE: dict = {}


def _robustness_placeholder(target_price: float) -> dict:
    """輕量佔位摘要（**不**執行任何 solve()）。鍵集與真實報告不同，故以
    `computed` 標記區分；呼叫端若需要完整報告應改用 quick=False。"""
    return {
        "computed": False,
        "reason": "robustness report skipped on the interactive/webhook path "
                  "to avoid an unauthenticated DoS amplifier (see DEEP_DEFECT_AUDIT §6.6)",
        "target_price": target_price,
        "gain": None,
        "baseline_margin": None,
        "engine_margin": None,
        "multi_baseline": [],
        "sensitivity": [],
        "misbelief": [],
    }


def robustness_report(target_price: float, n_samples: int, force: bool = False):
    """取得（並快取）穩健性報告。`force=False` 時，快取命中即回傳。"""
    key = (float(target_price), int(n_samples))
    if not force and key in _ROBUSTNESS_CACHE:
        return _ROBUSTNESS_CACHE[key]
    txt, data = robustness.report(target_price=target_price, n_samples=n_samples)
    data = dict(data)
    data["computed"] = True
    _ROBUSTNESS_CACHE[key] = data
    return data


def analyze(spec: dict, observed_offer: float | None = None, quick: bool = False):
    """跑引擎，回傳建議 + 穩健性摘要 + 讓步提案。

    [修正 §6.6] `quick=True` 時**不再**在請求路徑上重算 robustness.report()。
    行為差異：`result["robustness"]["computed"]` 為 False 且欄位為 placeholder；
    需要完整報告的離線路徑請用 quick=False（或先呼叫 robustness_report()）。
    """
    post_mean = spec.get("prior_reserve_mean", DEMO_SPEC["prior_reserve_mean"])
    post_std = spec.get("prior_reserve_std", DEMO_SPEC["prior_reserve_std"])
    if observed_offer is not None:
        post_mean, post_std = bayes_update(post_mean, post_std, observed_offer)
    n_samples = spec.get("n_samples", DEMO_SPEC["n_samples"])
    res = solve(n_opp_samples=n_samples,
                lambda_break=spec.get("lambda_break", DEMO_SPEC["lambda_break"]),
                reserve_mean=post_mean, reserve_std=post_std)
    rec = recommend(res, target_price=spec.get("target_price", DEMO_SPEC["target_price"]))

    target_price = spec.get("target_price", DEMO_SPEC["target_price"])
    force_on_webhook = os.environ.get("TRADEFLOW_ROBUSTNESS_ON_WEBHOOK") == "1"
    if quick and not force_on_webhook:
        # 不再呼叫 robustness.report()（10+ 次 solve）；只用快取，缺快取則回 placeholder。
        rob = _ROBUSTNESS_CACHE.get((float(target_price), 2000)) or _robustness_placeholder(target_price)
    else:
        # 穩健性網格在完整路徑下降採樣至 2,000（耗時 2.4s vs 17.5s）
        rob_samples = 2000 if quick else n_samples
        rob = robustness_report(target_price=target_price, n_samples=rob_samples)
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
    result = analyze(spec, observed_offer=offer, quick=True)
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
    from src.sim.scenarios import SUPPLIER_REPLIES, UMBRELLA
    payload = {
        "extracted": {"product": UMBRELLA.name, "target_price": UMBRELLA.target},
        "offer": SUPPLIER_REPLIES[0],
        "email_body": (
            "Simulated supplier reply for the offline self-check. "
            "The numeric offer is the scripted walkthrough reply, not a factory quote."
        ),
    }
    out = on_webhook(payload)
    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))
