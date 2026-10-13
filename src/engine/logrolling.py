"""TradeFlow v4 — 企業授權多維條款置換引擎 (Corporate Mandate Logrolling Engine)。

數學模型：
在價格維度陷入僵局 (Price Stagnation) 或供應商強硬時，基於 Corporate Mandate 的凸約束集，
利用買賣雙方在現金流定金 (Deposit)、生產排期 (Lead Time)、模具費 (Mold Fee) 與起訂量 (MOQ)
上的非對稱邊際替代率 (MRS)，計算帕雷托最優契約對 (Logrolling Pareto Pair)，
並經由 AST 語法樹護欄安全驗證後，生成金牌買家多維置換商務話術。
"""
from __future__ import annotations
import ast
import re
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field, model_validator


class TradeoffType(str, Enum):
    """授權置換條款類型枚舉。"""
    DEPOSIT_FOR_LEADTIME = "DEPOSIT_FOR_LEADTIME"        # 高定金換交期壓縮
    VOLUME_FOR_PRICE = "VOLUME_FOR_PRICE"                # 放大訂單量換單價折讓
    PRICE_FOR_TOOLING = "PRICE_FOR_TOOLING"              # 鎖定單價換模具/打樣費全免
    DEPOSIT_FOR_PRICE = "DEPOSIT_FOR_PRICE"              # 高額定金換現金折扣
    PAYMENT_TERMS_FOR_PRICE = "PAYMENT_TERMS_FOR_PRICE"  # 現款結算換折讓


class CorporateMandate(BaseModel):
    """企業授權契約 (Corporate Mandate)。"""
    target_price: float = Field(..., gt=0.0, description="買方預期目標單價 (元)")
    walk_away_ceiling: float = Field(..., gt=0.0, description="買方絕對底線上限單價 (元)")
    baseline_lead_days: int = Field(default=25, gt=0, description="標準基準交期 (天)")
    max_acceptable_lead_days: int = Field(default=35, gt=0, description="最長可容忍交期 (天)")
    min_desired_lead_days: int = Field(default=12, gt=0, description="期望壓縮交期下限 (天)")
    lead_day_shadow_value: float = Field(default=0.15, ge=0.0, description="每提前一天商業影子單價 (元/天/件)")
    baseline_deposit_pct: float = Field(default=0.30, ge=0.0, le=1.0, description="基準定金比例 (30%)")
    max_allowed_deposit_pct: float = Field(default=0.50, ge=0.0, le=1.0, description="公司授權最高定金比例 (50%)")
    buyer_capital_cost_rate: float = Field(default=0.08, ge=0.0, description="買方年化資本成本率 (8%)")
    supplier_capital_relief_rate: float = Field(default=0.22, ge=0.0, description="供應商資金短缺緩釋年化收益率 (22%)")
    baseline_moq: int = Field(default=500, gt=0, description="基準標準採購量 (件)")
    max_allowed_moq: int = Field(default=1200, gt=0, description="最大允許採購量 (件)")
    excess_holding_cost_rate: float = Field(default=0.05, ge=0.0, description="超額庫存持有成本率 (5%)")
    mold_fee_exemption_demanded: bool = Field(default=True, description="是否要求免除模具費")
    estimated_mold_fee: float = Field(default=3000.0, ge=0.0, description="預估模具費總額 (元)")
    allowed_tradeoffs: List[TradeoffType] = Field(
        default_factory=lambda: [
            TradeoffType.DEPOSIT_FOR_LEADTIME,
            TradeoffType.VOLUME_FOR_PRICE,
            TradeoffType.PRICE_FOR_TOOLING,
        ],
        description="授權置換條款白名單"
    )

    @model_validator(mode="after")
    def validate_convex_bounds(self) -> CorporateMandate:
        if self.walk_away_ceiling < self.target_price:
            raise ValueError(f"上限價格 walk_away_ceiling ({self.walk_away_ceiling}) 不得低於目標價格 target_price ({self.target_price})")
        if self.max_acceptable_lead_days < self.baseline_lead_days:
            raise ValueError(f"最大容忍交期 ({self.max_acceptable_lead_days}) 不得短於基準交期 ({self.baseline_lead_days})")
        if self.min_desired_lead_days > self.baseline_lead_days:
            raise ValueError(f"期望交期下限 ({self.min_desired_lead_days}) 不得大於基準交期 ({self.baseline_lead_days})")
        if self.max_allowed_deposit_pct < self.baseline_deposit_pct:
            raise ValueError(f"最高許可定金 ({self.max_allowed_deposit_pct}) 不得低於基準定金 ({self.baseline_deposit_pct})")
        if self.max_allowed_moq < self.baseline_moq:
            raise ValueError(f"最大允許採購量 ({self.max_allowed_moq}) 不得低於基準採購量 ({self.baseline_moq})")
        return self


class SupplierCurrentOffer(BaseModel):
    """供應商當前堅持之報價條件。"""
    price: float = Field(..., gt=0.0, description="供應商堅持之單價 (元)")
    lead_days: int = Field(default=25, gt=0, description="供應商報出之交期 (天)")
    deposit_pct: float = Field(default=0.30, ge=0.0, le=1.0, description="供應商要求之定金比例")
    moq: int = Field(default=500, gt=0, description="供應商要求之起訂量 (件)")
    mold_fee: float = Field(default=3000.0, ge=0.0, description="供應商要求之模具費 (元)")
    is_price_stagnant: bool = Field(default=True, description="是否在單價上寸步不讓")


class LogrollingProposal(BaseModel):
    """單一多維置換方案資料規格。"""
    strategy_name: str
    proposed_price: float
    proposed_lead_days: int
    proposed_deposit_pct: float
    proposed_moq: int
    mold_fee_exempted: bool
    tradeoff_types_used: List[TradeoffType]
    buyer_surplus_value: float              # 買方相對於底線的總貨幣化剩餘 (元)
    buyer_effective_unit_saving: float      # 等效每件單價改善 (元/件)
    supplier_surplus_estimate: float        # 估計工廠總剩餘增量 (元)
    is_pareto_dominant: bool = False
    explanation: str
    buyer_script_zh: str = ""
    buyer_script_en: str = ""


class LogrollingResult(BaseModel):
    """多維置換計算結果總覽。"""
    best_recommendation: LogrollingProposal
    all_pareto_proposals: List[LogrollingProposal]
    original_supplier_offer: SupplierCurrentOffer
    total_candidates_evaluated: int
    ast_guardrail_passed: bool
    summary_report: str


class ASTMandateViolationError(Exception):
    """企業授權硬邊界違規異常。"""
    pass


class ASTMandateGuardrail:
    """AST 企業授權防禦護欄核心校驗器。"""

    @classmethod
    def compile_guardrail_ast(cls) -> ast.Module:
        code = """
def evaluate_mandate_invariants(props, mandate):
    assert props['proposed_price'] <= mandate['walk_away_ceiling'] + 1e-6, "AST: 單價超出 walk_away_ceiling"
    assert props['proposed_lead_days'] <= mandate['max_acceptable_lead_days'], "AST: 交期超出 max_acceptable_lead_days"
    assert props['proposed_deposit_pct'] <= mandate['max_allowed_deposit_pct'] + 1e-6, "AST: 定金超出 max_allowed_deposit_pct"
    assert props['proposed_moq'] <= mandate['max_allowed_moq'], "AST: 起訂量超出 max_allowed_moq"
    for t in props['tradeoff_types_used']:
        assert t in mandate['allowed_tradeoffs'], f"AST: 未授權條款 {t}"
    return True
"""
        return ast.parse(code)

    @classmethod
    def verify_proposal(
        cls,
        proposal: Dict[str, Any],
        mandate: CorporateMandate,
        buyer_script: str = "",
    ) -> bool:
        tree = cls.compile_guardrail_ast()
        compiled_scope: Dict[str, Any] = {}
        exec(compile(tree, filename="<ast_mandate_guardrail>", mode="exec"), compiled_scope)
        evaluator = compiled_scope["evaluate_mandate_invariants"]

        mandate_dict = {
            "walk_away_ceiling": mandate.walk_away_ceiling,
            "max_acceptable_lead_days": mandate.max_acceptable_lead_days,
            "max_allowed_deposit_pct": mandate.max_allowed_deposit_pct,
            "max_allowed_moq": mandate.max_allowed_moq,
            "allowed_tradeoffs": [t.value for t in mandate.allowed_tradeoffs],
        }

        try:
            evaluator(proposal, mandate_dict)
        except AssertionError as exc:
            raise ASTMandateViolationError(str(exc)) from exc

        if buyer_script:
            cls._audit_script_numerics(buyer_script, proposal, mandate)

        return True

    @classmethod
    def _audit_script_numerics(
        cls,
        script: str,
        proposal: Dict[str, Any],
        mandate: CorporateMandate,
    ) -> None:
        pct_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", script)
        for pct_str in pct_matches:
            val = float(pct_str) / 100.0
            if val > mandate.max_allowed_deposit_pct + 1e-4:
                raise ASTMandateViolationError(
                    f"AST: 話術承諾定金 {pct_str}% 超出公司最高授權 {mandate.max_allowed_deposit_pct*100:.1f}%"
                )

        day_matches = re.findall(r"(\d+)\s*天", script)
        for day_str in day_matches:
            val = int(day_str)
            if val > mandate.max_acceptable_lead_days:
                raise ASTMandateViolationError(
                    f"AST: 話術承諾交期 {val} 天超出公司最大容忍值 {mandate.max_acceptable_lead_days} 天"
                )


class BuyerScriptGenerator:
    """金牌買家多維置換商務話術生成器。"""

    @classmethod
    def generate_scripts(
        cls,
        proposal: LogrollingProposal,
        mandate: CorporateMandate,
        offer: SupplierCurrentOffer,
    ) -> Tuple[str, str]:
        p = proposal.proposed_price
        l = proposal.proposed_lead_days
        d_pct = int(round(proposal.proposed_deposit_pct * 100))
        q = proposal.proposed_moq
        mold_exempt = proposal.mold_fee_exempted

        # 中文金牌買家話術
        concession_parts = []
        if proposal.proposed_deposit_pct > offer.deposit_pct:
            concession_parts.append(f"我方今天由財務直接特批預付 {d_pct}% 定金現款秒結，助貴廠即刻鎖定原材料並鎖死產能")
        if q > offer.moq:
            concession_parts.append(f"首批訂單批量直接由 {offer.moq} 件拉升至 {q} 件")

        demand_parts = []
        if l < offer.lead_days:
            demand_parts.append(f"交期必須由 {offer.lead_days} 天全速壓縮至 {l} 天內交付")
        if mold_exempt:
            demand_parts.append(f"本期首批模具/打樣費 ¥{offer.mold_fee:.0f} 由貴廠全額減免")
        if p < offer.price:
            demand_parts.append(f"單價最終落定在 ¥{p:.2f}")

        concession_str = "，同時".join(concession_parts) if concession_parts else "維持我方優質付款條件"
        demand_str = "、".join(demand_parts)

        zh_script = (
            f"王總，理解貴廠在成本上的考量。若您堅持單價 ¥{p:.2f} 不動，我方展現最大的合作誠意：{concession_str}。"
            f"但作為對等條件，{demand_str}。"
            f"這是我們公司董事會特批的閉環置換方案。若貴方確認，我們今天下班前立即蓋章回傳 PO，首筆款項秒到賬；若此方案仍無法達成，我方只得啟動備案產能。"
        )

        # 英文外貿金牌買家話術
        en_script = (
            f"Dear Partner, regarding your quotation at ¥{p:.2f}/unit: To accommodate your cost constraints, "
            f"our treasury has approved an immediate {d_pct}% cash deposit today to secure raw materials and reserve capacity. "
            f"In return for this cashflow concession, production lead time must be expedited to {l} days, "
            f"and the tooling fee of ¥{offer.mold_fee:.0f} must be fully waived for this batch. "
            f"This represents our executive mandate for today's PO sign-off. Please confirm so we can release the payment immediately."
        )

        return zh_script, en_script


class LogrollingEngine:
    """企業授權多維條款置換引擎。"""

    @classmethod
    def evaluate(
        cls,
        mandate: CorporateMandate,
        offer: SupplierCurrentOffer,
    ) -> LogrollingResult:
        """主入口：搜尋帕雷托最優前沿並返回最優置換對。"""
        candidates: List[LogrollingProposal] = []

        # 基準：若維持工廠原樣條件
        base_proposal = cls._create_proposal(
            name="工廠原始報價 (Baseline)",
            p=offer.price,
            l=offer.lead_days,
            d=offer.deposit_pct,
            q=offer.moq,
            mold_exempt=False,
            tradeoffs=[],
            mandate=mandate,
            offer=offer,
        )
        candidates.append(base_proposal)

        # 條款置換策略 1: 定金換交期 (DEPOSIT_FOR_LEADTIME)
        if TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs:
            for d_candidate in [mandate.max_allowed_deposit_pct, (mandate.baseline_deposit_pct + mandate.max_allowed_deposit_pct) / 2]:
                for l_candidate in [mandate.min_desired_lead_days, mandate.baseline_lead_days - 5, mandate.baseline_lead_days - 10]:
                    if l_candidate < mandate.baseline_lead_days and l_candidate >= mandate.min_desired_lead_days:
                        prop = cls._create_proposal(
                            name="流動性定金置換產線交期壓縮 (Deposit for Lead Time)",
                            p=offer.price,
                            l=l_candidate,
                            d=d_candidate,
                            q=offer.moq,
                            mold_exempt=False,
                            tradeoffs=[TradeoffType.DEPOSIT_FOR_LEADTIME],
                            mandate=mandate,
                            offer=offer,
                        )
                        candidates.append(prop)

        # 條款置換策略 2: 定金換交期 + 模具費全免 (DEPOSIT_FOR_LEADTIME + PRICE_FOR_TOOLING)
        if (
            TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs
            and TradeoffType.PRICE_FOR_TOOLING in mandate.allowed_tradeoffs
            and mandate.mold_fee_exemption_demanded
        ):
            prop = cls._create_proposal(
                name="滿額定金現結換交期壓縮與模具全免 (Deposit for Lead Time & Tooling)",
                p=offer.price,
                l=mandate.min_desired_lead_days,
                d=mandate.max_allowed_deposit_pct,
                q=offer.moq,
                mold_exempt=True,
                tradeoffs=[TradeoffType.DEPOSIT_FOR_LEADTIME, TradeoffType.PRICE_FOR_TOOLING],
                mandate=mandate,
                offer=offer,
            )
            candidates.append(prop)

        # 條款置換策略 3: 起訂量換單價折讓 (VOLUME_FOR_PRICE)
        if TradeoffType.VOLUME_FOR_PRICE in mandate.allowed_tradeoffs and mandate.max_allowed_moq > offer.moq:
            for q_candidate in [mandate.max_allowed_moq, int((offer.moq + mandate.max_allowed_moq) / 2)]:
                discount_ratio = 0.08  # 工廠規模效應折讓 8%
                p_discounted = max(mandate.target_price, round(offer.price * (1 - discount_ratio), 2))
                prop = cls._create_proposal(
                    name="批量拉升規模效應置換單價折讓 (Volume for Price Concession)",
                    p=p_discounted,
                    l=mandate.baseline_lead_days,
                    d=mandate.baseline_deposit_pct,
                    q=q_candidate,
                    mold_exempt=False,
                    tradeoffs=[TradeoffType.VOLUME_FOR_PRICE],
                    mandate=mandate,
                    offer=offer,
                )
                candidates.append(prop)

        # 條款置換策略 4: 全維度複合黃金置換 (Triple Logrolling Synergy)
        if (
            TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs
            and TradeoffType.VOLUME_FOR_PRICE in mandate.allowed_tradeoffs
            and TradeoffType.PRICE_FOR_TOOLING in mandate.allowed_tradeoffs
        ):
            prop = cls._create_proposal(
                name="全維度超級置換 (Triple Logrolling: Deposit + Volume + Tooling)",
                p=round((offer.price + mandate.target_price) / 2, 2),
                l=mandate.min_desired_lead_days,
                d=mandate.max_allowed_deposit_pct,
                q=min(mandate.max_allowed_moq, int(offer.moq * 1.5)),
                mold_exempt=True,
                tradeoffs=[
                    TradeoffType.DEPOSIT_FOR_LEADTIME,
                    TradeoffType.VOLUME_FOR_PRICE,
                    TradeoffType.PRICE_FOR_TOOLING,
                ],
                mandate=mandate,
                offer=offer,
            )
            candidates.append(prop)

        # 篩選可行解並進行帕雷托非支配排序
        pareto_proposals = cls._filter_pareto_frontier(candidates)

        # 按照買方總剩餘價值排序，取得最優解
        best_proposal = max(pareto_proposals, key=lambda x: x.buyer_surplus_value)

        # AST 護欄驗證
        ASTMandateGuardrail.verify_proposal(
            proposal={
                "proposed_price": best_proposal.proposed_price,
                "proposed_lead_days": best_proposal.proposed_lead_days,
                "proposed_deposit_pct": best_proposal.proposed_deposit_pct,
                "proposed_moq": best_proposal.proposed_moq,
                "tradeoff_types_used": [t.value for t in best_proposal.tradeoff_types_used],
            },
            mandate=mandate,
            buyer_script=best_proposal.buyer_script_zh,
        )

        summary = (
            f"【Logrolling 多維置換結果】共評估 {len(candidates)} 組條款，篩選出 {len(pareto_proposals)} 組帕雷托解。"
            f"推薦方案：{best_proposal.strategy_name}。"
            f"條款明細：單價 ¥{best_proposal.proposed_price:.2f} (原 ¥{offer.price:.2f})，"
            f"交期 {best_proposal.proposed_lead_days} 天 (原 {offer.lead_days} 天)，"
            f"定金 {best_proposal.proposed_deposit_pct*100:.0f}% (原 {offer.deposit_pct*100:.0f}%)，"
            f"模具費免除: {'是' if best_proposal.mold_fee_exempted else '否'}。"
            f"買方等效單價收益 +¥{best_proposal.buyer_effective_unit_saving:.2f}/件，總剩餘增量 +¥{best_proposal.buyer_surplus_value:.0f}。"
        )

        return LogrollingResult(
            best_recommendation=best_proposal,
            all_pareto_proposals=pareto_proposals,
            original_supplier_offer=offer,
            total_candidates_evaluated=len(candidates),
            ast_guardrail_passed=True,
            summary_report=summary,
        )

    @classmethod
    def _create_proposal(
        cls,
        name: str,
        p: float,
        l: int,
        d: float,
        q: int,
        mold_exempt: bool,
        tradeoffs: List[TradeoffType],
        mandate: CorporateMandate,
        offer: SupplierCurrentOffer,
    ) -> LogrollingProposal:
        # 1. 買方剩餘計算 (貨幣化)
        price_surplus = (mandate.walk_away_ceiling - p) * q
        lead_surplus = mandate.lead_day_shadow_value * max(0, mandate.baseline_lead_days - l) * q
        mold_surplus = mandate.estimated_mold_fee if mold_exempt else 0.0

        extra_deposit_pct = max(0.0, d - mandate.baseline_deposit_pct)
        buyer_deposit_cost = p * q * extra_deposit_pct * mandate.buyer_capital_cost_rate * (l / 365.0)

        extra_moq = max(0, q - mandate.baseline_moq)
        buyer_moq_cost = p * extra_moq * mandate.excess_holding_cost_rate

        buyer_total_surplus = price_surplus + lead_surplus + mold_surplus - buyer_deposit_cost - buyer_moq_cost
        unit_effective_saving = buyer_total_surplus / q

        # 2. 供應商剩餘估計 (貨幣化)
        est_prod_cost = mandate.target_price * 0.82
        supplier_profit = (p - est_prod_cost) * q
        supplier_cash_gain = p * q * extra_deposit_pct * mandate.supplier_capital_relief_rate * (l / 365.0)
        supplier_rush_cost = 0.08 * (max(0, mandate.baseline_lead_days - l) ** 1.25) * q
        supplier_mold_cost = (mandate.estimated_mold_fee * 0.5) if mold_exempt else 0.0

        supplier_total_surplus = supplier_profit + supplier_cash_gain - supplier_rush_cost - supplier_mold_cost

        temp_prop = LogrollingProposal(
            strategy_name=name,
            proposed_price=round(p, 2),
            proposed_lead_days=l,
            proposed_deposit_pct=round(d, 3),
            proposed_moq=q,
            mold_fee_exempted=mold_exempt,
            tradeoff_types_used=tradeoffs,
            buyer_surplus_value=round(buyer_total_surplus, 2),
            buyer_effective_unit_saving=round(unit_effective_saving, 3),
            supplier_surplus_estimate=round(supplier_total_surplus, 2),
            explanation="",
        )

        zh_script, en_script = BuyerScriptGenerator.generate_scripts(temp_prop, mandate, offer)
        temp_prop.buyer_script_zh = zh_script
        temp_prop.buyer_script_en = en_script
        temp_prop.explanation = (
            f"定金 {d*100:.0f}% 換取交期壓縮至 {l} 天 (節省 {mandate.baseline_lead_days - l} 天)，"
            f"模具費免除收益 ¥{mold_surplus:.0f}，買方淨剩餘增額 ¥{buyer_total_surplus:.1f}。"
        )
        return temp_prop

    @classmethod
    def _filter_pareto_frontier(cls, proposals: List[LogrollingProposal]) -> List[LogrollingProposal]:
        """對候選方案進行非支配排序 (Non-Dominated Sort)，提取帕雷托前沿。"""
        pareto: List[LogrollingProposal] = []
        for cand in proposals:
            dominated = False
            for other in proposals:
                if (
                    other.buyer_surplus_value >= cand.buyer_surplus_value
                    and other.supplier_surplus_estimate >= cand.supplier_surplus_estimate
                    and (
                        other.buyer_surplus_value > cand.buyer_surplus_value
                        or other.supplier_surplus_estimate > cand.supplier_surplus_estimate
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                cand.is_pareto_dominant = True
                pareto.append(cand)
        return pareto or proposals
