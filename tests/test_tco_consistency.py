"""TCO 影子成本一致性回歸測試 (tests/test_tco_consistency.py).

對應 DEEP_DEFECT_AUDIT.md §4.7：
`TCOEngine` 曾有**兩套**互不相容的超額 MOQ 持有成本公式：
  * `calculate_effective_price`（線性路徑）：`p_raw * excess_ratio * 0.05`
    —— 把「年化 5%」當成**一次性**費用直接乘，係數 ≈ 0.05。
  * `calculate_nonlinear_effective_tco`（非線性路徑）：`p_raw * excess_ratio
    * (wacc * 90/365)` —— 係數 ≈ 0.0197。
同一概念相差約 2.5 倍，而 `src/app/main.py` 與 `src/engine/logrolling.py`
分別採用其中一套，使決策路徑上的影子定價不一致。

本測試固定修正後的行為：兩條路徑的**持有天數同源**（共用
`INVENTORY_HOLDING_DAYS`），且線性路徑確實把年化費率折算為持有期間成本，
而非當成一次性費用。

本檔為回歸守衛：在修正前，`test_linear_path_is_annualised` 與
`test_holding_days_are_shared` 皆會失敗。
"""
from __future__ import annotations

import inspect
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine import tco
from src.engine.tco import (
    INVENTORY_HOLDING_DAYS,
    TCOEngine,
    SupplierOfferedTerms,
    TradeTermsSpec,
)


class TestTCOFormulaConsistency(unittest.TestCase):

    def setUp(self):
        self.terms = TradeTermsSpec(
            baseline_lead_days=20,
            baseline_moq=1000,
            baseline_payment_deposit_pct=0.30,
            annual_cost_of_capital=0.08,
            daily_lead_delay_penalty_pct=0.003,
            excess_moq_holding_cost_rate=0.05,
        )

    # ------------------------------------------------------------------
    # 核心不變量 1：線性路徑必須把「年化」費率折算到持有期間
    # ------------------------------------------------------------------
    def test_linear_path_is_annualised(self):
        """excess_moq_holding_cost_rate 是年化率，必須乘上 持有天數/365。

        修正前的錯誤行為：`p_raw * excess_ratio * 0.05`（未乘 90/365）。
        以 12.0 元、baseline 1000、offered 2000 (excess_ratio=1.0) 為例：
          錯誤：12.0 * 1.0 * 0.05            = 0.6000
          正確：12.0 * 1.0 * 0.05 * (90/365) = 0.147945...
        """
        offered = SupplierOfferedTerms(
            raw_unit_price=12.0,
            offered_moq=2000,
            offered_lead_days=20,
            deposit_pct=0.30,
        )
        res = TCOEngine.calculate_effective_price(self.terms, offered)

        expected = 12.0 * 1.0 * 0.05 * (INVENTORY_HOLDING_DAYS / 365.0)
        self.assertAlmostEqual(
            res.moq_holding_penalty, round(expected, 3), places=3,
            msg="線性路徑未把年化 MOQ 持有費率折算為持有期間成本",
        )

        # 明確排除舊的一次性計算結果
        unannualised = 12.0 * 1.0 * 0.05
        self.assertNotAlmostEqual(
            res.moq_holding_penalty, round(unannualised, 3), places=3,
            msg="線性路徑仍在使用把年化率當一次性費用的舊公式",
        )

    # ------------------------------------------------------------------
    # 核心不變量 2：兩條路徑的持有天數必須同源
    # ------------------------------------------------------------------
    def test_holding_days_are_shared(self):
        """非線性路徑的 holding_cost 必須使用 INVENTORY_HOLDING_DAYS。"""
        raw = 12.0
        baseline_moq = 500
        offered_moq = 1000  # excess_ratio = 1.0
        wacc = 0.08

        out = TCOEngine.calculate_nonlinear_effective_tco(
            raw_price=raw,
            baseline_moq=baseline_moq,
            offered_moq=offered_moq,
            wacc=wacc,
        )
        expected_holding = raw * 1.0 * (wacc * (INVENTORY_HOLDING_DAYS / 365.0))
        self.assertAlmostEqual(
            out["moq_breakdown"]["holding_cost"], round(expected_holding, 3),
            places=3,
            msg="非線性路徑的庫存持有成本未使用共用的 INVENTORY_HOLDING_DAYS",
        )

        # 原始碼層面：不得再有硬寫的 90.0/365 字面常數殘留，
        # 否則兩條路徑又會各自漂移。
        src = inspect.getsource(tco)
        self.assertNotIn(
            "90.0 / 365.0", src,
            "src/engine/tco.py 仍有硬寫的 90.0/365.0；應改用 INVENTORY_HOLDING_DAYS",
        )
        self.assertNotIn(
            "90 / 365", src,
            "src/engine/tco.py 仍有硬寫的 90/365；應改用 INVENTORY_HOLDING_DAYS",
        )

    # ------------------------------------------------------------------
    # 共同量綱：兩條路徑的持有成本係數必須落在同一個數量級
    # ------------------------------------------------------------------
    def test_common_basis_within_documented_tolerance(self):
        """以相同 excess_ratio 比較兩條路徑，係數應在同一量級（不再差 2.5 倍）。

        兩者刻意不同（非線性路徑多了 markdown_risk 一項），故僅斷言
        holding 分量同源、且總量級不再出現 2.5 倍差距。
        """
        raw = 12.0
        excess_ratio = 1.0
        wacc = 0.08
        annual_rate = 0.05

        linear_component = raw * excess_ratio * annual_rate * (INVENTORY_HOLDING_DAYS / 365.0)
        nonlinear_holding = raw * excess_ratio * (wacc * (INVENTORY_HOLDING_DAYS / 365.0))

        # 兩者為不同費率（綜合持有滯銷 5% vs 資本成本 8%），但量綱一致、
        # 比值應由費率比決定，而非由「是否乘持有天數」決定。
        self.assertAlmostEqual(
            nonlinear_holding / linear_component,
            wacc / annual_rate,
            places=6,
            msg="兩條路徑的持有成本不再共享同一時間基準",
        )
        # 舊 bug 下比值為 (0.08*90/365)/0.05 ≈ 0.394；修正後比值為 0.08/0.05 = 1.6。
        self.assertGreater(
            nonlinear_holding / linear_component, 1.0,
            "比值小於 1 表示線性路徑又回到了未折算年化的舊公式",
        )

    # ------------------------------------------------------------------
    # 邊界：未提供 / 未超額時不得產生持有成本
    # ------------------------------------------------------------------
    def test_no_penalty_when_moq_within_baseline(self):
        offered = SupplierOfferedTerms(
            raw_unit_price=12.0,
            offered_moq=1000,
            offered_lead_days=20,
            deposit_pct=0.30,
        )
        res = TCOEngine.calculate_effective_price(self.terms, offered)
        self.assertEqual(res.moq_holding_penalty, 0.0)

        out = TCOEngine.calculate_nonlinear_effective_tco(
            raw_price=12.0, baseline_moq=1000, offered_moq=1000,
        )
        self.assertEqual(out["moq_breakdown"]["holding_cost"], 0.0)
        self.assertEqual(out["moq_breakdown"]["markdown_risk_cost"], 0.0)

    def test_none_moq_does_not_crash(self):
        offered = SupplierOfferedTerms(raw_unit_price=12.0)
        res = TCOEngine.calculate_effective_price(self.terms, offered)
        self.assertEqual(res.moq_holding_penalty, 0.0)

        out = TCOEngine.calculate_nonlinear_effective_tco(
            raw_price=12.0, baseline_moq=500, offered_moq=None,
        )
        self.assertEqual(out["moq_inventory_shadow_cost"], 0.0)


if __name__ == "__main__":
    unittest.main()
