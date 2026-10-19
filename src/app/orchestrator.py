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
from ..engine import bayes_update, proposal
from ..engine.bayesian_v2 import robust_location
from ..engine.one_step import one_step_counter
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


def robustness_report(target_price: float, n_samples: int, force: bool = False, value_ceiling: float | None = None):
    """Legacy demo grid. The live webhook does not call this."""
    if value_ceiling is None:
        raise ValueError("robustness_report requires value_ceiling")
    from ..engine import robustness
    key = (float(target_price), int(n_samples), float(value_ceiling))
    if not force and key in _ROBUSTNESS_CACHE:
        return _ROBUSTNESS_CACHE[key]
    txt, data = robustness.report(
        target_price=target_price, n_samples=n_samples, value_ceiling=value_ceiling,
    )
    data = dict(data)
    data["computed"] = True
    _ROBUSTNESS_CACHE[key] = data
    return data


def analyze(spec: dict, observed_offer: float | None = None, quick: bool = False, demo: bool = False):
    """One-step counter for a caller-supplied target, ceiling, and quote.

    ``demo=True`` is the only path that fills a missing target or ceiling
    from the labelled simulated mandate.
    """
    spec = dict(spec)
    target = spec.get("target_price")
    ceiling = spec.get("walk_away_ceiling", spec.get("ceiling_price"))
    if demo and (target is None or ceiling is None):
        target = target if target is not None else DEMO_SPEC["target_price"]
        ceiling = ceiling if ceiling is not None else DEMO_SPEC["ceiling_price"]
        spec["label"] = "simulated"
    if target is None or ceiling is None:
        raise ValueError("target_price and a ceiling are required")
    quote_price = spec.get("quote_price")
    if quote_price is None and observed_offer is not None:
        quote_price = float(observed_offer) * float(target)
    if quote_price is None:
        raise ValueError("a supplier quote is required")
    markup = float(spec.get("expected_markup") or 0.0)
    width_source = "quote_location"
    if spec.get("prior_reserve_mean") is None:
        post_mean, post_std = robust_location([float(quote_price) / float(target) - markup])
    else:
        post_mean = float(spec["prior_reserve_mean"])
        raw_std = spec.get("prior_reserve_std")
        if raw_std is None:
            if not demo:
                raise ValueError(
                    "prior_reserve_std is required when prior_reserve_mean is set"
                )
            raw_std = DEMO_SPEC["prior_reserve_std"]
            width_source = "labelled_demo"
        else:
            width_source = "user"
        post_std = float(raw_std)
        if observed_offer is not None:
            post_mean, post_std = bayes_update(
                post_mean, post_std, float(observed_offer), estimated_markup=markup,
            )
    n_samples = spec.get("n_samples")
    rec = one_step_counter(
        quote=float(quote_price),
        target=float(target),
        ceiling=float(ceiling),
        mean=float(post_mean),
        std=float(post_std),
        n_samples=n_samples,
        risk_tolerance=spec.get("risk_tolerance"),
    )
    force_on_webhook = os.environ.get("TRADEFLOW_ROBUSTNESS_ON_WEBHOOK") == "1"
    if quick and not force_on_webhook:
        rob = _ROBUSTNESS_CACHE.get((float(target), 2000)) or _robustness_placeholder(float(target))
    else:
        rob = _robustness_placeholder(float(target))
        rob["reason"] = (
            "The live path does not run the legacy robustness grid. "
            "That grid is the labelled demo CLI."
        )
    prop = proposal.build_proposal(
        {
            "target_price": float(target),
            "walk_away_ceiling": float(ceiling),
            "prior_reserve_mean": float(post_mean),
            "prior_reserve_std": float(post_std),
            "quote_price": float(quote_price),
            "n_samples": n_samples,
        },
        observed_offer=None,
    )
    rec = dict(rec)
    rec["concession_curve"] = prop["concession_curve"]
    return {
        "recommendation": rec,
        "posterior_reserve_mean": float(post_mean),
        "posterior_reserve_std": float(post_std),
        "robustness": rob,
        "proposal": prop,
        "data_class": spec.get("label") or "user_supplied",
        "prior_width_source": width_source,
    }


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
    extracted = payload.get("extracted") or {}
    spec = {
        "target_price": extracted.get("target_price"),
        "walk_away_ceiling": extracted.get("walk_away_ceiling") or extracted.get("ceiling_price"),
        "n_samples": extracted.get("n_samples"),
        "prior_reserve_mean": extracted.get("prior_reserve_mean"),
        "prior_reserve_std": extracted.get("prior_reserve_std"),
        "product": extracted.get("product"),
    }
    offer = payload.get("offer")
    result = analyze(spec, observed_offer=offer, quick=True, demo=bool(payload.get("demo")))
    rec = result["recommendation"]
    opening = rec.get("opening_offer_price")
    opening_text = "withheld" if opening is None else f"¥{opening:.2f}"
    notes = (f"Supplier offer analysis (product={spec.get('product')}): "
             f"recommended opening {opening_text}, "
             f"concession curve {rec['concession_curve']}, "
             f"expected margin ¥{rec['expected_margin_price']:.2f}/unit, "
             f"breakdown prob {rec['breakdown_prob']:.1%}. "
             f"Draft a professional procurement reply.")
    draft = draft_email(notes, reply_to=payload.get("email_body"))
    result["draft_email"] = draft
    return result


if __name__ == "__main__":
    from .demo_data import SUPPLIER_REPLIES
    payload = {
        "demo": True,
        "extracted": {"product": DEMO_SPEC["product"], "target_price": DEMO_SPEC["target_price"]},
        "offer": SUPPLIER_REPLIES[0],
        "email_body": (
            "Simulated supplier reply for the offline self-check. "
            "The numeric offer is the scripted walkthrough reply, not a factory quote."
        ),
    }
    out = on_webhook(payload)
    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))
