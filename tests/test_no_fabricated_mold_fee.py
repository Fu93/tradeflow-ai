"""假資料回歸測試 — 模具費必須來自對方原文（審計 §5.5）。

背景：`src/app/main.py` 曾把對方模具費**寫死為 3000.0**，系統在毫不知情對方
開價的情況下憑空發明一筆「¥3000 模具費」，再向對方宣稱全額減免。這是
「對外宣稱一個從未被任何來源支持的數字」的典型假資料缺陷。

另外 `InboundTacticalClassification` 的**最高頻回傳路徑（未識別 → 暫按正常處理）**
曾漏傳 `demanded_mold_fee`，使已成功抽取的數字在傳給 logrolling 前被靜默丟棄
—— 同樣是「有數字卻送不出去」的同類缺陷。

本檔為這兩者的守衛：在修復前，`test_supplier_quoted_fee_reaches_action_object`
與 `test_no_fee_means_zero_not_hardcoded` 會失敗。
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="tf_mold_guard_")
os.environ["TRADEFLOW_DB_PATH"] = os.path.join(TMP, "sessions.db")
os.environ["TRADEFLOW_STORE_DIR"] = os.path.join(TMP, "sessions_json")
os.environ["TRADEFLOW_ENV_PATH"] = os.path.join(TMP, "env_guard")
for _k in ("TRADEFLOW_ENV", "TRADEFLOW_API_KEY", "TRADEFLOW_WEBHOOK_TOKEN"):
    os.environ.pop(_k, None)


class TestMoldFeeIsNeverFabricated(unittest.TestCase):
    """驗證系統不會虛構模具費金額。"""

    # ---------------------------------------------------------------
    # 1. 靜態防線：原始碼不得再出現寫死的模具費金額
    # ---------------------------------------------------------------
    def test_no_hardcoded_mold_fee_in_main(self):
        """src/app/main.py 不得有寫死的模具費金額。

        用 AST 走訪**實際程式碼**的關鍵字/預設值，因此註解與 docstring 中
        描述舊版缺陷的文字不會造成誤判。
        """
        import ast
        import src.app.main as m

        tree = ast.parse(inspect.getsource(m))
        offenders = []
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.keyword) and node.arg in (
                "mold_fee", "waive_mold_fee_amount", "waive_mold_amount"
            ):
                targets.append((node.arg, node.value))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and "mold" in t.id.lower():
                        targets.append((t.id, node.value))
            for name, val in targets:
                if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
                    if val.value == 3000:
                        offenders.append((getattr(node, "lineno", "?"), name, val.value))
        self.assertEqual(
            offenders, [],
            f"src/app/main.py 仍存在寫死的模具費 3000: {offenders}",
        )

    def test_every_return_site_passes_mold_fee(self):
        """每一條 classification 回傳路徑都必須傳 demanded_mold_fee。

        空輸入（無可抽取內容）與合規安全閘門（length-independent escalation）
        為已知例外，但**未識別保底路徑**必須傳遞，否則下游永遠取不到金額。
        """
        src = open("src/engine/bilingual.py", encoding="utf-8").read()
        returns = re.findall(
            r"return InboundTacticalClassification\((.*?)\n\s*\)", src, re.DOTALL
        )
        self.assertGreaterEqual(len(returns), 7)
        missing = [blk for blk in returns if "demanded_mold_fee" not in blk]
        # 唯一合法例外：空輸入守衛（沒有任何可抽取內容）
        allowed = ["Empty input; unclassified"]
        unresolved = [
            blk[:70].replace("\n", " ")
            for blk in missing
            if not any(a in blk for a in allowed)
        ]
        self.assertEqual(
            unresolved, [],
            f"以下 classification 回傳漏傳 demanded_mold_fee: {unresolved}",
        )

    # ---------------------------------------------------------------
    # 2. 動態防線：抽取結果必須真的流到 action_object
    # ---------------------------------------------------------------
    def test_supplier_quoted_fee_reaches_action_object(self):
        from fastapi.testclient import TestClient
        import src.app.main as m

        c = TestClient(m.app)
        sid = "mold-guard-quoted"
        c.post(
            "/api/v1/sessions",
            json={"session_id": sid, "product_name": "Umbrella",
                  "target_price": 12.0, "ceiling_price": 16.2,
                  "moq": 5000, "max_rounds": 6},
        )
        saw_fee = False
        for price, txt in [
            (14.80, "首次開模，模具費 4500，單價 14.80"),
            (14.60, "模具費 4500 另計，最低 14.60"),
            (14.50, "模具費 4500，單價 14.50 最低了"),
        ]:
            d = c.post(f"/api/v1/sessions/{sid}/round",
                       json={"opponent_offer_price": price,
                             "opponent_raw_text": txt}).json()
            ao = d.get("action_object", {}) or {}
            amt = ao.get("waive_mold_fee_amount") or 0
            if amt:
                saw_fee = True
                # 金額必須是對方實際報出的 4500，不是虛構的 3000
                self.assertEqual(
                    float(amt), 4500.0,
                    "waive_mold_fee_amount 應為對方實際報出的金額",
                )
        self.assertTrue(
            saw_fee,
            "對方明確報出模具費 4500，卻沒有任何輸出帶到該金額",
        )

    def test_no_fee_means_zero_not_hardcoded(self):
        """對方從未報模具費 → 金額必須為 0（而非舊版寫死的 3000）。"""
        from fastapi.testclient import TestClient
        import src.app.main as m

        c = TestClient(m.app)
        sid = "mold-guard-nofee"
        c.post(
            "/api/v1/sessions",
            json={"session_id": sid, "product_name": "Umbrella",
                  "target_price": 12.0, "ceiling_price": 16.2,
                  "moq": 5000, "max_rounds": 6},
        )
        for price, txt in [
            (14.80, "老闆初次合作特惠價 ¥14.80 / 件"),
            (14.60, "原料漲了，最低 14.60，沒利潤"),
            (14.50, "真的壓不下來 14.50，愛做不做"),
        ]:
            d = c.post(f"/api/v1/sessions/{sid}/round",
                       json={"opponent_offer_price": price,
                             "opponent_raw_text": txt}).json()
            ao = d.get("action_object", {}) or {}
            amt = float(ao.get("waive_mold_fee_amount") or 0)
            self.assertEqual(
                amt, 0.0,
                f"對方未報模具費，waive_mold_fee_amount 卻為 {amt}（虛構金額）",
            )

    def test_extract_helper_never_invents(self):
        """_extract_mold_fee 對無模具費文本必須回 None，而非任何預設值。"""
        from src.app.main import _extract_mold_fee
        for t in ["單價 14.80，沒提模具費。", "模具費另計（未報數字）。", ""]:
            self.assertIsNone(
                _extract_mold_fee(t),
                f"_extract_mold_fee({t!r}) 應為 None",
            )
        self.assertEqual(_extract_mold_fee("模具費 4500"), 4500.0)


if __name__ == "__main__":
    unittest.main()
