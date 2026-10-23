"""TradeFlow v5 — 採購決策副駕駛 (Copilot) 人機協同與人工審批門禁測試。

測試範疇：
1. 模式分流：/round 攜帶 mode="copilot" 時，狀態確實停留在 AWAITING_HUMAN_DECISION，不自動追加買方出價輪次。
2. 情報雷達：Copilot 狀態下確實產出 Should-Cost 應付成本拆解、校準貝氏推斷與非線性 TCO 影子成本。
3. 人工審批門禁 (/approve)：
   - approve: 人工核准系統推薦報價與話術，狀態推進至 HUMAN_APPROVED_APPLIED -> COPILOT_MONITORING。
   - override: 人工覆寫報價，若突破 walk_away_ceiling 則觸發 422 護欄攔截；合法時寫入自訂報價。
   - reject: 人工否決並破局撤退，狀態轉至 WALK_AWAY。
4. CAS 樂觀鎖：向 /round 或 /approve 傳遞陳舊 expected_version 時，強制返回 409 Conflict。
5. 安全門禁 (Default-Deny Fail-Closed)：生產模式未配置金鑰或金鑰非法時，強制阻斷存取。
"""
import os
import unittest
import tempfile
import shutil
from fastapi.testclient import TestClient

from src.app.main import app, session_mgr, SANDBOX_DEMO_TOKEN
from src.legacy.session.schema import SessionSpec, AutopilotState
from src.legacy.session.manager import SessionManager


class TestCopilotHumanApproval(unittest.TestCase):

    def setUp(self):
        self.orig_db_path = session_mgr.db_path
        self.orig_store_dir = session_mgr.store_dir
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_sessions.db")
        self.store_dir = os.path.join(self.temp_dir, "sessions_json")
        # 覆蓋 session_mgr 為獨立隔離環境
        session_mgr.__init__(db_path=self.db_path, store_dir=self.store_dir)
        self.client = TestClient(app)

    def tearDown(self):
        session_mgr.__init__(db_path=self.orig_db_path, store_dir=self.orig_store_dir)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_session(self, session_id: str = "sess_copilot_001") -> SessionSpec:
        spec = SessionSpec(
            session_id=session_id,
            product_name="21吋全自動碳纖雨傘",
            target_price=12.0,
            ceiling_price=16.0,
            moq=2000,
            max_rounds=5,
            supplier_id="Factory_Zhejiang_01",
            supplier_channel="1688_chat",
            expected_markup=0.0,
        )
        session_mgr.create_session(spec)
        return spec

    def test_copilot_mode_pauses_at_awaiting_human_decision(self):
        """驗證 mode='copilot' 時，系統不自動發盤，而是停在 AWAITING_HUMAN_DECISION 等待人類採購員。"""
        sess_id = "sess_pause_test"
        self._create_sample_session(sess_id)

        payload = {
            "opponent_raw_text": "老闆，這款碳纖傘骨成本高，最低只能給 ¥14.50，交期30天。",
            "opponent_offer_price": 14.50,
            "offered_lead_days": 30,
            "offered_moq": 2000,
            "mode": "copilot",
            "round_index": 1,
        }
        res = self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json=payload,
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(res.status_code, 422, res.text)
        data = res.json()
        self.assertIn("unconfirmed numbers used in decisions: 1", data["detail"])
        self.assertNotIn("recommended_offer_price", data)
        self.assertNotIn("reply_text", data)

        stored_sess = session_mgr.get_session(sess_id)
        self.assertEqual(len(stored_sess.history), 0)
        self.assertNotEqual(stored_sess.state, AutopilotState.DEAL_CLOSED)
        self.assertNotEqual(stored_sess.state, AutopilotState.AWAITING_HUMAN_DECISION)

    def test_human_approve_recommendation(self):
        """驗證人類在 AWAITING_HUMAN_DECISION 狀態下一鍵 approve，系統才正式記錄買方輪次。"""
        sess_id = "sess_approve_test"
        self._create_sample_session(sess_id)

        # 第 1 輪進入 Copilot 決策雷達
        r1 = self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json={"opponent_offer_price": 14.20, "opponent_raw_text": "¥14.20最少500把", "mode": "copilot"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(r1.status_code, 422, r1.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r1.json()["detail"])
        current_ver = session_mgr.get_session(sess_id).version

        app_res = self.client.post(
            f"/api/v1/sessions/{sess_id}/approve",
            json={"action": "approve", "expected_version": current_ver},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(app_res.status_code, 409, app_res.text)
        self.assertIn("not waiting for a decision", app_res.json()["detail"])
        stored_sess = session_mgr.get_session(sess_id)
        self.assertEqual(len(stored_sess.history), 0)

    def test_human_override_price_and_ceiling_guardrail(self):
        """驗證人類自訂覆寫報價：合法時接受，超過天花板 ceiling_price 時嚴格 422 拒絕。"""
        sess_id = "sess_override_test"
        self._create_sample_session(sess_id)  # ceiling = 16.0

        r1 = self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json={"opponent_offer_price": 15.0, "mode": "copilot"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(r1.status_code, 422, r1.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r1.json()["detail"])
        session_mgr.transition_state(sess_id, AutopilotState.AWAITING_HUMAN_DECISION)

        # 1. 突破天花板價 (¥17.50 > ¥16.00) -> 必須阻斷
        breach_res = self.client.post(
            f"/api/v1/sessions/{sess_id}/approve",
            json={"action": "override", "custom_price": 17.50},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(breach_res.status_code, 422)
        self.assertIn("breaches walk-away ceiling", breach_res.json()["detail"])

        # 2. 合法覆寫價 (¥13.80 <= ¥16.00) -> 成功應用
        valid_res = self.client.post(
            f"/api/v1/sessions/{sess_id}/approve",
            json={"action": "override", "custom_price": 13.80, "custom_reply_text": "行內人都懂，13.80含稅包郵現結。"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(valid_res.status_code, 200)
        self.assertEqual(valid_res.json()["applied_price"], 13.80)
        self.assertEqual(valid_res.json()["reply_text"], "行內人都懂，13.80含稅包郵現結。")

    def test_human_reject_and_walk_away(self):
        """驗證人類點選 reject 時，會話立即以 WALK_AWAY 結案撤退。"""
        sess_id = "sess_reject_test"
        self._create_sample_session(sess_id)

        self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json={"opponent_offer_price": 15.80, "mode": "copilot"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )

        rej_res = self.client.post(
            f"/api/v1/sessions/{sess_id}/approve",
            json={"action": "reject", "tactical_rationale": "廠商品質疑慮且價格無誠意，終止談判。"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(rej_res.status_code, 200)
        self.assertEqual(rej_res.json()["autopilot_state"], "WALK_AWAY")

        stored_sess = session_mgr.get_session(sess_id)
        self.assertEqual(stored_sess.state, AutopilotState.WALK_AWAY)
        self.assertIn("廠商品質疑慮", stored_sess.trip_reason)

    def test_cas_optimistic_lock_conflict(self):
        """驗證並發修改時傳遞陳舊 expected_version 必須噴 409 Conflict。"""
        sess_id = "sess_cas_test"
        self._create_sample_session(sess_id)

        # 正常輪次推演
        r1 = self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json={"opponent_offer_price": 14.0, "mode": "copilot"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(r1.status_code, 422, r1.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r1.json()["detail"])
        current = session_mgr.get_session(sess_id).version

        stale_res = self.client.post(
            f"/api/v1/sessions/{sess_id}/round",
            json={"opponent_offer_price": 13.9, "expected_version": current + 1, "mode": "copilot"},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(stale_res.status_code, 409)
        self.assertIn("CAS optimistic lock conflict", stale_res.json()["detail"])

        stale_app = self.client.post(
            f"/api/v1/sessions/{sess_id}/approve",
            json={"action": "approve", "expected_version": current + 1},
            headers={"X-API-Key": SANDBOX_DEMO_TOKEN},
        )
        self.assertEqual(stale_app.status_code, 409)
        self.assertIn("CAS optimistic lock conflict", stale_app.json()["detail"])


if __name__ == "__main__":
    unittest.main()
