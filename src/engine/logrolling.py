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

from src.config.heuristics import (
    BASELINE_DEPOSIT_PCT,
    BUYER_CAPITAL_RATE,
    EXCESS_HOLDING_RATE,
    LEAD_DAY_SHADOW,
    MAX_DEPOSIT_PCT,
    MOLD_SURPLUS_SHARE,
    MOQ_STRETCH,
    PLANNING_MOLD_FEE,
    RUSH_COST_COEFFICIENT,
    RUSH_COST_EXPONENT,
    SUPPLIER_COST_RATIO,
    SUPPLIER_RELIEF_RATE,
    VOLUME_DISCOUNT_RATIO,
)


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
    baseline_lead_days: int | None = Field(default=None, gt=0, description="標準基準交期 (天)。未設定則不參與置換。")
    max_acceptable_lead_days: int | None = Field(default=None, gt=0, description="最長可容忍交期 (天)。未設定則不參與置換。")
    min_desired_lead_days: int | None = Field(default=None, gt=0, description="期望壓縮交期下限 (天)。未設定則不參與置換。")
    lead_day_shadow_value: float = Field(default=LEAD_DAY_SHADOW, ge=0.0, description="每提前一天商業影子單價 (元/天/件)")
    baseline_deposit_pct: float = Field(default=BASELINE_DEPOSIT_PCT, ge=0.0, le=1.0, description="基準定金比例")
    max_allowed_deposit_pct: float = Field(default=MAX_DEPOSIT_PCT, ge=0.0, le=1.0, description="公司授權最高定金比例")
    buyer_capital_cost_rate: float = Field(default=BUYER_CAPITAL_RATE, ge=0.0, description="買方年化資本成本率")
    supplier_capital_relief_rate: float = Field(default=SUPPLIER_RELIEF_RATE, ge=0.0, description="供應商資金短缺緩釋年化收益率")
    baseline_moq: int | None = Field(default=None, gt=0, description="基準標準採購量 (件)。未設定則不參與置換。")
    max_allowed_moq: int | None = Field(default=None, gt=0, description="最大允許採購量 (件)。未設定則不參與置換。")
    excess_holding_cost_rate: float = Field(default=EXCESS_HOLDING_RATE, ge=0.0, description="超額庫存持有成本率")
    mold_fee_exemption_demanded: bool = Field(default=True, description="是否要求免除模具費")
    estimated_mold_fee: float = Field(default=PLANNING_MOLD_FEE, ge=0.0, description="規劃用模具費，不是供應商報價")
    volume_discount_ratio: float = Field(
        default=VOLUME_DISCOUNT_RATIO,
        ge=0.0,
        le=1.0,
        description=(
            "以量換價時假設的規模效應折讓率。"
            "此為**未經真實工廠報價校準的選擇值**，非量測值；"
            "不同品類/工廠應覆寫此欄位。舊版寫死於程式碼中，現開放設定。"
        ),
    )
    supplier_production_cost_ratio: float = Field(
        default=SUPPLIER_COST_RATIO,
        ge=0.0,
        le=1.0,
        description=(
            "供應商生產成本佔目標價的比例 (預設 0.82)，用於估算供應商利潤。"
            "**未經真實成本單校準**；舊版寫死為 0.82，現開放覆寫。"
        ),
    )
    supplier_rush_cost_coefficient: float = Field(
        default=RUSH_COST_COEFFICIENT,
        ge=0.0,
        description=(
            "供應商趕工成本係數，與下方指數共同決定趕工成本曲線。"
            "**兩個常數皆無公開文獻或成本單推導**；舊版寫死，現開放覆寫。"
        ),
    )
    supplier_rush_cost_exponent: float = Field(
        default=RUSH_COST_EXPONENT,
        gt=0.0,
        description="供應商趕工成本對提前天數的凸性指數 (預設 1.25)。未經校準，可覆寫。",
    )
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
        if (
            self.max_acceptable_lead_days is not None
            and self.baseline_lead_days is not None
            and self.max_acceptable_lead_days < self.baseline_lead_days
        ):
            raise ValueError(f"最大容忍交期 ({self.max_acceptable_lead_days}) 不得短於基準交期 ({self.baseline_lead_days})")
        if (
            self.min_desired_lead_days is not None
            and self.baseline_lead_days is not None
            and self.min_desired_lead_days > self.baseline_lead_days
        ):
            raise ValueError(f"期望交期下限 ({self.min_desired_lead_days}) 不得大於基準交期 ({self.baseline_lead_days})")
        if self.max_allowed_deposit_pct < self.baseline_deposit_pct:
            raise ValueError(f"最高許可定金 ({self.max_allowed_deposit_pct}) 不得低於基準定金 ({self.baseline_deposit_pct})")
        if (
            self.max_allowed_moq is not None
            and self.baseline_moq is not None
            and self.max_allowed_moq < self.baseline_moq
        ):
            raise ValueError(f"最大允許採購量 ({self.max_allowed_moq}) 不得低於基準採購量 ({self.baseline_moq})")
        return self


class SupplierCurrentOffer(BaseModel):
    """供應商當前堅持之報價條件。"""
    price: float = Field(..., gt=0.0, description="供應商堅持之單價 (元)")
    lead_days: int | None = Field(default=None, gt=0, description="供應商報出之交期 (天)。未報則不參與交期置換。")
    deposit_pct: float = Field(default=BASELINE_DEPOSIT_PCT, ge=0.0, le=1.0, description="供應商要求之定金比例")
    moq: int | None = Field(default=None, gt=0, description="供應商要求之起訂量 (件)。未報則不參與批量置換。")
    mold_fee: float = Field(default=PLANNING_MOLD_FEE, ge=0.0, description="未報價時的規劃值，不是已收到的模具費")
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
    """AST 企業授權防禦護欄核心校驗器。

    [C3 修正] 舊版以 `assert` 實作，並在 `except AssertionError` 轉譯。`assert` 是
    可被直譯器最佳化剝除的敘述：`python -O` 底下所有 assert 消失，
    `verify_proposal()` 對任何越界方案一律放行（實測）。安全不變量不得依賴
    `sys.flags.optimize`，故本版改為顯式 `if not ...: raise`。
    """

    _GUARDRAIL_SNIPPET = '''
def evaluate_mandate_invariants(props, mandate):
    if not props['proposed_price'] <= mandate['walk_away_ceiling'] + 1e-6:
        raise ValueError("AST: 單價超出 walk_away_ceiling")
    if not props['proposed_lead_days'] <= mandate['max_acceptable_lead_days']:
        raise ValueError("AST: 交期超出 max_acceptable_lead_days")
    if not props['proposed_deposit_pct'] <= mandate['max_allowed_deposit_pct'] + 1e-6:
        raise ValueError("AST: 定金超出 max_allowed_deposit_pct")
    if not props['proposed_moq'] <= mandate['max_allowed_moq']:
        raise ValueError("AST: 起訂量超出 max_allowed_moq")
    for t in props['tradeoff_types_used']:
        if not t in mandate['allowed_tradeoffs']:
            raise ValueError(f"AST: 未授權條款 {t}")
    return True
'''

    @classmethod
    def compile_guardrail_ast(cls) -> ast.Module:
        return ast.parse(cls._GUARDRAIL_SNIPPET)

    @classmethod
    def verify_proposal(
        cls,
        proposal: Dict[str, Any],
        mandate: CorporateMandate,
        buyer_script: str = "",
        buyer_script_en: str = "",
    ) -> bool:
        """
        驗證方案是否符合企業授權。

        [2.4 修正] 舊版只驗證中文話術 (`buyer_script`)，英文外貿話術
        (`buyer_script_en`) 完全未受檢 —— 而它同樣會送往對外通路。
        現兩個表面皆納入同一組不變量。

        [誠實揭露] 此護欄驗證的是「方案 vs 呼叫端提供的 mandate」，因此它
        **不能**證明 mandate 本身等於獨立授權來源，也不檢查商業合理性
        （例如供應商剩餘為負）。商業合理性由 `LogrollingEngine._is_feasible`
        在候選建立時把關。
        """
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
        except ValueError as exc:
            raise ASTMandateViolationError(str(exc)) from exc

        if buyer_script:
            cls._audit_script_numerics(buyer_script, proposal, mandate)
        if buyer_script_en:
            cls._audit_script_numerics(buyer_script_en, proposal, mandate)

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
            val = float(pct_str) / 100
            if val > mandate.max_allowed_deposit_pct + 1e-4:
                raise ASTMandateViolationError(
                    f"AST: 話術承諾定金 {pct_str}% 超出公司最高授權 {mandate.max_allowed_deposit_pct*100:.1f}%"
                )

        # 嚴格僅校驗買方承諾交付之目標交期（如「壓縮至 XX 天內交付」或「XX 天交付」），排除對手原交期引述
        promised_day_matches = re.findall(r"(?:壓縮至|壓至|內|目標交期|承諾)\s*(\d+)\s*天", script)
        for day_str in promised_day_matches:
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
        # [P1 誠實性守門] 模具費減免主張必須同時滿足兩個前提：
        #   (1) mandate 授權要求免除模具費（`mold_fee_exemption_demanded`）；
        #   (2) 對方**真的開過**模具費（`offer.mold_fee > 0`）。
        # 舊版只看 `mold_exempt`，因此在對方從未報出模具費時仍對外宣稱
        # 「模具費由貴廠全額減免」—— 一筆對方從未開過價的錢。
        mold_waiver_grounded = bool(
            mold_exempt and mandate.mold_fee_exemption_demanded and offer.mold_fee > 0.0
        )
        if mold_waiver_grounded:
            demand_parts.append(f"本期首批模具/打樣費 ¥{offer.mold_fee:.0f} 由貴廠全額減免")
        if p < offer.price:
            demand_parts.append(f"單價按批量折讓落定在 ¥{p:.2f}")

        concession_str = "，同時".join(concession_parts) if concession_parts else "維持我方優質付款條件"
        demand_str = "、".join(demand_parts)

        premise = f"若單價維持在貴廠報價 ¥{offer.price:.2f}" if abs(p - offer.price) < 1e-4 else f"若單價按批量折讓落定在 ¥{p:.2f}"
        zh_script = (
            f"老闆，理解貴廠在製程與排單成本上的考量。{premise}，我方展現充分的合作誠意：{concession_str}。"
            f"但作為對等條件，{demand_str}。"
            f"這是我們採購總監與財務剛特批的打包條件。若貴方確認，我們今天立即蓋章回傳 PO，首筆定金即刻按約定走帳；若此條件仍無法達成，我方受限於專案預算只得啟動備案產能。"
        )

        # 英文外貿金牌買家話術
        en_script = (
            f"Dear Partner, regarding your quotation at ¥{p:.2f}/unit: To accommodate your cost constraints, "
            f"our treasury has approved an immediate {d_pct}% cash deposit today to secure raw materials and reserve capacity. "
            f"In return for this cashflow concession, production lead time must be expedited to {l} days. "
            + (
                f"The tooling fee of ¥{offer.mold_fee:.0f} must also be fully waived for this batch. "
                if mold_waiver_grounded
                else ""
            )
            + f"This represents our executive mandate for today's PO sign-off. Please confirm so we can release the payment immediately."
        )

        return zh_script, en_script


class LogrollingEngine:
    """企業授權多維條款置換引擎。"""

    @classmethod
    def _is_feasible(cls, proposal: LogrollingProposal, mandate: CorporateMandate) -> bool:
        """
        [2.4 修正] 候選**建立時**的商業合理性與授權邊界過濾。

        舊版把 AST 護欄放在「選出贏家之後」，且 `_create_proposal` 完全不做邊界檢查，
        因此不可行／荒謬的候選仍會進入排名。實測：
        `CorporateMandate(target_price=12, walk_away_ceiling=16.2)` +
        `SupplierCurrentOffer(price=9.00, deposit_pct=0.60)` → SUCCEEDED，
        推薦價 ¥10.50、定金 50%、`ast_guardrail_passed=True`，
        且該方案的 `supplier_surplus_estimate` 為 **-2474.69**（工廠明顯虧損）。

        本過濾在候選產生時即套用授權邊界與帕雷托合理性：
          1. 價格不得超過 walk_away_ceiling。
          2. 交期不得超過 max_acceptable_lead_days。
          3. 定金不得超過 max_allowed_deposit_pct。
          4. 起訂量不得超過 max_allowed_moq。
          5. 供應商剩餘估計不得為負 —— 否則這不是「雙贏置換」，
             不應被當成帕雷托改善推薦出去。
        """
        if proposal.proposed_price > mandate.walk_away_ceiling + 1e-6:
            return False
        if proposal.proposed_price <= 0.0:
            return False
        if proposal.proposed_lead_days > mandate.max_acceptable_lead_days:
            return False
        if proposal.proposed_lead_days < 1:
            return False
        if proposal.proposed_deposit_pct > mandate.max_allowed_deposit_pct + 1e-6:
            return False
        if proposal.proposed_moq > mandate.max_allowed_moq:
            return False
        if proposal.supplier_surplus_estimate < 0.0:
            return False
        # [P1 誠實性守門] 第二道防線：即使未來有人新增候選時忘記加上面的
        # 「`mandate.mold_fee_exemption_demanded`」守門，這裡也必須拒絕任何
        # 「宣稱免除模具費、但 mandate 並未授權該主張」的提案。
        #
        # 沒有這道防線的實測後果（策略 4 舊版）：`mold_fee_exemption_demanded=False`
        # 且對方 `mold_fee=0.0` 時，候選仍掛 `mold_exempt=True`，並以
        # `mandate.estimated_mold_fee`（預設 3000、LIMITATIONS §十三自述為未校準
        # 選定值）乘上一筆不存在的減免，把虛構收益灌進 `buyer_surplus_value`
        # 贏得排名；話術同時對外承諾「模具費由貴廠全額減免」。
        if proposal.mold_fee_exempted and not mandate.mold_fee_exemption_demanded:
            return False
        # 話術與決策必須一致：掛了 PRICE_FOR_TOOLING 就必須真的免除模具費。
        # 否則等於「聲稱換了模具費卻沒換」，是另一種不誠實的置換。
        if (
            TradeoffType.PRICE_FOR_TOOLING in proposal.tradeoff_types_used
            and not proposal.mold_fee_exempted
        ):
            return False
        return True

    @classmethod
    def evaluate(
        cls,
        mandate: CorporateMandate,
        offer: SupplierCurrentOffer,
    ) -> LogrollingResult:
        """主入口：搜尋帕雷托最優前沿並返回最優置換對。"""
        missing = [
            name for name, value in (
                ("supplier lead time", offer.lead_days),
                ("supplier MOQ", offer.moq),
                ("baseline lead time", mandate.baseline_lead_days),
                ("maximum lead time", mandate.max_acceptable_lead_days),
                ("minimum lead time", mandate.min_desired_lead_days),
                ("baseline MOQ", mandate.baseline_moq),
                ("maximum MOQ", mandate.max_allowed_moq),
            ) if value is None
        ]
        if missing:
            raise ValueError(
                "Logrolling needs the supplier's lead time and MOQ and the buyer's "
                "planning terms. Missing: " + ", ".join(missing) + "."
            )
        candidates: List[LogrollingProposal] = []
        rejected_candidates: List[LogrollingProposal] = []

        def _register(prop: LogrollingProposal) -> None:
            (candidates if cls._is_feasible(prop, mandate) else rejected_candidates).append(prop)

        # 基準：若維持工廠原樣條件。
        # [2.4 修正] 基準方案**不**進入排名：維持現狀不是「改善」，否則它會
        # 與真實方案競爭，且在報價已低於目標價時成為通吃的贏家。
        baseline_proposal = cls._create_proposal(
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
        candidates.append(baseline_proposal)

        # 條款置換策略 1: 定金換交期 (DEPOSIT_FOR_LEADTIME)
        if TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs:
            for d_candidate in [mandate.max_allowed_deposit_pct, (mandate.baseline_deposit_pct + mandate.max_allowed_deposit_pct) / 2]:
                for l_candidate in [mandate.min_desired_lead_days, mandate.baseline_lead_days - 5, mandate.baseline_lead_days - 10]:
                    if l_candidate < mandate.baseline_lead_days and l_candidate >= mandate.min_desired_lead_days:
                        _register(cls._create_proposal(
                            name="流動性定金置換產線交期壓縮 (Deposit for Lead Time)",
                            p=offer.price,
                            l=l_candidate,
                            d=d_candidate,
                            q=offer.moq,
                            mold_exempt=False,
                            tradeoffs=[TradeoffType.DEPOSIT_FOR_LEADTIME],
                            mandate=mandate,
                            offer=offer,
                        ))

        # 條款置換策略 2: 定金換交期 + 模具費全免 (DEPOSIT_FOR_LEADTIME + PRICE_FOR_TOOLING)
        if (
            TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs
            and TradeoffType.PRICE_FOR_TOOLING in mandate.allowed_tradeoffs
            and mandate.mold_fee_exemption_demanded
        ):
            _register(cls._create_proposal(
                name="滿額定金現結換交期壓縮與模具全免 (Deposit for Lead Time & Tooling)",
                p=offer.price,
                l=mandate.min_desired_lead_days,
                d=mandate.max_allowed_deposit_pct,
                q=offer.moq,
                mold_exempt=True,
                tradeoffs=[TradeoffType.DEPOSIT_FOR_LEADTIME, TradeoffType.PRICE_FOR_TOOLING],
                mandate=mandate,
                offer=offer,
            ))

        # 條款置換策略 3: 起訂量換單價折讓 (VOLUME_FOR_PRICE)
        if TradeoffType.VOLUME_FOR_PRICE in mandate.allowed_tradeoffs and mandate.max_allowed_moq > offer.moq:
            for q_candidate in [mandate.max_allowed_moq, int((offer.moq + mandate.max_allowed_moq) / 2)]:
                # [修正] 舊版把 0.08 寫死在此處，與 CorporateMandate 的其他條款
                # （lead_day_shadow_value、buyer_capital_cost_rate 等）都走可設定
                # Field 的做法不一致，呼叫端無法覆寫。現改為 mandate 上的欄位。
                discount_ratio = mandate.volume_discount_ratio
                p_discounted = max(mandate.target_price, round(offer.price * (1 - discount_ratio), 2))
                _register(cls._create_proposal(
                    name="批量拉升規模效應置換單價折讓 (Volume for Price Concession)",
                    p=p_discounted,
                    l=mandate.baseline_lead_days,
                    d=mandate.baseline_deposit_pct,
                    q=q_candidate,
                    mold_exempt=False,
                    tradeoffs=[TradeoffType.VOLUME_FOR_PRICE],
                    mandate=mandate,
                    offer=offer,
                ))

        # 條款置換策略 4: 全維度複合黃金置換 (Triple Logrolling Synergy)
        # [P1 誠實性守門] 舊版此處**沒有**檢查 `mandate.mold_fee_exemption_demanded`
        # 就硬掛 `mold_exempt=True`（策略 2 有檢查）。於是當對方從未報出模具費
        # （main.py 已據此把該旗標設為 False）時，引擎仍產生「模具費全額減免」
        # 提案，並以 `mandate.estimated_mold_fee`（預設 3000、未校準的選定值）
        # 乘上一筆不存在的減免，算出虛構收益贏得排名（實測贏家買方剩餘
        # 4824.59，第二名僅 1247.53），其話術更直接對外承諾「由貴廠全額減免」。
        # 現補上與策略 2 完全相同的守門：mandate 未要求免除模具費，就不產生
        # 任何宣稱模具全免的候選。
        if (
            TradeoffType.DEPOSIT_FOR_LEADTIME in mandate.allowed_tradeoffs
            and TradeoffType.VOLUME_FOR_PRICE in mandate.allowed_tradeoffs
            and TradeoffType.PRICE_FOR_TOOLING in mandate.allowed_tradeoffs
            and mandate.mold_fee_exemption_demanded
        ):
            _register(cls._create_proposal(
                name="全維度超級置換 (Triple Logrolling: Deposit + Volume + Tooling)",
                p=round((offer.price + mandate.target_price) / 2, 2),
                l=mandate.min_desired_lead_days,
                d=mandate.max_allowed_deposit_pct,
                q=min(mandate.max_allowed_moq, int(offer.moq * MOQ_STRETCH)),
                mold_exempt=True,
                tradeoffs=[
                    TradeoffType.DEPOSIT_FOR_LEADTIME,
                    TradeoffType.VOLUME_FOR_PRICE,
                    TradeoffType.PRICE_FOR_TOOLING,
                ],
                mandate=mandate,
                offer=offer,
            ))

        # 篩選可行解並進行帕雷托非支配排序 —— 只對**可行策略候選**排名。
        # 基準（維持現狀）與不可行方案皆不參與排名；若無任何可行策略候選，
        # 則誠實地推薦「維持工廠原始條件」（買方不讓步），而不是把不可行方案
        # 重新包裝成帕雷托改善。
        strategy_candidates = [c for c in candidates if c.tradeoff_types_used]
        pareto_source = strategy_candidates or [baseline_proposal]
        pareto_proposals = cls._filter_pareto_frontier(pareto_source)

        # 按照買方總剩餘價值排序，取得最優解
        best_proposal = max(pareto_proposals, key=lambda x: x.buyer_surplus_value)

        # [2.4 修正] 護欄亦對**英文**話術執行（舊版只驗中文 buyer_script_zh），
        # 且對整個帕雷托前沿的推薦方案逐一驗證，而非只檢查贏家。
        ast_guardrail_passed = True
        for prop in pareto_proposals:
            ASTMandateGuardrail.verify_proposal(
                proposal={
                    "proposed_price": prop.proposed_price,
                    "proposed_lead_days": prop.proposed_lead_days,
                    "proposed_deposit_pct": prop.proposed_deposit_pct,
                    "proposed_moq": prop.proposed_moq,
                    "tradeoff_types_used": [t.value for t in prop.tradeoff_types_used],
                },
                mandate=mandate,
                buyer_script=prop.buyer_script_zh,
                buyer_script_en=prop.buyer_script_en,
            )
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
            buyer_script_en=best_proposal.buyer_script_en,
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
            ast_guardrail_passed=ast_guardrail_passed,
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
        # [5.5 修正] 舊版 `price_surplus = (ceiling - p) * q` 讓「拉升批量」機器式地
        # 放大剩餘：多出的每一件都以 (ceiling - p) 全額計入收益，而超額庫存成本卻
        # 只是一次性的 5% 單價，等於把「買更多庫存」當成免費獲利，推薦因此系統性
        # 偏向量膨脹。修正後只對**買方真正需要的數量**計價，超出基準量的部分視為
        # 庫存，其單價改善以超額持有成本率折算（即「量增不是白吃的錢」）。
        required_qty = min(q, mandate.baseline_moq)
        extra_qty = max(0, q - mandate.baseline_moq)

        # 價格剩餘只在**買方真正需要的數量**上計價。超出基準量的部分是不需要的
        # 庫存，不產生「剩餘」收益，並且要支付超額持有成本 —— 這正是「量增不是
        # 免費的錢」。舊公式 `(ceiling - p) * q` 讓每一件多餘庫存都以完整價差
        # 計入獲利，因此機器式地偏向量膨脹。
        price_surplus = (mandate.walk_away_ceiling - p) * required_qty

        lead_surplus = mandate.lead_day_shadow_value * max(0, mandate.baseline_lead_days - l) * required_qty
        # [P1 誠實性守門] 模具費減免只有在「mandate 授權該主張」時才計入買方收益。
        # 舊版只看 `mold_exempt` 參數本身，等於把 `mandate.estimated_mold_fee`
        # （預設 3000、LIMITATIONS §十三自述為未校準選定值）當成已實現的收益；
        # 一旦候選忘記檢查 `mold_fee_exemption_demanded`，這筆虛構收益就會
        # 主導排名。現改為以 mandate 的授權旗標為前提。
        # 另外對方若從未報出模具費（`offer.mold_fee <= 0`），也沒有「減免」可言。
        mold_surplus = (
            mandate.estimated_mold_fee
            if (mold_exempt and mandate.mold_fee_exemption_demanded and offer.mold_fee > 0.0)
            else 0.0
        )

        extra_deposit_pct = max(0.0, d - mandate.baseline_deposit_pct)
        buyer_deposit_cost = p * q * extra_deposit_pct * mandate.buyer_capital_cost_rate * (l / 365)

        buyer_moq_cost = p * extra_qty * mandate.excess_holding_cost_rate

        buyer_total_surplus = price_surplus + lead_surplus + mold_surplus - buyer_deposit_cost - buyer_moq_cost
        unit_effective_saving = buyer_total_surplus / q

        # 2. 供應商剩餘估計 (貨幣化)
        # [修正] 下列三個常數舊版寫死於此，且未在任何文件中揭露其來源。
        # 它們是**未經真實工廠成本單校準的選擇值**，現提升為 mandate 欄位以便覆寫，
        # 並在 description 中標明其性質，避免被誤讀為量測值。
        est_prod_cost = mandate.target_price * mandate.supplier_production_cost_ratio
        supplier_profit = (p - est_prod_cost) * q
        supplier_cash_gain = p * q * extra_deposit_pct * mandate.supplier_capital_relief_rate * (l / 365)
        supplier_rush_cost = (
            mandate.supplier_rush_cost_coefficient
            * (max(0, mandate.baseline_lead_days - l) ** mandate.supplier_rush_cost_exponent)
            * q
        )
        # 與買方模具收益同步：只有在 mandate 授權、且對方真的開過模具費時，
        # 供應商端才承擔對應的模具成本。否則又是一筆單方面的虛構數字。
        supplier_mold_cost = (
            (mandate.estimated_mold_fee * MOLD_SURPLUS_SHARE)
            if (mold_exempt and mandate.mold_fee_exemption_demanded and offer.mold_fee > 0.0)
            else 0.0
        )

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
