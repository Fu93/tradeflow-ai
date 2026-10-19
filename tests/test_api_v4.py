"""端到端整合測試：Phase 6 完整 API v4 談判會話、貝氏更新、熔斷與手機指令解鎖流程。

[修正] 本測試原本以**省略 `mode`** 的方式發送輪次，並斷言結果是
`AUTOPILOT_CRUISING`。該斷言把「舊的不安全預設」固化為合約：`RoundActionRequest.mode`
的預設值現已改為 `"copilot"`（人類定奪），這是刻意的安全修正 ——
一個未指定模式的請求不應在無人核可下進入全自主巡航並可能自行結案。

因此本測試改為**明確**傳入 `"mode": "autopilot"` 來驗證自駕路徑本身仍然完整
（含熔斷與遠端解鎖）。「預設即 copilot」的行為由
`tests/test_api_security.py` 中的專屬回歸測試負責把關。
"""
import os
import shutil
import tempfile
import unittest
from fastapi.testclient import TestClient

from src.app.main import app, session_mgr
from src.session.schema import AutopilotState


class TestApiV4Integration(unittest.TestCase):
    def setUp(self):
        # [TEST ISOLATION — task-9/audit §6.4] This test used to write the repo's
        # REAL data/sessions.db (fixed session_id "test-e2e-session-v4"). It now
        # uses TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR so each run gets a private
        # store and parallel runs cannot collide or leave rows behind.
        self._tmp = tempfile.mkdtemp(prefix="tf_test_api_v4_")
        self._orig = {
            k: os.environ.get(k)
            for k in ("TRADEFLOW_DB_PATH", "TRADEFLOW_STORE_DIR",
                      "TRADEFLOW_WEBHOOK_TOKEN", "TRADEFLOW_API_KEY")
        }
        os.environ["TRADEFLOW_DB_PATH"] = os.path.join(self._tmp, "sessions.db")
        os.environ["TRADEFLOW_STORE_DIR"] = os.path.join(self._tmp, "sessions_json")
        # Keep the sandbox fail-open behaviour this suite was written against.
        os.environ.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
        os.environ.pop("TRADEFLOW_API_KEY", None)
        # Remember where the global manager pointed so tearDown can restore a
        # LIVE location; otherwise later test classes inherit a manager whose
        # store_dir has just been deleted.
        self._prev_paths = (session_mgr.db_path, session_mgr.store_dir)
        session_mgr.__init__(
            db_path=os.environ["TRADEFLOW_DB_PATH"],
            store_dir=os.environ["TRADEFLOW_STORE_DIR"],
        )
        self.client = TestClient(app)
        self.test_session_id = "test-e2e-session-v4"

    def tearDown(self):
        for k, v in self._orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Restore the global manager to its previous (live) store before removing
        # the temp dir, so subsequent test classes are not left dangling.
        session_mgr.__init__(db_path=self._prev_paths[0], store_dir=self._prev_paths[1])
        shutil.rmtree(self._tmp, ignore_errors=True)

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

        # 2. 正常輪次：供應商報價 14.80（明確選擇 autopilot 自駕路徑）
        round1_resp = self.client.post(
            f"/api/v1/sessions/{self.test_session_id}/round",
            json={
                "opponent_offer_price": 14.80,
                "opponent_raw_text": "親，初次合作特惠價 ¥14.80 / 件",
                "mode": "autopilot",
            },
        )
        self.assertEqual(round1_resp.status_code, 200)
        res1 = round1_resp.json()
        self.assertEqual(res1["autopilot_state"], AutopilotState.AUTOPILOT_CRUISING.value)
        turn1 = res1["turn"]
        self.assertEqual(turn1["round_index"], 1)
        self.assertGreater(turn1["parsed_price"], 10.0)
        self.assertLessEqual(turn1["parsed_price"], 14.80)

        # 3. 觸發熔斷：供應商報出極端高價，突破上限或觸發破局風險
        # 模擬超高報價 18.00（高於 ceiling 16.20，觸發熔斷）
        round2_resp = self.client.post(
            f"/api/v1/sessions/{self.test_session_id}/round",
            json={
                "opponent_offer_price": 18.00,
                "opponent_raw_text": "成本上漲，最低 18.00 一分不降",
                "mode": "autopilot",
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
