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

from src.config.heuristics import (
    BASELINE_DEPOSIT_PCT,
    BUYER_CAPITAL_RATE,
    DAILY_LEAD_PENALTY,
    EXCESS_HOLDING_RATE,
    INVENTORY_HOLDING_DAYS,
    TCO_CRISIS_DELAY_SCALE,
    TCO_CRISIS_DELAY_START,
    TCO_CRISIS_EXPONENT,
    TCO_CRISIS_RATE,
    TCO_MARKDOWN_EXP,
    TCO_MARKDOWN_RATE,
    TCO_UNFAVORABLE_PREMIUM,
)


class TradeTermsSpec(BaseModel):
    """採購方基準合約標準條款。"""
    baseline_lead_days: Optional[int] = Field(default=None, description="基準標準交期 (天)。未設定則不計交期影子成本。")
    baseline_moq: Optional[int] = Field(default=None, description="基準標準訂單量 (件)。未設定則不計起訂量影子成本。")
    baseline_payment_deposit_pct: float = Field(default=BASELINE_DEPOSIT_PCT, description="基準定金比例")
    baseline_payment_credit_days: int = Field(default=0, description="基準驗收後帳期天數 (0=見提單走帳)")
    annual_cost_of_capital: float = Field(default=BUYER_CAPITAL_RATE, description="買方年化資本佔用成本")
    daily_lead_delay_penalty_pct: float = Field(default=DAILY_LEAD_PENALTY, description="交期每延遲一天之違約險折算係數")
    excess_moq_holding_cost_rate: float = Field(
        default=EXCESS_HOLDING_RATE,
        description=(
            "超額庫存持有與滯銷風險**年化**費率 (5%)。"
            "注意此為年化率，必須乘上持有天數/365 才是單件影子成本；"
            "早期版本曾把年化率當成一次性費用直接乘，與本模組非線性路徑"
            "（以 wacc × 90/365 折算）相差約 2.5 倍。兩條路徑現共用 "
            "INVENTORY_HOLDING_DAYS 與本費率。"
        ),
    )


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
        # [CORRECTNESS 2026-10-23] 這裡補上與非線性路徑相同的 None 守衛。
        # TradeTermsSpec 的兩個 baseline 欄位預設為 None（「未設定則不計該項
        # 影子成本」），但線性路徑直接拿它做減法與除法：只要呼叫端只提供
        # offered_lead_days 或 offered_moq 就會拋 TypeError，baseline_moq=0
        # 則拋 ZeroDivisionError。docstring 承諾的「不計」在此才成立。
        lead_penalty = 0.0
        if offered.offered_lead_days is not None and terms_spec.baseline_lead_days is not None:
            delay_days = max(0, offered.offered_lead_days - terms_spec.baseline_lead_days)
            lead_penalty = p_raw * delay_days * terms_spec.daily_lead_delay_penalty_pct

        # 2. 起訂量超額持有與倉儲影子成本 (MOQ Holding Cost)
        # [修正 — 見 DEEP_DEFECT_AUDIT.md §4.7]
        # 舊版此處為 `p_raw * excess_ratio * excess_moq_holding_cost_rate`，把
        #「年化 5%」當成一次性費用直接乘，得到 0.05 的量級；而同模組的
        # calculate_nonlinear_effective_tco 用 `wacc * 90/365` ≈ 0.0197。
        # 同一概念兩套數字（相差 ~2.5 倍），且 main.py 與 logrolling.py 各用一套。
        # 現統一：年化費率 × 持有天數/365，與非線性路徑同基準。
        moq_penalty = 0.0
        baseline_moq = terms_spec.baseline_moq
        if offered.offered_moq is not None and baseline_moq is not None and baseline_moq > 0 and offered.offered_moq > baseline_moq:
            excess_ratio = (offered.offered_moq - baseline_moq) / baseline_moq
            holding_years = INVENTORY_HOLDING_DAYS / 365
            moq_penalty = p_raw * excess_ratio * terms_spec.excess_moq_holding_cost_rate * holding_years

        # 3. 付款條款與資金佔用成本 (Payment Terms Cashflow Cost)
        payment_penalty = 0.0
        if offered.deposit_pct is not None:
            # 相比基準定金多出的資金佔用
            deposit_delta = max(0.0, offered.deposit_pct - terms_spec.baseline_payment_deposit_pct)
            # 佔用時長預設以生產交期天數折算
            lead_days = offered.offered_lead_days or terms_spec.baseline_lead_days or 0
            payment_penalty = p_raw * deposit_delta * (terms_spec.annual_cost_of_capital * (lead_days / 365))

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
        baseline_lead_days: Optional[int] = None,
        offered_lead_days: Optional[int] = None,
        baseline_moq: Optional[int] = None,
        offered_moq: Optional[int] = None,
        wacc: float = BUYER_CAPITAL_RATE,
    ) -> Dict[str, Any]:
        """計算非價格條款之非線性影子成本 (Shadow Cost)。

        [誠實揭露] 下列為編譯時固定之啟發式 / 產業經驗常數，非資料擬合，
        未經實證校準：
        - 5：交期延遲的安全緩衝天數（超過才課罰）；
        - 0.015 / 指數 1.75 / 除數 10：缺貨風險的超線性階梯懲罰曲線（超過
          緩衝後以 ((delay-5)/10)^1.75 放大 1.5% 基價）；
        - INVENTORY_HOLDING_DAYS (90) / 365：庫存週轉視為平均 90 天；
        - 0.08 / 1.2：滯銷降價風險（1 − exp(−1.2·excess_ratio) 之飽和收益，
          基於 8% base 風險率）；
        - 1.05：`is_unfavorable` 觸發閾值（等效價 > 原始價 1.05x 判定不利）。
        若需不同情境，應以 TradeTermsSpec / 函數參數覆寫這些值，本方法目前
        直接使用字面常數。

        [與 calculate_effective_price 的關係 — 見 §4.7]
        本方法與 calculate_effective_price 共用 INVENTORY_HOLDING_DAYS 作為持有
        期間，但**刻意**分成兩項，非同一數字：
          * holding_cost  = 庫存資金佔用，以 wacc 為年化率（資本成本觀點）；
          * markdown_risk = 滯銷降價的非線性飽和風險（1 − e^(−1.2·excess_ratio)），
            此項是線性路徑（單一「持有與滯銷」年化費率）所沒有的。
        亦即本路徑 = 資本成本 + 非線性滯銷風險；線性路徑 = 單一綜合年化費率。
        兩者的共同點是「年化率 × 持有天數/365」的量綱，且持有天數同源；
        差異處已明文揭露，並由 tests/test_tco_consistency.py 固定。
        """
        lead_penalty = 0.0
        lead_breakdown = {"capital_cost": 0.0, "stockout_risk_cost": 0.0}
        if (
            offered_lead_days is not None
            and baseline_lead_days is not None
            and offered_lead_days > baseline_lead_days
        ):
            delay = offered_lead_days - baseline_lead_days
            # 資金佔用折算
            cap_cost = raw_price * (wacc * (delay / 365))
            crisis_cost = 0.0
            # 5 天安全緩衝區之外，超線性懲罰 (階梯指數 1.75) — 常數見上方揭露
            if delay > TCO_CRISIS_DELAY_START:
                crisis_cost = raw_price * TCO_CRISIS_RATE * math.pow((delay - TCO_CRISIS_DELAY_START) / TCO_CRISIS_DELAY_SCALE, TCO_CRISIS_EXPONENT)
            lead_penalty = cap_cost + crisis_cost
            lead_breakdown = {
                "capital_cost": round(cap_cost, 3),
                "stockout_risk_cost": round(crisis_cost, 3),
            }

        moq_penalty = 0.0
        moq_breakdown = {"holding_cost": 0.0, "markdown_risk_cost": 0.0}
        if offered_moq is not None and baseline_moq is not None and offered_moq > baseline_moq:
            excess_ratio = (offered_moq - baseline_moq) / float(baseline_moq)
            # 庫存持有資金佔用 (以平均 90 天滯銷週轉計) — 天數常數與線性路徑共用
            holding_cost = raw_price * excess_ratio * (wacc * (INVENTORY_HOLDING_DAYS / 365))
            # 滯銷降價非線性飽和風險: 1 - exp(-1.2 * excess_ratio) — 常數見上方揭露
            markdown_risk = raw_price * excess_ratio * TCO_MARKDOWN_RATE * (1.0 - math.exp(-TCO_MARKDOWN_EXP * excess_ratio))
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
            "is_unfavorable": effective_price > raw_price * TCO_UNFAVORABLE_PREMIUM,
        }

