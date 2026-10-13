"""端到端整合測試：Phase 6 完整 API v4 談判會話、貝氏更新、熔斷與手機指令解鎖流程。"""
import unittest
from fastapi.testclient import TestClient

from src.app.main import app
from src.session.schema import AutopilotState


class TestApiV4Integration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_session_id = "test-e2e-session-v4"

    def test_end_to_end_negotiation_and_circuit_breaker_cycle(self):
        # 1. 建立會話
        create_resp = self.client.post(
            "/api/v1/sessions",
            json={
                "session_id": self.test_session_id,
                "supplier_id": "supp-test-vendor-1688",
                "product_name": "Carbon Auto Umbrella",
                "target_price": 12.0,
                "ceiling_price": 16.2,
                "moq": 10000,
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        sess = create_resp.json()
        self.assertEqual(sess["spec"]["session_id"], self.test_session_id)
        self.assertEqual(sess["state"], AutopilotState.IDLE.value)
        self.assertAlmostEqual(sess["spec"]["target_price"], 12.0)

        # 2. 正常輪次：供應商報價 14.80
        round1_resp = self.client.post(
            f"/api/v1/sessions/{self.test_session_id}/round",
            json={
                "opponent_offer_price": 14.80,
                "opponent_raw_text": "親，初次合作特惠價 ¥14.80 / 件",
            },
        )
        self.assertEqual(round1_resp.status_code, 200)
        res1 = round1_resp.json()
        self.assertEqual(res1["autopilot_state"], AutopilotState.AUTOPILOT_CRUISING.value)
        turn1 = res1["turn"]
        self.assertEqual(turn1["round_index"], 1)
        self.assertGreater(turn1["parsed_price"], 10.0)
        self.assertLess(turn1["parsed_price"], 14.80)

        # 3. 觸發熔斷：供應商報出極端高價，突破上限或觸發破局風險
        # 模擬超高報價 18.00（高於 ceiling 16.20，觸發熔斷）
        round2_resp = self.client.post(
            f"/api/v1/sessions/{self.test_session_id}/round",
            json={
                "opponent_offer_price": 18.00,
                "opponent_raw_text": "成本上漲，最低 18.00 一分不降",
            },
        )
        self.assertEqual(round2_resp.status_code, 200)
        res2 = round2_resp.json()
        self.assertTrue(res2["circuit_tripped"])
        self.assertEqual(res2["autopilot_state"], AutopilotState.CIRCUIT_TRIPPED_ESCALATED.value)

        # 4. 手機端遠端授權指令：回覆 1 (妥協一步)
        cmd_resp = self.client.post(
            "/webhook/mobile-command",
            json={
                "command": "1",
                "session_id": self.test_session_id,
            },
        )
        self.assertEqual(cmd_resp.status_code, 200)
        cmd_data = cmd_resp.json()
        self.assertEqual(cmd_data["status"], "success")
        self.assertEqual(cmd_data["action"], "concede_step")

        # 5. 驗證會話已從熔斷狀態解鎖回自駕巡航
        check_resp = self.client.get(f"/api/v1/sessions/{self.test_session_id}")
        self.assertEqual(check_resp.status_code, 200)
        updated_sess = check_resp.json()
        self.assertEqual(updated_sess["state"], AutopilotState.AUTOPILOT_CRUISING.value)


if __name__ == "__main__":
    unittest.main()
