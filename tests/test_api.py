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
        self.assertIn("posterior_reserve_mean", body)
        self.assertAlmostEqual(body["posterior_reserve_mean"], 1.155, places=2)

    def test_webhook_offline(self):
        r = self.c.post("/webhook/evolus", json={
            "extracted": {"target_price": 12.0},
            "offer": 1.25, "email_body": "We can do ¥15, MOQ 500."})
        self.assertEqual(r.status_code, 200)
        self.assertIn("draft_email", r.json())
        self.assertIn("recommendation", r.json())
        # offline 模式應含 stub 標記
        self.assertIn("offline stub", r.json()["draft_email"])


if __name__ == "__main__":
    unittest.main()
