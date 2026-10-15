"""TradeFlow v4 — 總擁有成本 (Total Cost of Ownership, TCO) 多目標非價格條款折算引擎。

數學模型：
將交期 (Lead Time)、起訂量 (MOQ)、付款條款 (Payment Terms)、包裝與稅票 (VAT & Packaging)
等非價格維度，透過影子成本 (Shadow Cost) 函數，動態歸一化為「等效採購單價 (Effective Landed Unit Cost)」：

  P_effective = P_raw + C_lead_time + C_moq_holding + C_payment_credit + C_tax_pack

這使後續的 MCTS 張量求解器能無縫優化「全維度商業總成本」，徹底解決「單純壓價被其他條款背刺」的產業難題。
"""
from __future__ import annotations
import math
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class TradeTermsSpec(BaseModel):
    """採購方基準合約標準條款。"""
    baseline_lead_days: int = Field(default=20, description="基準標準交期 (天)")
    baseline_moq: int = Field(default=1000, description="基準標準訂單量 (件)")
    baseline_payment_deposit_pct: float = Field(default=0.30, description="基準定金比例 (如 30%)")
    baseline_payment_credit_days: int = Field(default=0, description="基準驗收後帳期天數 (0=見提單走帳)")
    annual_cost_of_capital: float = Field(default=0.08, description="買方年化資本佔用成本 (8%)")
    daily_lead_delay_penalty_pct: float = Field(default=0.003, description="交期每延遲一天之違約險折算係數 (0.3%/天)")
    excess_moq_holding_cost_rate: float = Field(default=0.05, description="超額庫存持有與滯銷風險年化費率 (5%)")


class SupplierOfferedTerms(BaseModel):
    """供應商回覆之複合報價與附加條款。"""
    raw_unit_price: float = Field(..., description="供應商報出之原始票面單價 (¥)")
    offered_lead_days: Optional[int] = Field(default=None, description="承諾交期 (天)")
    offered_moq: Optional[int] = Field(default=None, description="要求之起訂量 (件)")
    deposit_pct: Optional[float] = Field(default=None, description="要求之預付定金比例 (如 1.0=全款現結)")
    unbundled_tax_rate: Optional[float] = Field(default=None, description="稅票加收點數 (如要求開專票加 10%)")
    unbundled_packaging_fee: Optional[float] = Field(default=None, description="包裝/外箱額外加收單價 (¥/件)")


class TCOBreakdown(BaseModel):
    """TCO 歸一化詳細成本拆解與等效價格。"""
    raw_unit_price: float
    lead_time_penalty: float
    moq_holding_penalty: float
    payment_cost_penalty: float
    tax_packaging_adjustment: float
    effective_unit_price: float
    is_favorable_vs_raw: bool
    explanation: str


class TCOEngine:
    """TCO 多目標條款折算引擎。"""

    @classmethod
    def calculate_effective_price(
        cls,
        terms_spec: TradeTermsSpec,
        offered: SupplierOfferedTerms,
    ) -> TCOBreakdown:
        p_raw = offered.raw_unit_price
        
        # 1. 交期延遲影子成本 (Lead Time Penalty)
        lead_penalty = 0.0
        if offered.offered_lead_days is not None:
            delay_days = max(0, offered.offered_lead_days - terms_spec.baseline_lead_days)
            lead_penalty = p_raw * delay_days * terms_spec.daily_lead_delay_penalty_pct

        # 2. 起訂量超額持有與倉儲影子成本 (MOQ Holding Cost)
        moq_penalty = 0.0
        if offered.offered_moq is not None and offered.offered_moq > terms_spec.baseline_moq:
            excess_ratio = (offered.offered_moq - terms_spec.baseline_moq) / terms_spec.baseline_moq
            # 超量部分按年化 5% 倉儲滯銷折算進單件單價
            moq_penalty = p_raw * excess_ratio * terms_spec.excess_moq_holding_cost_rate

        # 3. 付款條款與資金佔用成本 (Payment Terms Cashflow Cost)
        payment_penalty = 0.0
        if offered.deposit_pct is not None:
            # 相比基準定金多出的資金佔用
            deposit_delta = max(0.0, offered.deposit_pct - terms_spec.baseline_payment_deposit_pct)
            # 佔用時長預設以生產交期天數折算
            lead_days = offered.offered_lead_days or terms_spec.baseline_lead_days
            payment_penalty = p_raw * deposit_delta * (terms_spec.annual_cost_of_capital * (lead_days / 365.0))

        # 4. 稅票與包裝拆分額外成本 (Tax & Packaging Surcharge)
        tax_pack_adj = 0.0
        if offered.unbundled_tax_rate is not None:
            tax_pack_adj += p_raw * offered.unbundled_tax_rate
        if offered.unbundled_packaging_fee is not None:
            tax_pack_adj += offered.unbundled_packaging_fee

        # 5. 總擁有成本彙總
        p_effective = p_raw + lead_penalty + moq_penalty + payment_penalty + tax_pack_adj

        explanation = (
            f"原始票面價 ¥{p_raw:.2f} -> 等效 TCO 價 ¥{p_effective:.2f} "
            f"(交期補償: +¥{lead_penalty:.2f}, MOQ庫存補償: +¥{moq_penalty:.2f}, "
            f"定金佔用: +¥{payment_penalty:.2f}, 稅票包裝: +¥{tax_pack_adj:.2f})"
        )

        return TCOBreakdown(
            raw_unit_price=round(p_raw, 3),
            lead_time_penalty=round(lead_penalty, 3),
            moq_holding_penalty=round(moq_penalty, 3),
            payment_cost_penalty=round(payment_penalty, 3),
            tax_packaging_adjustment=round(tax_pack_adj, 3),
            effective_unit_price=round(p_effective, 3),
            is_favorable_vs_raw=(p_effective <= p_raw),
            explanation=explanation,
        )

    @classmethod
    def calculate_nonlinear_effective_tco(
        cls,
        raw_price: float,
        baseline_lead_days: int = 15,
        offered_lead_days: Optional[int] = None,
        baseline_moq: int = 500,
        offered_moq: Optional[int] = None,
        wacc: float = 0.08,
    ) -> Dict[str, Any]:
        """計算非價格條款之非線性影子成本 (Shadow Cost)。

        [誠實揭露] 下列為編譯時固定之啟發式 / 產業經驗常數，非資料擬合，
        未經實證校準：
        - 5：交期延遲的安全緩衝天數（超過才課罰）；
        - 0.015 / 指數 1.75 / 除數 10：缺貨風險的超線性階梯懲罰曲線（超過
          緩衝後以 ((delay-5)/10)^1.75 放大 1.5% 基價）；
        - 90 / 365：庫存週轉視為平均 90 天；
        - 0.08 / 1.2：滯銷降價風險（1 − exp(−1.2·excess_ratio) 之飽和收益，
          基於 8% base 風險率）；
        - 1.05：`is_unfavorable` 觸發閾值（等效價 > 原始價 1.05x 判定不利）。
        若需不同情境，應以 TradeTermsSpec / 函數參數覆寫這些值，本方法目前
        直接使用字面常數。
        """
        lead_penalty = 0.0
        lead_breakdown = {"capital_cost": 0.0, "stockout_risk_cost": 0.0}
        if offered_lead_days is not None and offered_lead_days > baseline_lead_days:
            delay = offered_lead_days - baseline_lead_days
            # 資金佔用折算
            cap_cost = raw_price * (wacc * (delay / 365.0))
            crisis_cost = 0.0
            # 5 天安全緩衝區之外，超線性懲罰 (階梯指數 1.75) — 常數見上方揭露
            if delay > 5:
                crisis_cost = raw_price * 0.015 * math.pow((delay - 5) / 10.0, 1.75)
            lead_penalty = cap_cost + crisis_cost
            lead_breakdown = {
                "capital_cost": round(cap_cost, 3),
                "stockout_risk_cost": round(crisis_cost, 3),
            }

        moq_penalty = 0.0
        moq_breakdown = {"holding_cost": 0.0, "markdown_risk_cost": 0.0}
        if offered_moq is not None and offered_moq > baseline_moq:
            excess_ratio = (offered_moq - baseline_moq) / float(baseline_moq)
            # 庫存持有資金佔用 (以平均 90 天滯銷週轉計) — 常數見上方揭露
            holding_cost = raw_price * excess_ratio * (wacc * (90.0 / 365.0))
            # 滯銷降價非線性飽和風險: 1 - exp(-1.2 * excess_ratio) — 常數見上方揭露
            markdown_risk = raw_price * excess_ratio * 0.08 * (1.0 - math.exp(-1.2 * excess_ratio))
            moq_penalty = holding_cost + markdown_risk
            moq_breakdown = {
                "holding_cost": round(holding_cost, 3),
                "markdown_risk_cost": round(markdown_risk, 3),
            }

        effective_price = raw_price + lead_penalty + moq_penalty

        return {
            "raw_price": round(raw_price, 3),
            "lead_time_shadow_cost": round(lead_penalty, 3),
            "lead_time_breakdown": lead_breakdown,
            "moq_inventory_shadow_cost": round(moq_penalty, 3),
            "moq_breakdown": moq_breakdown,
            "effective_tco_price": round(effective_price, 3),
            "effective_unit_tco": round(effective_price, 3),
            "effective_unit_price": round(effective_price, 3),
            "is_unfavorable": effective_price > raw_price * 1.05,
        }

