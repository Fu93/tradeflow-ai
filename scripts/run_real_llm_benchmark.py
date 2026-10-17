"""TradeFlow — REAL LLM adversarial benchmark (A-level measurement).

這個腳本以**真實 AMD 託管模型**擔任買方，對抗 5 類數學策略對手原型，
量測真實的成交率、節省率與**預算違規率**。

與 `src/benchmark/arena.py` 的關鍵差別：
  * arena.py 的 `vanilla_llm` 是**參數化模擬器**（合成數據，C 級）。
  * 本腳本**每一輪都真實呼叫** AMD tokenfactory 上的模型（A 級實測）。

[誠實設計]
  * LLM 回傳無法解析為數字時，**明確記錄為 parse_failure**，絕不猜測或代換為預設值。
  * 每次呼叫記錄延遲、token 數、實際成本（取自 API 回傳的 usage）。
  * 推理模型需要較大 token 預算：實測 max_tokens=500/2000 會把預算全耗在
    reasoning 上而回傳空字串；4000 才能拿到實際答案。此為實測得到的參數。

用法：
    python scripts/run_real_llm_benchmark.py --matches 20            # 小規模驗證
    python scripts/run_real_llm_benchmark.py --matches 100 --adversaries all
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 自動載入 .env（憑證不進版控；佔位符視為未設定）
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from src.benchmark.adversaries import (
    BluffingAdversary,
    ConcederAdversary,
    HardBargainerAdversary,
    NoisyAdversary,
    TitForTatAdversary,
)

from src.config.heuristics import (  # noqa: E402
    BENCHMARK_FINAL_STEP,
    DEAL_TOLERANCE,
    PLAUSIBLE_OFFER_MAX,
    PLAUSIBLE_OFFER_MIN,
)
from src.sim.scenarios import UMBRELLA  # noqa: E402

# Simulated umbrella mandate. The benchmark prices a fictional SKU so the
# model can be scored. It is not a factory quote.
TARGET_PRICE = UMBRELLA.target
WALK_AWAY_CEILING = UMBRELLA.ceiling
CEILING_NORM = WALK_AWAY_CEILING / TARGET_PRICE

ADVERSARY_FACTORY = {
    "Hard-Bargainer": lambda rounds: HardBargainerAdversary(max_rounds=rounds),
    "Conceder": lambda rounds: ConcederAdversary(max_rounds=rounds),
    "Tit-for-Tat": lambda rounds: TitForTatAdversary(max_rounds=rounds),
    "Bluffing Hardliner": lambda rounds: BluffingAdversary(max_rounds=rounds),
    "Noisy Seller": lambda rounds: NoisyAdversary(max_rounds=rounds),
}


class RealLLMBuyer:
    """以真實 AMD 託管模型擔任買方決策者。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 4000,
        timeout: float = 300.0,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        # 累計統計
        self.calls = 0
        self.parse_failures = 0
        self.unit_anomalies = 0
        self.api_failures = 0
        self.total_reasoning_tokens = 0
        self.total_cost_usd = 0.0
        self.total_latency_s = 0.0

    def _call_api(self, prompt: str) -> Tuple[Optional[str], Dict[str, Any]]:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(self.retries):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self.calls += 1
                self.total_latency_s += time.perf_counter() - t0
                usage = payload.get("usage") or {}
                self.total_reasoning_tokens += usage.get("reasoning_tokens") or 0
                self.total_cost_usd += usage.get("cost") or 0.0
                content = payload["choices"][0]["message"].get("content") or ""
                return content, usage
            except Exception as exc:  # noqa: BLE001
                if attempt < self.retries - 1:
                    time.sleep(4 * (attempt + 1))
                    continue
                self.api_failures += 1
                return None, {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return None, {}

    @staticmethod
    def _parse_offer(text: str) -> Optional[float]:
        """從回覆抽取買方出價（人民幣元）。

        優先解析 JSON 的 `offer_cny` 鍵（prompt 要求之格式），
        失敗才退回取最後一個數字。解析失敗回 None，絕不猜測。
        """
        if not text or not text.strip():
            return None
        # 1) JSON 優先（prompt 規定的回傳格式）
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                for key in ("offer_cny", "offer", "price"):
                    if key in data and data[key] is not None:
                        return float(str(data[key]).replace(",", ""))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        # 2) 退回：取最後一個數字
        nums = re.findall(r"\d+\.?\d*", text.replace(",", ""))
        if not nums:
            return None
        try:
            return float(nums[-1])
        except ValueError:
            return None

    def build_prompt(self, round_idx: int, max_rounds: int,
                     supplier_history: List[float], buyer_history: List[float]) -> str:
        """構造買方決策 prompt。

        [重要 — 單位換算]
        `supplier_history` / `buyer_history` 皆為**歸一化值**（1.0 = 目標價），
        來自對抗 Agent 與 `decide_offer` 的回傳。顯示給模型前**必須乘以
        TARGET_PRICE 換回人民幣**，否則 prompt 會出現「工廠報價 ¥1.33」這種
        荒謬內容——實測這正是模型輸出漂移與推理暴增（3327 reasoning tokens）
        的根本原因。

        [實測修正 — prompt 格式迭代結論]
        1. 初版提及「歸一化 1.35」→ 模型在兩種單位間搖擺，延遲 ~190s。
        2. 改用 JSON 信封 `{"offer_cny": ...}` → 單位由 key 名稱錨定，輸出穩定。
        """
        target = TARGET_PRICE
        ceiling = WALK_AWAY_CEILING
        head = (
            "你是一名企業採購員，負責從中國工廠採購一批自動雨傘。\n"
            f"你的目標價是 ¥{target:.2f}，公司財務允許的最高上限是 ¥{ceiling:.2f}。\n"
        )
        if not supplier_history:
            body = "工廠業務員即將給出第一輪報價。\n"
        else:
            hist = "\n".join(
                f"  第 {i} 輪：工廠報價 ¥{q * target:.2f}，你還價 ¥{b * target:.2f}"
                for i, (q, b) in enumerate(zip(supplier_history, buyer_history), start=1)
            )
            body = (
                f"目前已談判 {round_idx - 1} 輪（最多 {max_rounds} 輪）：\n{hist}\n"
                f"工廠本輪（第 {round_idx} 輪）最新報價：¥{supplier_history[-1] * target:.2f}\n"
            )
        return head + body + '只回傳 JSON，不要任何其他文字：{"offer_cny": 13.20}'

    def decide_offer(self, round_idx: int, max_rounds: int,
                     supplier_history: List[float], buyer_history: List[float]) -> Dict[str, Any]:
        prompt = self.build_prompt(round_idx, max_rounds, supplier_history, buyer_history)
        content, usage = self._call_api(prompt)
        if content is None:
            return {"offer_norm": None, "raw": None, "error": usage.get("error", "api_failure")}
        offer_cny = self._parse_offer(content)
        if offer_cny is None:
            self.parse_failures += 1
            return {"offer_norm": None, "raw": content[:200], "error": "parse_failure"}
        # 合理性帶檢查：落在 ¥5–¥30 之外視為單位/格式異常，明確記錄而非靜默接受
        if not (PLAUSIBLE_OFFER_MIN <= offer_cny <= PLAUSIBLE_OFFER_MAX):
            self.unit_anomalies += 1
            return {"offer_norm": None, "raw": content[:200], "error": "unit_anomaly",
                    "parsed_cny": offer_cny}
        return {"offer_norm": offer_cny / TARGET_PRICE, "raw": content[:200], "error": None}


def run_match(buyer: RealLLMBuyer, adversary, max_rounds: int = 5,
              rng=None) -> Dict[str, Any]:
    """執行一場真實 LLM 買方 vs 數學對手的多輪談判。"""
    import numpy as np
    if rng is None:
        rng = np.random.default_rng()

    buyer_history: List[float] = []
    supplier_history: List[float] = []
    parse_failures = 0
    api_failures = 0
    unit_anomalies = 0

    deal_closed = False
    final_price = 0.0
    final_round = max_rounds
    has_budget_violation = False

    # 買方開盤（真實 LLM）
    opening = buyer.decide_offer(1, max_rounds, supplier_history, buyer_history)
    if opening["error"]:
        reason = opening["error"]
        if reason == "parse_failure":
            parse_failures += 1
        elif reason == "unit_anomaly":
            unit_anomalies += 1
        else:
            api_failures += 1
        # 解析/單位異常/呼叫失敗：記錄並中止本場，不猜測數字
        return {"deal_closed": False, "final_price": None, "savings_pct": 0.0,
                "rounds": 0, "budget_violation": False, "aborted": True,
                "abort_reason": reason, "trace": []}

    current_buyer_offer = float(opening["offer_norm"])
    buyer_history.append(current_buyer_offer)
    if current_buyer_offer > CEILING_NORM + 1e-4:
        has_budget_violation = True

    trace = [{"round": 1, "buyer_offer_norm": current_buyer_offer, "buyer_raw": opening["raw"]}]

    for r in range(1, max_rounds + 1):
        if current_buyer_offer > CEILING_NORM + 1e-4:
            has_budget_violation = True

        supp_offer = float(adversary.respond(r, current_buyer_offer, buyer_history, supplier_history, rng))
        supplier_history.append(supp_offer)

        # 成交判定（與 arena.py 相同規則）
        if current_buyer_offer >= supp_offer or abs(supp_offer - current_buyer_offer) <= DEAL_TOLERANCE:
            deal_closed = True
            final_price = min(supp_offer, current_buyer_offer + BENCHMARK_FINAL_STEP)
            final_round = r
            if final_price > CEILING_NORM + 1e-4:
                has_budget_violation = True
            break

        if r < max_rounds:
            nxt = buyer.decide_offer(r + 1, max_rounds, supplier_history, buyer_history)
            if nxt["error"]:
                reason = nxt["error"]
                if reason == "parse_failure":
                    parse_failures += 1
                elif reason == "unit_anomaly":
                    unit_anomalies += 1
                else:
                    api_failures += 1
                return {"deal_closed": False, "final_price": None, "savings_pct": 0.0,
                        "rounds": r, "budget_violation": has_budget_violation, "aborted": True,
                        "abort_reason": reason, "trace": trace}
            current_buyer_offer = float(nxt["offer_norm"])
            buyer_history.append(current_buyer_offer)
            trace.append({"round": r + 1, "buyer_offer_norm": current_buyer_offer,
                          "buyer_raw": nxt["raw"]})

    savings_pct = max(0.0, (CEILING_NORM - final_price) / CEILING_NORM) if deal_closed else 0.0
    return {
        "deal_closed": deal_closed,
        "final_price": final_price if deal_closed else None,
        "savings_pct": savings_pct,
        "rounds": final_round,
        "budget_violation": has_budget_violation,
        "aborted": False,
        "abort_reason": None,
        "parse_failures": parse_failures,
        "api_failures": api_failures,
        "trace": trace,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Real LLM adversarial benchmark on AMD endpoint.")
    ap.add_argument("--matches", type=int, default=20, help="matches per adversary archetype")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--adversaries", type=str, default="all",
                    help="comma-separated names, or 'all'")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=str, default="results/real_llm_benchmark.json")
    args = ap.parse_args()

    for var in ("VLLM_BASE_URL", "VLLM_API_KEY", "VLLM_MODEL"):
        if not os.getenv(var):
            print(f"[error] {var} not set in environment", file=sys.stderr)
            return 1

    base_url = os.environ["VLLM_BASE_URL"]
    api_key = os.environ["VLLM_API_KEY"]
    model = os.environ["VLLM_MODEL"]

    names = list(ADVERSARY_FACTORY) if args.adversaries == "all" else [
        n.strip() for n in args.adversaries.split(",") if n.strip()
    ]

    buyer = RealLLMBuyer(base_url, api_key, model)
    import numpy as np
    rng = np.random.default_rng(args.seed)

    print("=" * 78)
    print("TradeFlow REAL LLM Adversarial Benchmark (A-level measurement)")
    print(f"  endpoint   : {base_url}")
    print(f"  model      : {model}")
    print(f"  matches    : {args.matches} per archetype x {len(names)} archetypes")
    print(f"  max rounds : {args.rounds}")
    print("=" * 78)

    all_results: Dict[str, Any] = {
        "meta": {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "endpoint": base_url,
            "model": model,
            "matches_per_archetype": args.matches,
            "max_rounds": args.rounds,
            "seed": args.seed,
            "target_price_cny": TARGET_PRICE,
            "walk_away_ceiling_cny": WALK_AWAY_CEILING,
            "ceiling_norm": CEILING_NORM,
            "max_tokens": buyer.max_tokens,
            "data_level": "A - real LLM inference calls (not a parametric simulator)",
        },
        "archetypes": {},
    }

    grand_deals = grand_violations = grand_matches = 0
    grand_savings = 0.0

    for name in names:
        print(f"\n[{name}] running {args.matches} matches ...", flush=True)
        deals = violations = 0
        savings_sum = 0.0
        rounds_sum = 0
        aborted = 0
        abort_reasons: Dict[str, int] = {}
        t0 = time.perf_counter()

        for m in range(args.matches):
            adv = ADVERSARY_FACTORY[name](args.rounds)
            res = run_match(buyer, adv, max_rounds=args.rounds, rng=rng)
            if res["aborted"]:
                aborted += 1
                abort_reasons[res["abort_reason"]] = abort_reasons.get(res["abort_reason"], 0) + 1
                continue
            if res["deal_closed"]:
                deals += 1
                savings_sum += res["savings_pct"]
            if res["budget_violation"]:
                violations += 1
            rounds_sum += res["rounds"]
            if (m + 1) % 5 == 0:
                print(f"    {m + 1}/{args.matches} done "
                      f"({time.perf_counter() - t0:.0f}s elapsed)", flush=True)

        completed = args.matches - aborted
        all_results["archetypes"][name] = {
            "matches": args.matches,
            "completed": completed,
            "aborted": aborted,
            "abort_reasons": abort_reasons,
            "deal_rate": round(deals / completed, 4) if completed else None,
            "savings_pct": round(savings_sum / deals, 4) if deals else None,
            "breakdown_rate": round(1 - deals / completed, 4) if completed else None,
            "violation_rate": round(violations / completed, 4) if completed else None,
            "avg_rounds": round(rounds_sum / completed, 2) if completed else None,
            "wall_seconds": round(time.perf_counter() - t0, 1),
        }
        grand_deals += deals
        grand_violations += violations
        grand_matches += completed
        grand_savings += savings_sum
        print(f"    -> deal {deals}/{completed}, violation {violations}/{completed}, "
              f"aborted {aborted}", flush=True)

    all_results["totals"] = {
        "completed_matches": grand_matches,
        "deal_rate": round(grand_deals / grand_matches, 4) if grand_matches else None,
        "savings_pct": round(grand_savings / grand_deals, 4) if grand_deals else None,
        "violation_rate": round(grand_violations / grand_matches, 4) if grand_matches else None,
        "llm_calls": buyer.calls,
        "llm_parse_failures": buyer.parse_failures,
        "llm_unit_anomalies": buyer.unit_anomalies,
        "llm_api_failures": buyer.api_failures,
        "llm_reasoning_tokens": buyer.total_reasoning_tokens,
        "llm_total_cost_usd": round(buyer.total_cost_usd, 6),
        "llm_avg_latency_s": round(buyer.total_latency_s / buyer.calls, 2) if buyer.calls else None,
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(f"Saved: {args.out}")
    print(f"  completed matches : {grand_matches}")
    print(f"  deal rate         : {all_results['totals']['deal_rate']}")
    print(f"  savings pct       : {all_results['totals']['savings_pct']}")
    print(f"  violation rate    : {all_results['totals']['violation_rate']}")
    print(f"  LLM calls         : {buyer.calls} (parse fail {buyer.parse_failures}, "
          f"unit anomaly {buyer.unit_anomalies}, api fail {buyer.api_failures})")
    print(f"  total cost        : ${buyer.total_cost_usd:.4f}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
