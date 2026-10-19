"""單元測試：Phase 1 狀態機 CAS (Compare-And-Swap) 樂觀鎖與版本控制防禦。

驗證目標：
1. 正常狀態流轉下 version 欄位單調原子自增 (1 -> 2 -> 3)。
2. append_turn 寫入輪次時同步原子自增版本號。
3. 模擬高並發競爭：當兩個工作協程持相同過期版本號同時提交變更時，後者必然觸發 OptimisticLockConflictError，杜絕髒寫覆蓋。
4. 驗證 v5 採購副駕駛 (Copilot) 全套合法狀態流轉鏈條。
"""
import os
import shutil
import tempfile
import unittest
from src.session import (
    SessionManager,
    SessionSpec,
    NegotiationTurn,
    AutopilotState,
    OptimisticLockConflictError,
    InvalidStateTransitionError,
)


class TestConcurrencyCAS(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="tradeflow_cas_test_")
        self.db_path = os.path.join(self.test_dir, "test_cas.db")
        self.store_dir = os.path.join(self.test_dir, "sessions_json")
        self.manager = SessionManager(db_path=self.db_path, store_dir=self.store_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cas_version_increment(self):
        """測試正常流轉下 version 單調遞增。"""
        spec = SessionSpec(
            session_id="cas_sess_001",
            product_name="precision part",
            target_price=50.0,
            ceiling_price=65.0,
            expected_markup=0.0,
        )
        s0 = self.manager.create_session(spec)
        self.assertEqual(s0.version, 1)

        # 第 1 次流轉：預期版本 1，流轉至 AUTOPILOT_CRUISING
        s1 = self.manager.transition_state(
            "cas_sess_001",
            AutopilotState.AUTOPILOT_CRUISING,
            expected_version=1,
        )
        self.assertEqual(s1.version, 2)
        self.assertEqual(s1.state, AutopilotState.AUTOPILOT_CRUISING)

        # 第 2 次流轉：預期版本 2，流轉至 CIRCUIT_TRIPPED_ESCALATED
        s2 = self.manager.transition_state(
            "cas_sess_001",
            AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
            reason="P_break high",
            expected_version=2,
        )
        self.assertEqual(s2.version, 3)
        self.assertEqual(s2.state, AutopilotState.CIRCUIT_TRIPPED_ESCALATED)

    def test_stale_version_conflict(self):
        """測試持過期版本提交時被 CAS 阻斷並拋出 OptimisticLockConflictError。"""
        spec = SessionSpec(
            session_id="cas_sess_002",
            product_name="precision part",
            target_price=50.0,
            ceiling_price=65.0,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # 傳入不存在的過期版本 99
        with self.assertRaises(OptimisticLockConflictError):
            self.manager.transition_state(
                "cas_sess_002",
                AutopilotState.AUTOPILOT_CRUISING,
                expected_version=99,
            )

        # 驗證資料庫中的狀態分毫未改
        cur = self.manager.get_session("cas_sess_002")
        self.assertIsNotNone(cur)
        self.assertEqual(cur.version, 1)
        self.assertEqual(cur.state, AutopilotState.IDLE)

    def test_append_turn_increments_version(self):
        """測試寫入對話輪次時同步提升版本號。"""
        spec = SessionSpec(
            session_id="cas_sess_003",
            product_name="auto umbrella",
            target_price=12.0,
            ceiling_price=16.0,
            expected_markup=0.0,
        )
        s0 = self.manager.create_session(spec)
        self.assertEqual(s0.version, 1)

        turn1 = NegotiationTurn(
            round_index=1,
            sender="opponent",
            raw_text="¥14.50",
            parsed_price=14.50,
        )
        s1 = self.manager.append_turn("cas_sess_003", turn1)
        self.assertEqual(s1.version, 2)

        turn2 = NegotiationTurn(
            round_index=1,
            sender="buyer_agent",
            raw_text="¥12.80",
            parsed_price=12.80,
        )
        s2 = self.manager.append_turn("cas_sess_003", turn2)
        self.assertEqual(s2.version, 3)

    def test_simulated_race_condition(self):
        """模擬兩個並發客戶端讀取同一會話 (version=1)，先後發起競爭狀態變更。"""
        spec = SessionSpec(
            session_id="cas_race_001",
            product_name="race test",
            target_price=20.0,
            ceiling_price=25.0,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # 協程 A 和 協程 B 同時獲取到當前狀態 (version=1, state=IDLE)
        worker_a_cached = self.manager.get_session("cas_race_001")
        worker_b_cached = self.manager.get_session("cas_race_001")
        self.assertEqual(worker_a_cached.version, 1)
        self.assertEqual(worker_b_cached.version, 1)

        # 協程 A 搶先一步成功提交流轉
        s_a = self.manager.transition_state(
            "cas_race_001",
            AutopilotState.AUTOPILOT_CRUISING,
            expected_version=worker_a_cached.version,
        )
        self.assertEqual(s_a.version, 2)
        self.assertEqual(s_a.state, AutopilotState.AUTOPILOT_CRUISING)

        # 協程 B 隨後嘗試用其過時的快照 (version=1) 進行流轉
        with self.assertRaises(OptimisticLockConflictError):
            self.manager.transition_state(
                "cas_race_001",
                AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
                expected_version=worker_b_cached.version,
            )

        # 驗證資料庫依然維持協程 A 變更的結果，協程 B 沒有引發髒寫覆蓋
        final_sess = self.manager.get_session("cas_race_001")
        self.assertEqual(final_sess.version, 2)
        self.assertEqual(final_sess.state, AutopilotState.AUTOPILOT_CRUISING)

    def test_copilot_state_flow(self):
        """驗證 v5 採購副駕駛 (Copilot) 完整狀態流轉鏈條。"""
        spec = SessionSpec(
            session_id="copilot_sess_001",
            product_name="smart copilot part",
            target_price=100.0,
            ceiling_price=120.0,
            expected_markup=0.0,
        )
        self.manager.create_session(spec)

        # 1. IDLE -> COPILOT_MONITORING
        s1 = self.manager.transition_state("copilot_sess_001", AutopilotState.COPILOT_MONITORING)
        self.assertEqual(s1.state, AutopilotState.COPILOT_MONITORING)

        # 2. COPILOT_MONITORING -> DISCREPANCY_ALERT (觸發異常告警)
        s2 = self.manager.transition_state(
            "copilot_sess_001",
            AutopilotState.DISCREPANCY_ALERT,
            reason="原料溢價超過30%異常",
        )
        self.assertEqual(s2.state, AutopilotState.DISCREPANCY_ALERT)
        self.assertEqual(s2.trip_reason, "原料溢價超過30%異常")

        # 3. DISCREPANCY_ALERT -> AWAITING_HUMAN_DECISION (呈現決策卡)
        s3 = self.manager.transition_state("copilot_sess_001", AutopilotState.AWAITING_HUMAN_DECISION)
        self.assertEqual(s3.state, AutopilotState.AWAITING_HUMAN_DECISION)

        # 4. AWAITING_HUMAN_DECISION -> HUMAN_APPROVED_APPLIED (採購員確認採納話術)
        s4 = self.manager.transition_state("copilot_sess_001", AutopilotState.HUMAN_APPROVED_APPLIED)
        self.assertEqual(s4.state, AutopilotState.HUMAN_APPROVED_APPLIED)

        # 5. HUMAN_APPROVED_APPLIED -> DEAL_CLOSED (終止成交)
        s5 = self.manager.transition_state("copilot_sess_001", AutopilotState.DEAL_CLOSED)
        self.assertEqual(s5.state, AutopilotState.DEAL_CLOSED)

    def test_100_sessions_concurrent_stress(self):
        """Phase 4 驗收標準：100 會話多線程高並發壓測。

        驗證指標：
        1. 100 個獨立會話在 16 個並發線程下密集進行狀態流轉與輪次寫入。
        2. SQLite WAL 模式下零 SQLITE_BUSY (database is locked) 崩潰。
        3. 所有會話最終版本號與輪次數據 100% 保持一致，零數據丟失或損壞。
        """
        import concurrent.futures

        n_sessions = 100
        # 1. 預置 100 個會話
        for i in range(n_sessions):
            spec = SessionSpec(
                session_id=f"stress_sess_{i:03d}",
                product_name=f"Stress Product {i}",
                target_price=10.0 + i,
                ceiling_price=15.0 + i,
            expected_markup=0.0,
        )
            self.manager.create_session(spec)

        def worker_task(session_idx: int) -> dict:
            sid = f"stress_sess_{session_idx:03d}"
            # 狀態流轉: IDLE -> COPILOT_MONITORING
            s1 = self.manager.transition_state(sid, AutopilotState.COPILOT_MONITORING)
            # 寫入第 1 輪 (opponent)
            t1 = NegotiationTurn(
                round_index=1,
                sender="opponent",
                raw_text=f"¥{14.0 + session_idx:.2f}",
                parsed_price=14.0 + session_idx,
            )
            s2 = self.manager.append_turn(sid, t1)
            # 狀態流轉: COPILOT_MONITORING -> AWAITING_HUMAN_DECISION
            s3 = self.manager.transition_state(sid, AutopilotState.AWAITING_HUMAN_DECISION)
            # 寫入第 1 輪 (buyer)
            t2 = NegotiationTurn(
                round_index=1,
                sender="buyer_human_approved",
                raw_text=f"¥{11.5 + session_idx:.2f}",
                parsed_price=11.5 + session_idx,
            )
            s4 = self.manager.append_turn(sid, t2)
            # 狀態流轉: AWAITING_HUMAN_DECISION -> HUMAN_APPROVED_APPLIED -> COPILOT_MONITORING
            s5 = self.manager.transition_state(sid, AutopilotState.HUMAN_APPROVED_APPLIED)
            s6 = self.manager.transition_state(sid, AutopilotState.COPILOT_MONITORING)
            return {"session_id": sid, "final_version": s6.version, "turn_count": len(s6.history)}

        results = []
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, i) for i in range(n_sessions)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as exc:
                    errors.append(exc)

        # 斷言：100 個會話全部成功，0 個 SQLite 鎖定或超時崩潰
        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        self.assertEqual(len(results), n_sessions)

        # 檢驗全量會話資料完整性
        for r in results:
            self.assertEqual(r["turn_count"], 2)
            # 初始版本 1 + 6 次狀態/輪次寫入 = 版本 7
            self.assertEqual(r["final_version"], 7)

        # 驗證全量列表可完整無損檢索
        all_sessions = self.manager.list_sessions()
        self.assertEqual(len(all_sessions), n_sessions)


if __name__ == "__main__":
    unittest.main()
