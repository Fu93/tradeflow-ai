"""單元測試：Phase 2 通訊傳輸協定 (EmailStdTransport, WebhookHub, TelemetryNotifier) 與 API。"""
import asyncio
import os
import shutil
import tempfile
import unittest
from fastapi.testclient import TestClient

from src.transport.base import BaseTransport, InboundMessage
from src.transport.email_std import EmailStdTransport
from src.transport.webhook_hub import WebhookHub, SessionExecutionBridge
from src.transport.telemetry import TelemetryNotifier
from src.session.schema import RemoteAuthCommand, SessionSpec
from src.app.main import app
import src.app.main as api_main


class TestTransportStd(unittest.TestCase):
    def test_price_semantic_extractor(self):
        """測試各種複雜語意下的階梯式價格提取器。"""
        cases = [
            ("親，這批雨傘單價 ¥14.80，MOQ 500", 14.80),
            ("Our unit price: $12.50 per piece", 12.50),
            ("工廠核算過，最低只能給到 13.20 元", 13.20),
            ("報價：RMB 15.00，含稅", 15.00),
            ("can do 13.8 at most", 13.80),
            ("一口價 11.5", 11.50),
            ("今天天氣很好，沒有報價", None),
        ]
        for text, expected in cases:
            extracted = BaseTransport.extract_offer(text)
            if expected is None:
                self.assertIsNone(extracted, f"Failed on: {text}")
            else:
                self.assertIsNotNone(extracted, f"Failed on: {text}")
                self.assertAlmostEqual(extracted, expected, places=2, msg=f"Mismatch on: {text}")

    def test_email_std_mock_mode(self):
        """測試無 SMTP/IMAP 憑證時的離線 Mock 派發與收件。"""
        transport = EmailStdTransport()
        self.assertTrue(transport.is_mock_mode)

        # 模擬發信
        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            transport.send_message(
                recipient="supplier@factory.com",
                content="Please quote for 500 umbrellas",
                metadata={"session_id": "sess_mock_email", "round_index": 1, "human_approved": True},
            )
        )
        self.assertTrue(res)
        self.assertEqual(len(transport.mock_sent_box), 1)
        self.assertIn("sess_mock_email", transport.mock_sent_box[0]["subject"])

        refused = EmailStdTransport()
        blocked = loop.run_until_complete(
            refused.send_message(
                recipient="supplier@factory.com",
                content="Please quote for 500 umbrellas",
                metadata={"session_id": "sess_unapproved"},
            )
        )
        self.assertFalse(blocked)
        self.assertEqual(len(refused.mock_sent_box), 0)

        # 模擬收信
        transport.mock_inbox.append(
            InboundMessage(
                channel="email",
                session_id="sess_mock_email",
                sender="supplier@factory.com",
                content="We can offer ¥14.20 per unit.",
                parsed_price=14.20,
            )
        )
        inbound = loop.run_until_complete(transport.poll_inbound())
        self.assertEqual(len(inbound), 1)
        self.assertEqual(inbound[0].parsed_price, 14.20)
        loop.close()

    def test_webhook_hub_dispatch(self):
        """測試手機端指令解碼與分發。"""
        hub = WebhookHub()
        hub.register_bridge("sess_wh_1")

        # 測試指令 1 (妥協)
        cmd, p = hub.dispatch_command("sess_wh_1", "1")
        self.assertEqual(cmd, RemoteAuthCommand.CONCEDE)
        self.assertIsNone(p)

        # 測試指令 2 (堅持)
        hub.register_bridge("sess_wh_2")
        cmd, p = hub.dispatch_command("sess_wh_2", "HOLD_FIRM")
        self.assertEqual(cmd, RemoteAuthCommand.HOLD_FIRM)

        # 測試指令 3 (撤退)
        hub.register_bridge("sess_wh_3")
        cmd, p = hub.dispatch_command("sess_wh_3", "3")
        self.assertEqual(cmd, RemoteAuthCommand.WALK_AWAY)

        # 測試自訂價格
        hub.register_bridge("sess_wh_4")
        cmd, p = hub.dispatch_command("sess_wh_4", "¥13.25")
        self.assertEqual(cmd, RemoteAuthCommand.OVERRIDE_PRICE)
        self.assertAlmostEqual(p, 13.25)

    def test_webhook_async_wait_and_unlock(self):
        """測試異步掛起與瞬間喚醒。"""
        hub = WebhookHub()
        loop = asyncio.new_event_loop()

        async def scenario():
            hub.register_bridge("sess_async")

            async def mock_remote_response():
                await asyncio.sleep(0.05)
                hub.dispatch_command("sess_async", "1")

            asyncio.create_task(mock_remote_response())
            cmd, _ = await hub.wait_for_auth("sess_async", timeout_sec=1.0)
            return cmd

        result = loop.run_until_complete(scenario())
        self.assertEqual(result, RemoteAuthCommand.CONCEDE)
        loop.close()

    def test_telemetry_notifier(self):
        """測試手機端遙測通知格式化與 Mock 隊列記錄。"""
        notifier = TelemetryNotifier()
        res = notifier.notify_circuit_breaker(
            session_id="sess_telemetry",
            product="auto umbrella",
            opponent_offer=14.80,
            p_break=0.184,
            advice_text="Recommended offer ¥13.20",
            proposed_price=13.20,
        )
        self.assertEqual(res["event_type"], "CIRCUIT_BREAKER")
        self.assertIn("14.80", res["content"])
        self.assertIn("18.4%", res["content"])
        self.assertEqual(len(notifier.mock_history), 1)


class TestAppV4Endpoints(unittest.TestCase):
    """[REGRESSION — 見 DEEP_DEFECT_AUDIT.md §6.4 / w1 報告]

    本類別是**最後一個**未隔離的 API 測試：它原本直接對 repo 真實的
    `data/sessions.db` 建立會話（`test_session_and_round_processing` 每次執行
    都留下 `sess_e2e_*` 資料列），使測試污染開發者的本機狀態，也讓平行執行
    的測試互相碰撞。

    現在比照 tests/test_api.py 的 `_IsolatedClientCase` 作法：每個測試類別
    取得自己的暫存 DB 與快照目錄，並在 tearDown **先**把全域 manager 還原到
    原本的位置，才刪除暫存目錄（否則後續測試類別會繼承一個已不存在的
    store_dir）。
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tf_test_transport_")
        self._orig_env = {
            k: os.environ.get(k)
            for k in ("TRADEFLOW_DB_PATH", "TRADEFLOW_STORE_DIR", "TRADEFLOW_WEBHOOK_TOKEN")
        }
        os.environ["TRADEFLOW_DB_PATH"] = os.path.join(self._tmp, "sessions.db")
        os.environ["TRADEFLOW_STORE_DIR"] = os.path.join(self._tmp, "sessions_json")
        # 保持這些測試撰寫時的 sandbox fail-open 語意。
        os.environ.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
        self._prev_paths = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
        api_main.session_mgr.__init__(
            db_path=os.environ["TRADEFLOW_DB_PATH"],
            store_dir=os.environ["TRADEFLOW_STORE_DIR"],
        )
        self.client = TestClient(app)

    def tearDown(self):
        from src.transport.webhook_hub import hub
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        api_main.session_mgr.__init__(
            db_path=self._prev_paths[0], store_dir=self._prev_paths[1]
        )
        hub._bridges.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_mobile_command_endpoint(self):
        """測試 POST /webhook/mobile-command 端點（bridge 解鎖應回報成功）。"""
        from src.transport.webhook_hub import hub
        hub.register_bridge("sess_api_test")

        # 1. 模擬 JSON 指令
        resp = self.client.post(
            "/webhook/mobile-command",
            json={"session_id": "sess_api_test", "command": "1"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # bridge 被解鎖本身就是效果，必須回報 success（見 w1 修正）。
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["resolved_command"], "CONCEDE")

    def test_session_and_round_processing(self):
        """測試端到端建立會話並發起一輪報價。"""
        import uuid
        s_id = f"sess_e2e_{uuid.uuid4().hex[:8]}"
        spec = {
            "session_id": s_id,
            "product_name": "auto umbrella",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "moq": 500,
            "expected_markup": 0.0,
        }
        res_create = self.client.post("/api/v1/sessions", json=spec)
        self.assertEqual(res_create.status_code, 200)
        self.assertEqual(res_create.json()["spec"]["session_id"], s_id)

        # 2. 推進第 1 輪 (對手回價 ¥13.50，在安全區間)
        round_payload = {
            "opponent_raw_text": "親，我們能做 ¥13.50",
            "opponent_offer_price": 13.50,
        }
        res_round = self.client.post(f"/api/v1/sessions/{s_id}/round", json=round_payload)
        self.assertEqual(res_round.status_code, 200)
        round_data = res_round.json()
        self.assertIn("autopilot_state", round_data)
        self.assertIn("recommended_offer_price", round_data)
        self.assertFalse(round_data["circuit_tripped"])


if __name__ == "__main__":
    unittest.main()
