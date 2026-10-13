"""TradeFlow app — FastAPI 入口。

職責：接收詢價規格 → 呼叫引擎（貝氏 + 向量化 MCTS）→ 回傳建議 JSON。
Evolus 由我們 app 驅動（見 orchestrator.py）；引擎運算在 MI300X 上批量跑。
"""
from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ..engine import solve, recommend, bayes_update, robustness, model
from . import orchestrator

app = FastAPI(title="TradeFlow Negotiation Engine", version="0.1.0")


class Spec(BaseModel):
    product: str = "auto umbrella"
    target_price: float = 12.0          # ¥ per unit
    moq: int = 500
    rounds: int = model.ROUNDS
    n_samples: int = 20_000
    lambda_break: float = 1.0
    prior_reserve_mean: float = 1.08
    prior_reserve_std: float = 0.10
    observed_offer: float | None = None  # 供應商回價（歸一化，1.0=目標價）


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/negotiate")
def negotiate(spec: Spec):
    post_mean, post_std = spec.prior_reserve_mean, spec.prior_reserve_std
    if spec.observed_offer is not None:
        post_mean, post_std = bayes_update(post_mean, post_std, spec.observed_offer)
    try:
        res = solve(n_opp_samples=spec.n_samples, use_gpu=False,
                    lambda_break=spec.lambda_break,
                    reserve_mean=post_mean, reserve_std=post_std)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    rec = recommend(res, target_price=spec.target_price)
    rec["posterior_reserve_mean"] = float(post_mean)
    rec["posterior_reserve_std"] = float(post_std)
    return rec


@app.post("/webhook/evolus")
def webhook(payload: dict):
    """Evolus workflow 回呼入口（email-in → extractor → 這裡）。"""
    try:
        return orchestrator.on_webhook(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
