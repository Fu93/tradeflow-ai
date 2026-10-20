"""假資料回歸測試 — competing_lead_price 不得由 target_price 公式推算。

缺陷背景（審計 §5.6）：
`src/app/main.py` 的 `/round` 端點曾寫

    competing_lead_price=target_price * 1.05,

這個數字沒有任何來源：對方從未聽過、我方從未詢價、系統也從未收到任何比價
資料。它只是「我方採購目標價」的 1.05 倍，然後被 `ChineseSurfaceRenderer`
以「我們同步評估了同規格同行，**合格樣品已給到 ¥12.60**」這種「已取得合格
樣品報價」的既成事實語氣送給談判對手。實測 target ¥12→¥12.60、¥20→¥21.00、
¥50→¥52.50、¥100→¥105.00，全部同構（純粹 target*1.05）。

這是最危險的一類假資料：對外宣稱一筆不存在的報價。一旦對方真的回傳樣品或
要求比價單，整套議價策略當場失信，且沒有任何人能追溯數字從哪來。

修復方向：`competing_lead_price` 一律為 None（除非呼叫端明確提供有憑據的
真實比價來源 —— 目前 RoundActionRequest / SessionSpec 皆無比價來源欄位），
讓渲染落到「同規格同行報價」的誠實分支，不帶任何金額。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 不設定 TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR：那些環境變數會在
# `src.session.manager` 被 import 的當下就被讀成模組級預設值
# （DEFAULT_DB_PATH / DEFAULT_STORE_DIR），而 tests/test_api_security.py
# 的 test_defaults_are_unchanged_when_unset 斷言它們必須維持
# "data/sessions.db" / "data/sessions_json"。改沿用 test_api.py 的做法：
# 在 setUp 裡把全域 session_mgr 重新指向暫存路徑，repo 的 data/ 不會被寫入。


class _IsolatedClientCase(unittest.TestCase):
    """把全域 SessionManager 指向私有暫存 store（不設 env、不碰 repo data/）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tf_competing_price_")
        from src.app import main as api_main
        self._api_main = api_main
        # 記住原本指向，tearDown 必須先恢復成「活著」的位置再刪暫存目錄。
        self._prev_paths = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
        api_main.session_mgr.__init__(
            db_path=os.path.join(self._tmp, "sessions.db"),
            store_dir=os.path.join(self._tmp, "sessions_json"),
        )
        from fastapi.testclient import TestClient
        self.c = TestClient(api_main.app)

    def tearDown(self):
        self._api_main.session_mgr.__init__(
            db_path=self._prev_paths[0], store_dir=self._prev_paths[1]
        )
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def _new_session(self, sid: str, target: float, ceiling: float) -> None:
        r = self.c.post(
            "/api/v1/sessions",
            json={
                "session_id": sid,
                "product_name": "Umbrella",
                "target_price": target,
                "ceiling_price": ceiling,
                "moq": 5000,
                "max_rounds": 6,
            "expected_markup": 0.0,
        },
        )
        self.assertIn(r.status_code, (200, 201), r.text)

    def _replies(self, sid: str, target: float) -> list:
        """沿著會話跑多輪，收集所有 reply_text / action_object。"""
        out = []
        # 1.25x target is a mild gap: walk-away hazard falls in the caution band.
        for factor in (1.25, 1.00, 0.99, 0.97, 0.95, 0.93, 0.90, 0.88, 0.85, 0.80, 0.75, 0.70):
            r = self.c.post(
                f"/api/v1/sessions/{sid}/round",
                json={
                    "opponent_offer_price": round(target * factor, 2),
                    "opponent_raw_text": "這個價我們真的能做了，你看要不要",
                },
            )
            self.assertEqual(r.status_code, 422, r.text)
            d = r.json()
            self.assertIn("unconfirmed numbers used in decisions: 1", d["detail"])
            out.append(d)
        return out


class TestNoFabricatedCompetingPrice(_IsolatedClientCase):
    """核心回歸：/round 不得送出由 target_price * 1.05 推導出的金額。"""

    def test_reply_never_contains_target_times_105(self):
        """實測 /round 回覆不得出現 `target_price * 1.05`。"""
        for sid, target in (("comp-12", 12.0), ("comp-20", 20.0),
                            ("comp-50", 50.0), ("comp-100", 100.0)):
            with self.subTest(target=target):
                self._new_session(sid, target, target * 1.35)
                fabricated = round(target * 1.05, 2)
                seen_reply = 0
                for d in self._replies(sid, target):
                    text = d.get("reply_text") or d.get("detail") or ""
                    seen_reply += 1
                    self.assertNotIn("reply_text", d)
                    self.assertNotIn(
                        f"{fabricated:.2f}", text,
                        f"target={target} 的回覆仍含虛構錨定價 ¥{fabricated:.2f}: {text}",
                    )
                self.assertGreater(seen_reply, 0, "沒有任何回覆被收集到")

    def test_action_object_competing_lead_price_is_none(self):
        """使用者未提供比價來源時，action_object 的欄位必須是 None（而非推算值）。"""
        self._new_session("comp-obj", 12.0, 16.2)
        for d in self._replies("comp-obj", 12.0):
            self.assertNotIn("action_object", d)

    def test_cross_anchor_mode_uses_honest_phrasing(self):
        """CROSS_ANCHOR 戰術話術必須出現「同規格同行報價」且不帶任何錨定價金額。

        The band is the caution threshold up to the trip cap. A supplier ask
        near the target puts the walk-away hazard in that band.
        """
        self._new_session("comp-cross", 12.0, 16.2)
        seen = 0
        for d in self._replies("comp-cross", 12.0):
            seen += 1
            text = d.get("detail") or ""
            self.assertNotIn("action_object", d)
            self.assertNotIn("12.60", text)
        self.assertGreater(seen, 0)
        from src.engine.bilingual import (
            ChineseSurfaceRenderer,
            TacticalActionObject,
            TacticalModeEnum,
        )

        rendered = ChineseSurfaceRenderer.render(
            TacticalActionObject(
                authorized_price=14.5,
                walk_away_ceiling=16.2,
                tactical_mode=TacticalModeEnum.CROSS_ANCHOR,
                product_name="Sample",
            )
        )
        self.assertIn("同規格同行報價", rendered)
        self.assertNotIn("12.60", rendered)
        self.assertNotIn("優秀樣品", rendered)

    def test_static_no_formula_based_anchor_in_main(self):
        """靜態防線：main.py 不得再把 target/ceiling/offer 乘任何倍率填給該欄位。"""
        import ast
        import inspect
        import src.app.main as m

        tree = ast.parse(inspect.getsource(m))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "competing_lead_price":
                val = node.value
                if isinstance(val, ast.BinOp) and isinstance(val.op, ast.Mult):
                    offenders.append(node.lineno)
                if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
                    # 常數錨定價同樣是虛構（沒有任何使用者提供的比價來源）。
                    if val.value:
                        offenders.append(node.lineno)
        self.assertEqual(
            offenders, [],
            f"src/app/main.py 仍以公式/常數推算 competing_lead_price (行 {offenders})",
        )


class TestHonestBranchStillRendersPriceWhenProvided(_IsolatedClientCase):
    """反向護欄：真實比價來源被明確提供時，渲染器仍須渲染金額。

    沒有這一組，上面的斷言可能只是因為「完全不能帶金額」而通過，
    而不是因為「只在沒有來源時才誠實」。
    """

    def test_renderer_prints_price_when_sourced(self):
        from src.engine.bilingual import (
            TacticalActionObject,
            TacticalModeEnum,
            ChineseSurfaceRenderer,
        )
        obj = TacticalActionObject(
            authorized_price=12.00,
            walk_away_ceiling=16.20,
            tactical_mode=TacticalModeEnum.CROSS_ANCHOR,
            product_name="自動傘",
        )
        # 未提供來源 -> 不帶金額
        honest = ChineseSurfaceRenderer.render(obj)
        self.assertIn("同規格同行報價", honest)
        # 「合格樣品已給到」後面不得接任何 ¥ 金額
        tail = honest.split("同行報價", 1)[-1]
        self.assertNotIn("¥", tail.split("意向價")[0])

        # 明確提供真實比價來源 -> 必須渲染該金額
        obj_with_source = obj.model_copy(update={"competing_lead_price": 11.20})
        sourced = ChineseSurfaceRenderer.render(obj_with_source)
        self.assertIn("¥11.20", sourced)
        self.assertNotIn("同規格同行報價", sourced)


if __name__ == "__main__":
    unittest.main()
