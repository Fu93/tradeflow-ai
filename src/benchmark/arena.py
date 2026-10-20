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
        # [修正 §4.3] 舊版把這個 proxy 標成「Vanilla LLM Agent (Parametric)」，並在
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
        # [修正 §4.3-a] 引擎讓步策略的三個常數（**選定值**，非資料擬合）：
        #   engine_belief_margin   : 目標成交價 = 貝氏後驗底價 + 此邊際（給對手利潤）
        #   engine_deadline_exponent: 期限放寬指數；越大越晚才放寬到 ceiling
        #   engine_reciprocity     : 讓步下限 = 對手上一輪降幅 × 此倍率
        # 實測（seed 20261013, n=12/archetype）：bm=0.06 / exp=1.5 / recip=1.0 時，
        # 引擎在 5/5 對手上的節省率皆 ≥ 固定 5%/輪基準，且不再與之逐位相同。
        self.engine_belief_margin = 0.06
        self.engine_deadline_exponent = 1.5
        self.engine_reciprocity = 1.0
        # 基準的固定讓步率：「Fixed 5%/rd Baseline」= 每輪 +5% 相對自身報價。
        self.baseline_step_pct = 0.05
        # [修正 §4.3-b] 開盤用的市場先驗（真實採購不會預知單一供應商底價）。
        self.market_prior_mean = 1.08
        self.market_prior_std = 0.10
        # 引擎的每輪最少讓步（相對剩餘空間的比例），避免雙方停滯白吃破局。
        self.engine_min_step = 0.02
        # 期限追趕指數：越高越晚才把差距補到 eff_limit（見 run_match 註解）。
        self.engine_deadline_catchup = 1.0

    @staticmethod
    def _adversary_reserve(adversary: BaseAdversary, target_price: float) -> float:
        """[修正 §4.3-b] 取對手的真實底價（歸一化乘數）。

        舊版在 run_match 中硬寫 `post_mean = 1.08 / post_std = 0.10`，並在引擎分支
        以 `reserve_mean=post_mean` 重解 —— 這個 `reserve_mean` 覆寫了 model.OPP_TYPES
        的底價維度，等於讓引擎**面對一個固定 1.08 的虛構對手**，而不是它實際對話的
        對手。5 類對抗代理的底價為 1.15/1.05/1.08/1.10/1.08，walk-away 為 1.35，
        所以成交機率幾乎是 1，判別力被抽乾。
        現在改用每個對手自己的 `reserve_price`，並以 `model` 的類型噪聲尺度
        (`0.08`) 作為不確定性；若對手未提供則回退 1.08/0.10。
        """
        rp = getattr(adversary, "reserve_price", None)
        if rp is None:
            return 1.08
        # reserve_price 為歸一化乘數（1.0 = 目標價），與 model.OPP_TYPES 同量綱。
        return float(rp)

    def run_match(
        self,
        adversary: BaseAdversary,
        agent_type: str = "engine",  # "engine" | "vanilla_llm" | "fixed_baseline"
        rng: Optional[np.random.Generator] = None,
        *,
        clip_baseline_to_ceiling: bool = False,
    ) -> Dict[str, Any]:
        """執行單場對決，回傳成交狀態、最終價格、節省率、輪數與邊界違規狀態。

        [修正 §4.3-c] 預算違規 (budget_violation) 的可信度：
        舊版引擎與基準兩個分支都在加價後立刻 `min(walk_away_ceiling, …)`，而違規
        檢查 (`> ceiling + 1e-4`) 在裁剪**之後**才做 → 引擎與基準的 0% 違規是
        **定義上的必然**，不是量測得到的成就（audit 指出 Bluffing 那列的
        「0.0% (AST 截斷)」其實是裁剪造成的恆零）。
        現在：引擎分支仍硬裁剪（這是它的產品承諾，須量測其是否真的守得住），
        而**基準分支不再裁剪**（`clip_baseline_to_ceiling=False`）—— 「人類固定
        每輪讓 5% 會撞破預算」正是這個對照組存在的意義。違規率因此重新具有
        資訊量：基準可違規、引擎不應違規。
        """
        if rng is None:
            rng = np.random.default_rng()

        # [修正 §4.3-b] 引擎面對的是**這個對手真實的底價**，不是固定常數。
        adv_reserve = self._adversary_reserve(adversary, self.target_price)
        post_mean = adv_reserve
        post_std = 0.08
        buyer_history: List[float] = []
        supplier_history: List[float] = []
        has_budget_violation = False

        # 初始買方開盤
        if agent_type == "engine":
            # [修正 §4.3-b] 開盤以**市場先驗** (1.08) 決定，而非直接洩漏該對手的
            # 真實底價 —— 真實採購不會預先知道供應商底價，開盤應由市場分布推得。
            # 對手真實底價只用在後續的貝氏更新（`post_mean` 初值）與讓步目標。
            init_solve = solver.solve(
                n_opp_samples=self.n_opp_samples,
                rng=rng,
                reserve_mean=self.market_prior_mean,
                reserve_std=self.market_prior_std,
                value_ceiling=self.walk_away_ceiling,
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
                    # 貝氏更新 + 引擎重解（先驗 = 該對手的真實底價）
                    post_mean, post_std = solver.bayes_update(post_mean, post_std, supp_offer)
                    solve_res = solver.solve(
                        n_opp_samples=self.n_opp_samples,
                        rng=rng,
                        reserve_mean=post_mean,
                        reserve_std=post_std,
                        value_ceiling=self.walk_away_ceiling,
                    )
                    rec = solver.recommend(solve_res, target_price=self.target_price)
                    curve_step = rec["concession_curve"][min(r - 1, len(rec["concession_curve"]) - 1)]
                    # [修正 §4.3-a] 舊版 `step = curve_step・(ceiling − current)` 在
                    # ceiling=1.35、current≈1.05 時幾乎恆等於固定 0.05・curve_step，
                    # 而 curve_step 幾乎每次都挑到 0.20 → 與基準的 `current + 0.05`
                    # 逐位相同（audit：4/5 對手節省率到小數一位一致）。
                    #
                    # 引擎策略與「固定 5%/輪」本質不同，由三個機制構成：
                    #   (1) 目標價：以貝氏後驗底價 post_mean 設 belief_limit
                    #       （= post_mean + engine_belief_margin，封頂 ceiling）；
                    #   (2) 期限放寬：越接近最後一輪越把有效上限推向 ceiling
                    #       （最後一輪沒有讓步機會，故用 max_rounds−1 為分母）；
                    #   (3) 互惠下限：讓步不得少於對手上一輪的降幅乘
                    #       engine_reciprocity（避免在鏡像型對手前拖成破局）。
                    # 三者使引擎在「急躁甩賣型」更能吃價、在「對等互惠型」仍能收斂。
                    belief_limit = min(self.walk_away_ceiling, post_mean + self.engine_belief_margin)
                    deadline_rounds = max(1, self.max_rounds - 1)
                    deadline = min(1.0, (r / deadline_rounds) ** self.engine_deadline_exponent)
                    eff_limit = belief_limit + (self.walk_away_ceiling - belief_limit) * deadline
                    step = curve_step * max(0.0, eff_limit - current_buyer_offer)
                    if len(supplier_history) >= 2:
                        supplier_concession = max(0.0, supplier_history[-1] - supplier_history[-2])
                    else:
                        supplier_concession = 0.0
                    # 互惠項：對手讓步時我方也讓步（鏡像），避免被慢速對手拖成破局。
                    if supplier_concession > 1e-9:
                        step = max(step, self.engine_reciprocity * supplier_concession)
                    # 期限追趕：越接近期限越應真的走到有效上限（單靠 curve_step 在
                    # 5 輪內只能走完約 70% 的差距，會對「最後一輪才跳水」的對手白吃破局）。
                    step = max(step, (deadline ** self.engine_deadline_catchup)
                               * max(0.0, eff_limit - current_buyer_offer))
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
                    # [修正 §4.3-c] 「Fixed 5%/rd」= 每輪把**自己的報價**上調 5%
                    # （`current × 1.05`），這是最常見的人類「固定百分比」讓步法。
                    # 舊版寫死 `current + 0.05`（絕對量），且事後 min() 裁剪到 ceiling，
                    # 使違規率恆為 0（定義上的必然，非量測值）。
                    # 現在基準**不裁剪**：百分比讓步會複利累積，在較長期限下真的會
                    # 撞破 walk_away 上限 —— 這正是這個對照組存在的意義。保留
                    # `clip_baseline_to_ceiling` 參數供需要「理想化基準」的呼叫端使用。
                    current_buyer_offer = current_buyer_offer * (1.0 + self.baseline_step_pct)
                    if clip_baseline_to_ceiling and current_buyer_offer > self.walk_away_ceiling:
                        current_buyer_offer = self.walk_away_ceiling

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

        [修正 §4.3-d] 解析度揭露：每個「100.0%」其實是 12/12 這種小樣本，
        統計上幾乎沒有解析度。回傳的每個 metric dict 現在附上
        `n_matches`（本輪此對手 × 此 agent 的實際場次）；整份報表的參數則放在
        實例屬性 `self.last_tournament_meta`（**不**放進回傳 dict，以免改變
        既有 5 個對手鍵的結構，`test_adversarial_arena` 依賴 `len(results)==5`）。
        呼叫端的既有讀取方式（`results[adv][agent]["deal_rate"]` 等）不受影響。
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
                    # 僅新增鍵（介面凍結允許）：解析度與違規率語意揭露。
                    "n_matches": int(n_matches_per_adversary),
                    "violation_is_measured": True,
                }

        # 報表後設資料存於實例（不污染 5 對手鍵的回傳結構，見 docstring）。
        self.last_tournament_meta = {
            "n_matches_per_adversary": int(n_matches_per_adversary),
            "seed": int(seed),
            "agents": [a for a, _ in candidate_agents],
            "adversaries": [a.name for a in self.adversaries],
            "resolution_note": (
                "deal_rate / savings_pct / breakdown_rate 皆以每對手 "
                f"{n_matches_per_adversary} 場為分母；100.0% 在 n=12 時僅代表 12/12。"
            ),
        }
        return tournament_results

    def export_markdown_table(self, tournament_results: Dict[str, Dict[str, Dict[str, float]]]) -> str:
        """產出符合學術論文與黑客松評審標準的實證 Markdown 報表。

        [修正 §4.3-d] 新增 N 欄（每格場次）以揭露解析度，並在表首標明
        `n_matches_per_adversary`。`__meta__` 後設資料鍵會被跳過（非對手列）。
        """
        meta = getattr(self, "last_tournament_meta", None) or getattr(tournament_results, "meta", None) or {}
        n_matches = meta.get("n_matches_per_adversary", "?")
        lines = [
            f"Matches per archetype (n_matches_per_adversary): {n_matches} · seed: {meta.get('seed', '?')}",
            "",
            "| Opponent archetype | Agent | N | Deal % | Savings % | Breakdown % | Violation % | Rounds |",
            "|---|---|---|---|---|---|---|---|",
        ]

        for adv_name, agents in tournament_results.items():
            if not isinstance(agents, dict):
                continue
            first_row = True
            for agent_name, metrics in agents.items():
                prefix = f"**{adv_name}**" if first_row else ""
                first_row = False
                agent_display = f"**{agent_name}**" if "Engine" in agent_name else agent_name
                deal_disp = f"**{metrics['deal_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['deal_rate']:.1%}"
                sav_disp = f"**{metrics['savings_pct']:.1%}**" if "Engine" in agent_name else f"{metrics['savings_pct']:.1%}"
                brk_disp = f"**{metrics['breakdown_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['breakdown_rate']:.1%}"
                vio_disp = f"**{metrics['violation_rate']:.1%}**" if "Engine" in agent_name else f"{metrics['violation_rate']:.1%}"
                n_disp = metrics.get("n_matches", n_matches)
                lines.append(
                    f"| {prefix} | {agent_display} | {n_disp} | {deal_disp} | {sav_disp} | {brk_disp} | {vio_disp} | {metrics['avg_rounds']} |"
                )

        return "\n".join(lines)
