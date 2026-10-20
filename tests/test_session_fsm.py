"""單元測試：Phase 1 談判工作階段狀態機 (FSM) 與原子雙寫持久化。"""
import os
import sys
import tempfile
import shutil
import unittest
from datetime import datetime

# 導入待測模組
from src.session import (
    SessionManager,
    SessionSpec,
    NegotiationTurn,
    AutopilotState,
    InvalidStateTransitionError,
)


class TestSessionFSM(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tradeflow_test_")
        self.db_path = os.path.join(self.test_dir, "test_sessions.db")
        self.store_dir = os.path.join(self.test_dir, "sessions_json")
        self.manager = SessionManager(db_path=self.db_path, store_dir=self.store_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_creation(self):
        spec = SessionSpec(
            session_id="sess_001",
            product_name="auto umbrella",
            target_price=12.0,
            ceiling_price=16.2,
            moq=500,
            expected_markup=0.0,
        )
        session = self.manager.create_session(spec)

        self.assertEqual(session.spec.session_id, "sess_001")
        self.assertEqual(session.state, AutopilotState.IDLE)
        self.assertEqual(session.current_round, 0)

        # 驗證 SQLite 寫入
        retrieved = self.manager.get_session("sess_001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.spec.product_name, "auto umbrella")

        # 驗證原子 JSON 檔案存在且解析正確
        json_file = os.path.join(self.store_dir, "sess_001.json")
        self.assertTrue(os.path.exists(json_file))
        with open(json_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("sess_001", content)
            self.assertIn("auto umbrella", content)

    def test_append_turns_and_latest_offer(self):
        spec = SessionSpec(
            session_id="sess_002",
            product_name="auto umbrella",
            target_price=12.0,
            ceiling_price=16.2,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # 1. 供應商回價
        turn1 = NegotiationTurn(
            round_index=1,
            sender="opponent",
            raw_text="親，最低 ¥14.80/把",
            parsed_price=14.80,
            p_break=0.08,
            posterior_mean=1.12,
            posterior_std=0.09,
        )
        s1 = self.manager.append_turn("sess_002", turn1)
        self.assertEqual(s1.current_round, 1)
        self.assertEqual(s1.latest_opponent_offer, 14.80)
        self.assertIsNone(s1.latest_our_offer)

        # 2. 我方自動還價
        turn2 = NegotiationTurn(
            round_index=1,
            sender="buyer_agent",
            raw_text="現款現結，首輪意向價 ¥12.30",
            parsed_price=12.30,
            p_break=0.08,
        )
        s2 = self.manager.append_turn("sess_002", turn2)
        self.assertEqual(s2.latest_opponent_offer, 14.80)
        self.assertEqual(s2.latest_our_offer, 12.30)
        self.assertEqual(len(s2.history), 2)

    def test_valid_state_transitions(self):
        spec = SessionSpec(
            session_id="sess_003",
            product_name="test product",
            target_price=10.0,
            ceiling_price=13.5,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # IDLE -> AUTOPILOT_CRUISING
        s1 = self.manager.transition_state("sess_003", AutopilotState.AUTOPILOT_CRUISING)
        self.assertEqual(s1.state, AutopilotState.AUTOPILOT_CRUISING)

        # AUTOPILOT_CRUISING -> CIRCUIT_TRIPPED_ESCALATED
        s2 = self.manager.transition_state(
            "sess_003",
            AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
            reason="P_break >= 15%",
        )
        self.assertEqual(s2.state, AutopilotState.CIRCUIT_TRIPPED_ESCALATED)
        self.assertEqual(s2.trip_reason, "P_break >= 15%")

        # CIRCUIT_TRIPPED_ESCALATED -> AWAITING_REMOTE_AUTH
        s3 = self.manager.transition_state("sess_003", AutopilotState.AWAITING_REMOTE_AUTH)
        self.assertEqual(s3.state, AutopilotState.AWAITING_REMOTE_AUTH)

        # AWAITING_REMOTE_AUTH -> AUTOPILOT_CRUISING (人工手機授權解除)
        s4 = self.manager.transition_state("sess_003", AutopilotState.AUTOPILOT_CRUISING)
        self.assertEqual(s4.state, AutopilotState.AUTOPILOT_CRUISING)

        # AUTOPILOT_CRUISING -> DEAL_CLOSED
        s5 = self.manager.transition_state("sess_003", AutopilotState.DEAL_CLOSED)
        self.assertEqual(s5.state, AutopilotState.DEAL_CLOSED)

    def test_invalid_state_transition_guards(self):
        spec = SessionSpec(
            session_id="sess_004",
            product_name="guard test",
            target_price=10.0,
            ceiling_price=13.5,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # IDLE 不能直接跳到 DEAL_CLOSED
        with self.assertRaises(InvalidStateTransitionError):
            self.manager.transition_state("sess_004", AutopilotState.DEAL_CLOSED)

        # 推進至 DEAL_CLOSED (終止狀態)
        self.manager.transition_state("sess_004", AutopilotState.AUTOPILOT_CRUISING)
        self.manager.transition_state("sess_004", AutopilotState.DEAL_CLOSED)

        # 終止狀態嚴禁轉移至任何狀態
        with self.assertRaises(InvalidStateTransitionError):
            self.manager.transition_state("sess_004", AutopilotState.AUTOPILOT_CRUISING)

    def test_list_sessions(self):
        spec1 = SessionSpec(session_id="s_a", product_name="A", target_price=10.0, ceiling_price=13.0,
            expected_markup=0.0,
        )
        spec2 = SessionSpec(session_id="s_b", product_name="B", target_price=20.0, ceiling_price=26.0,
            expected_markup=0.0,
        )
        self.manager.create_session(spec1)
        self.manager.create_session(spec2)

        sessions = self.manager.list_sessions()
        self.assertEqual(len(sessions), 2)
        session_ids = [s.spec.session_id for s in sessions]
        self.assertIn("s_a", session_ids)
        self.assertIn("s_b", session_ids)


if __name__ == "__main__":
    unittest.main()
