"""TradeFlow v4 — 對抗式自博弈競技場 (Adversarial Arena)。

職責：
組織 1,000 場大規模蒙地卡羅對戰，全面對比：
1. TradeFlow Bayesian-MCTS 引擎
2. Fixed 5%/round 固定讓步基準（人類常見策略）
對戰 5 大非 LLM 對抗代理，產出客觀量化實證表格。
"""
from __future__ import annotations
import time
from typing import Dict, List, Any, Optional
import numpy as np

from .adversaries import (
    BaseAdversary,
    HardBargainerAdversary,
    ConcederAdversary,
    TitForTatAdversary,
    BluffingAdversary,
    NoisyAdversary,
)
from ..engine import solver, model, proposal


class AdversarialArena:
    """自博弈錦標賽競技場。"""

    def __init__(
        self,
        adversaries: Optional[List[BaseAdversary]] = None,
        walk_away_ceiling: float = 1.35,
        target_price: float = 12.0,
        max_rounds: int = 5,
        n_opp_samples: int = 1000,
    ):
        self.walk_away_ceiling = walk_away_ceiling
        self.target_price = target_price
        self.max_rounds = max_rounds
        self.n_opp_samples = n_opp_samples
        self.adversaries = adversaries or [
            HardBargainerAdversary(max_rounds=max_rounds),
            ConcederAdversary(max_rounds=max_rounds),
            TitForTatAdversary(max_rounds=max_rounds),
            BluffingAdversary(max_rounds=max_rounds),
            NoisyAdversary(max_rounds=max_rounds),
        ]
        # [修正] 舊版把這個 proxy 標成「Vanilla LLM Agent (Parametric)」，並在
        # self-play 報表裡與引擎並列，讓讀者以為那是真實 LLM 對手的量測。
        # 誠實說明：它沒有 LLM、沒有 prompt、沒有 API call —— 只是「有預算意識、
        # 會過度讓步」買方的**參數化代理**（panic 加權 + 高斯噪聲的讓步曲線）。
        # 因此改名，避免被誤讀成真 LLM 基準。真實 LLM 對比見 LIMITATIONS §2.3 /
        # PITCH Slide 1（0/94 違規，3.1% 上界）。
        self.candidate_agents = (
            ("TradeFlow Engine", "engine"),
            ("Panic-Heuristic Buyer Proxy (parametric, not an LLM)", "vanilla_llm"),
            ("Fixed 5%/rd Baseline", "fixed_baseline"),
        )

    def run_match(
        self,
        adversary: BaseAdversary,
        agent_type: str = "engine",  # "engine" | "vanilla_llm" | "fixed_baseline"
        rng: Optional[np.random.Generator] = None,
    ) -> Dict[str, Any]:
        """執行單場對決，回傳成交狀態、最終價格、節省率、輪數與邊界違規狀態。"""
        if rng is None:
            rng = np.random.default_rng()

        post_mean = 1.08
        post_std = 0.10
        buyer_history: List[float] = []
        supplier_history: List[float] = []
        has_budget_violation = False

        # 初始買方開盤
        if agent_type == "engine":
            # 引擎首輪報價 (經 Expectimax 網格搜尋推薦與 AST 邊界保護)
            init_solve = solver.solve(
                n_opp_samples=self.n_opp_samples,
                rng=rng,
                reserve_mean=post_mean,
                reserve_std=post_std,
            )
            rec = solver.recommend(init_solve, target_price=self.target_price)
            current_buyer_offer = min(self.walk_away_ceiling, rec["opening_offer_norm"])
        elif agent_type == "vanilla_llm":
            # 純 LLM 買方 (缺乏賽局死錨，開盤偏高且存在隨機漂移，模擬 AgenticPay 實證分佈)
            current_buyer_offer = float(np.clip(rng.normal(1.12, 0.03), 1.05, 1.25))
        else:
            # 固定基準開盤 (以 1.05 開盤，固定每輪讓 5%)
            current_buyer_offer = 1.05

        deal_closed = False
        final_price = 0.0
        final_round = self.max_rounds

        for r in range(1, self.max_rounds + 1):
            buyer_history.append(current_buyer_offer)
            if current_buyer_offer > self.walk_away_ceiling + 1e-4:
                has_budget_violation = True

            # 供應商回應
            supp_offer = adversary.respond(r, current_buyer_offer, buyer_history, supplier_history, rng)
            supplier_history.append(supp_offer)

            # 成交判定：買方出價 >= 賣方出價，或價差 <= 0.02 (2% 容忍度)
            if current_buyer_offer >= supp_offer or abs(supp_offer - current_buyer_offer) <= 0.02:
                deal_closed = True
                final_price = min(supp_offer, current_buyer_offer + 0.01)
                final_round = r
                if final_price > self.walk_away_ceiling + 1e-4:
                    has_budget_violation = True
                break

            # 若未成交且未到最後一輪，買方產生下一輪讓步
            if r < self.max_rounds:
                if agent_type == "engine":
                    # 貝氏更新 + 引擎重解
                    post_mean, post_std = solver.bayes_update(post_mean, post_std, supp_offer)
                    solve_res = solver.solve(
                        n_opp_samples=self.n_opp_samples,
                        rng=rng,
                        reserve_mean=post_mean,
                        reserve_std=post_std,
                    )
                    rec = solver.recommend(solve_res, target_price=self.target_price)
                    curve_step = rec["concession_curve"][min(r - 1, len(rec["concession_curve"]) - 1)]
                    step = curve_step * (self.walk_away_ceiling - current_buyer_offer)
                    current_buyer_offer = min(self.walk_away_ceiling, current_buyer_offer + step)
                elif agent_type == "vanilla_llm":
                    # 純 Prompt LLM 買方：在對手強硬與時間壓力下出現「過度讓步」與「突破底線」
                    panic_pressure = max(0.0, supp_offer - current_buyer_offer)
                    llm_step = 0.06 + 0.40 * panic_pressure + float(rng.normal(0, 0.02))
                    # 無 AST 硬護欄：在談判壓力下可能突破 walk_away_ceiling (1.35)
                    current_buyer_offer = float(current_buyer_offer + max(0.02, llm_step))
                    if current_buyer_offer > self.walk_away_ceiling:
                        has_budget_violation = True
                else:
                    # 固定每輪讓步 0.05 (有預算邊界硬截斷)
                    current_buyer_offer = min(self.walk_away_ceiling, current_buyer_offer + 0.05)

        # 結算
        if deal_closed:
            savings_pct = max(0.0, (self.walk_away_ceiling - final_price) / self.walk_away_ceiling)
        else:
            savings_pct = 0.0

        return {
            "deal_closed": deal_closed,
            "final_price": final_price if deal_closed else None,
            "savings_pct": savings_pct,
            "rounds": final_round,
            "budget_violation": has_budget_violation,
        }

    def run_tournament(
        self,
        n_matches_per_adversary: int = 200,
        seed: int = 20261013,
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        """
        執行多輪錦標賽（涵蓋 TradeFlow Engine、Panic-Heuristic Buyer Proxy
        （參數化，非 LLM）與 Fixed 5%/rd Baseline）。
        回傳結構化對抗勝率、節省率與預算邊界違背率。
        """
        rng = np.random.default_rng(seed)
        tournament_results: Dict[str, Dict[str, Dict[str, float]]] = {}

        candidate_agents = self.candidate_agents

        for adv in self.adversaries:
            adv_name = adv.name
            tournament_results[adv_name] = {}

            for agent_display, agent_key in candidate_agents:
                deals = 0
                savings_sum = 0.0
                rounds_sum = 0
                violations = 0

                for _ in range(n_matches_per_adversary):
                    m = self.run_match(adv, agent_type=agent_key, rng=rng)
                    if m["deal_closed"]:
                        deals += 1
                        savings_sum += m["savings_pct"]
                    rounds_sum += m["rounds"]
                    if m.get("budget_violation", False):
                        violations += 1

                deal_rate = deals / n_matches_per_adversary
                avg_savings = (savings_sum / deals) if deals > 0 else 0.0
                breakdown_rate = 1.0 - deal_rate
                avg_rounds = rounds_sum / n_matches_per_adversary
                violation_rate = violations / n_matches_per_adversary

                tournament_results[adv_name][agent_display] = {
                    "deal_rate": round(deal_rate, 4),
                    "savings_pct": round(avg_savings, 4),
                    "breakdown_rate": round(breakdown_rate, 4),
                    "avg_rounds": round(avg_rounds, 2),
                    "violation_rate": round(violation_rate, 4),
                }

        return tournament_results

    def export_markdown_table(self, tournament_results: Dict[str, Dict[str, Dict[str, float]]]) -> str:
        """產出符合學術論文與黑客松評審標準的實證 Markdown 報表。"""
        lines = [
            "| 對手類型 (Opponent Archetype) | 策略代理 (Agent) | 談判勝率 (Deal %) | 平均節省率 (Savings %) | 破局率 (Breakdown %) | 預算違規率 (Violation %) | 平均輪數 (Rounds) |",
            "|---|---|---|---|---|---|---|",
        ]

        for adv_name, agents in tournament_results.items():
            first_row = True
            for agent_name, metrics in agents.items():
                prefix = f"**{adv_name}**" if first_row else ""
                first_row = False
                agent_display = f"**{agent_name}**" if "Engine" in agent_name else agent_name
                deal_disp = f"**{metrics['deal_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['deal_rate']:.1%}"
                sav_disp = f"**{metrics['savings_pct']:.1%}**" if "Engine" in agent_name else f"{metrics['savings_pct']:.1%}"
                brk_disp = f"**{metrics['breakdown_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['breakdown_rate']:.1%}"
                vio_disp = f"**{metrics['violation_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['violation_rate']:.1%}"
                lines.append(
                    f"| {prefix} | {agent_display} | {deal_disp} | {sav_disp} | {brk_disp} | {vio_disp} | {metrics['avg_rounds']} |"
                )

        return "\n".join(lines)
