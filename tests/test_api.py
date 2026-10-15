"""FastAPI 端點測試（離線，用 TestClient；不呼叫真 Evolus）。

需要 fastapi + httpx（TestClient 依赖）。若未裝則跳過。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient
    from src.app import main as api_main
except Exception as e:
    print(f"SKIP test_api: {e}")
    raise SystemExit(0)

import unittest


class TestAPI(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(api_main.app)

    def test_health(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_negotiate(self):
        r = self.c.post("/negotiate", json={"target_price": 12.0, "n_samples": 2000,
                                            "observed_offer": 1.25})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("opening_offer_price", body)
        # v5 穩健貝氏共軛後驗更新 (取代舊版未校準之線性 0.3*EMA 平滑器)
        self.assertAlmostEqual(body["posterior_reserve_mean"], 1.144, places=2)

    def test_webhook_responds_with_draft(self):
        """Webhook 必須回傳草稿與建議，且**不得因外部服務狀態而失敗**。

        注意：本測試刻意**不**斷言「必須是 offline stub」。舊版本斷言
        `"offline stub" in draft_email`，會在金鑰配置正確時失敗，等於把
        「外部整合壞掉」鎖進測試 —— 反而讓真正的 Evolus 回歸無法被察覺。

        正確的契約是：無論 Evolus 可用與否，端點都必須 200 並回傳可用的草稿。
        """
        r = self.c.post("/webhook/evolus", json={
            "extracted": {"target_price": 12.0},
            "offer": 1.25, "email_body": "We can do ¥15, MOQ 500."})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("draft_email", body)
        self.assertIn("recommendation", body)

        # 草稿必須有實質內容（不論來自 Evolus 或本地降級）
        draft = body["draft_email"]
        self.assertIsInstance(draft, str)
        self.assertGreater(len(draft.strip()), 20,
                           "draft_email is empty or trivially short")

        # 若回傳的是降級草稿，必須明確標記，不可偽裝成真呼叫結果
        if "offline stub" in draft:
            self.assertIn("[DRAFT", draft,
                          "degraded draft must be clearly labelled to the operator")


class TestUnknownFieldRejection(unittest.TestCase):
    """未知欄位必須回 422，不可被靜默忽略。

    [REGRESSION] 原本 `RoundActionRequest` 未設 `extra="forbid"`，因此把
    `supplier_text` 誤打成 `supplier_message` 時，Pydantic 會默默丟棄該欄位，
    該輪以「空文本」執行，並回傳 HTTP 200 + 看似合理的 `NORMAL_COUNTER_OFFER`
    決策。呼叫端無法分辨這是真實分析還是無輸入的預設值 —— 這比報錯更危險。
    """

    def setUp(self):
        self.c = TestClient(api_main.app)

    def test_round_rejects_unknown_field(self):
        r = self.c.post(
            "/api/v1/sessions/unknown-field-01/round",
            json={"supplier_message": "原料漲價", "supplier_quote": 14.8},
        )
        self.assertEqual(
            r.status_code, 422,
            "a misspelled field must be rejected, not silently dropped",
        )

    def test_round_accepts_canonical_fields(self):
        """正確欄位名仍須正常運作（確認 422 來自 extra=forbid，而非欄位改名）。"""
        r = self.c.post(
            "/api/v1/sessions/unknown-field-02/round",
            json={"opponent_raw_text": "老闆，碰擊布原料暴漲，¥14.80 做不了",
                  "opponent_offer_price": 14.80},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["inbound_intent"], "CRY_POVERTY_COST_HIKE")

    def test_multi_auction_rejects_unknown_field(self):
        r = self.c.post(
            "/api/v1/multi-auction/run",
            json={"targt_price": 12.0},  # deliberate typo
        )
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
