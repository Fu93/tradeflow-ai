"""TradeFlow v5 — 談判會話持久化、CAS 樂觀鎖與狀態機管理器。

職責：
1. 雙層持久化：SQLite (WAL 模式) 記錄歷史輪次 + 跨平台原子 JSON 寫入最新快照。
2. 狀態機轉移守衛：嚴格校驗狀態合法性，防止非法狀態覆蓋。
3. CAS (Compare-And-Swap) 樂觀鎖：引入 version 欄位，徹底杜絕多並發協程/線程競態髒寫。
4. Windows 檔案鎖相容：使用同磁區臨時檔 + 指數退避重試置換。
"""
from __future__ import annotations
import os
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from .schema import (
    NegotiationSession,
    SessionSpec,
    NegotiationTurn,
    AutopilotState,
    RemoteAuthCommand,
)


class InvalidStateTransitionError(Exception):
    """當嘗試進行非法狀態機轉移時拋出此異常。"""
    pass


class OptimisticLockConflictError(Exception):
    """當 CAS 樂觀鎖版本或狀態不匹配時拋出此異常，表明存在並發寫衝突。"""
    pass


# 合法狀態轉移圖 (State Transition Graph)
VALID_TRANSITIONS: Dict[AutopilotState, List[AutopilotState]] = {
    AutopilotState.IDLE: [
        AutopilotState.AUTOPILOT_CRUISING,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.WALK_AWAY,
        AutopilotState.COPILOT_MONITORING,
        AutopilotState.AWAITING_HUMAN_DECISION,
    ],
    AutopilotState.AUTOPILOT_CRUISING: [
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
        AutopilotState.COPILOT_MONITORING,
        AutopilotState.AWAITING_HUMAN_DECISION,
    ],
    AutopilotState.CIRCUIT_TRIPPED_ESCALATED: [
        AutopilotState.AWAITING_REMOTE_AUTH,
        AutopilotState.AUTOPILOT_CRUISING,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
        AutopilotState.AWAITING_HUMAN_DECISION,
    ],
    AutopilotState.AWAITING_REMOTE_AUTH: [
        AutopilotState.AUTOPILOT_CRUISING,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
        AutopilotState.AWAITING_HUMAN_DECISION,
    ],
    # v5 採購副駕駛 (Copilot) 狀態流轉
    # [修正] 熔斷 (CIRCUIT_TRIPPED_ESCALATED) 是安全機制，必須能從**任何非終止
    # 狀態**觸發。舊版未把它列入 COPILOT_MONITORING 的允許轉移，導致 copilot
    # 模式下只要觸發熔斷就會拋 InvalidStateTransitionError 並讓 /round 回 HTTP 500
    # —— 也就是說，安全機制在最需要它的路徑上直接崩潰。
    AutopilotState.COPILOT_MONITORING: [
        AutopilotState.DISCREPANCY_ALERT,
        AutopilotState.AWAITING_HUMAN_DECISION,
        AutopilotState.AUTOPILOT_CRUISING,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
    ],
    AutopilotState.DISCREPANCY_ALERT: [
        AutopilotState.AWAITING_HUMAN_DECISION,
        AutopilotState.COPILOT_MONITORING,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.WALK_AWAY,
    ],
    AutopilotState.AWAITING_HUMAN_DECISION: [
        AutopilotState.HUMAN_APPROVED_APPLIED,
        AutopilotState.COPILOT_MONITORING,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
    ],
    AutopilotState.HUMAN_APPROVED_APPLIED: [
        AutopilotState.COPILOT_MONITORING,
        AutopilotState.AWAITING_HUMAN_DECISION,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.DEAL_CLOSED,
        AutopilotState.WALK_AWAY,
    ],
    # 終止狀態 (Terminal States) 不得轉移至任何其他狀態
    AutopilotState.DEAL_CLOSED: [],
    AutopilotState.WALK_AWAY: [],
}


class SessionManager:
    """談判會話管理器（SQLite WAL + CAS 樂觀鎖 + 原子 JSON 雙寫）。"""

    def __init__(
        self,
        db_path: str = "data/sessions.db",
        store_dir: str = "data/sessions_json",
    ):
        self.db_path = os.path.abspath(db_path)
        self.store_dir = os.path.abspath(store_dir)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.store_dir, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    ceiling_price REAL NOT NULL,
                    state TEXT NOT NULL,
                    current_round INTEGER NOT NULL DEFAULT 0,
                    trip_reason TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    spec_json TEXT NOT NULL
                );
                """
            )
            # 自動遷移：若既有資料庫缺少 version 欄位則動態擴展
            cursor = conn.execute("PRAGMA table_info(sessions);")
            cols = [row["name"] for row in cursor.fetchall()]
            if "version" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 1;")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    parsed_price REAL,
                    p_break REAL,
                    posterior_mean REAL,
                    posterior_std REAL,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                );
                """
            )

    def create_session(self, spec: SessionSpec) -> NegotiationSession:
        now = datetime.now(timezone.utc)
        session = NegotiationSession(
            spec=spec,
            state=AutopilotState.IDLE,
            history=[],
            current_round=0,
            latest_opponent_offer=None,
            latest_our_offer=None,
            trip_reason=None,
            version=1,
            created_at=now,
            updated_at=now,
        )

        with self._get_connection() as conn:
            conn.execute("DELETE FROM turns WHERE session_id = ?;", (session.spec.session_id,))
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    session_id, product_name, target_price, ceiling_price,
                    state, current_round, trip_reason, version, created_at, updated_at, spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session.spec.session_id,
                    session.spec.product_name,
                    session.spec.target_price,
                    session.spec.ceiling_price,
                    session.state.value,
                    session.current_round,
                    session.trip_reason,
                    session.version,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    session.spec.model_dump_json(),
                ),
            )

        self._atomic_write_json(session)
        return session

    def get_session(self, session_id: str) -> Optional[NegotiationSession]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?;", (session_id,)
            ).fetchone()
            if not row:
                return None

            spec = SessionSpec.model_validate_json(row["spec_json"])
            turn_rows = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY id ASC;",
                (session_id,),
            ).fetchall()

            history: List[NegotiationTurn] = []
            for tr in turn_rows:
                meta = json.loads(tr["metadata_json"])
                history.append(
                    NegotiationTurn(
                        round_index=tr["round_index"],
                        sender=tr["sender"],
                        raw_text=tr["raw_text"],
                        parsed_price=tr["parsed_price"],
                        timestamp=datetime.fromisoformat(tr["timestamp"]),
                        p_break=tr["p_break"],
                        posterior_mean=tr["posterior_mean"],
                        posterior_std=tr["posterior_std"],
                        metadata=meta,
                    )
                )

            # 找出最新報價
            latest_opp = None
            latest_our = None
            for t in reversed(history):
                if latest_opp is None and t.sender == "opponent" and t.parsed_price is not None:
                    latest_opp = t.parsed_price
                if latest_our is None and t.sender == "buyer_agent" and t.parsed_price is not None:
                    latest_our = t.parsed_price

            ver = row["version"] if "version" in row.keys() else 1

            return NegotiationSession(
                spec=spec,
                state=AutopilotState(row["state"]),
                history=history,
                current_round=row["current_round"],
                latest_opponent_offer=latest_opp,
                latest_our_offer=latest_our,
                trip_reason=row["trip_reason"],
                version=ver,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def append_turn(
        self,
        session_id: str,
        turn: NegotiationTurn,
        expected_version: Optional[int] = None,
    ) -> NegotiationSession:
        session = self.get_session(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")

        now = datetime.now(timezone.utc)
        new_round = max(session.current_round, turn.round_index)

        with self._get_connection() as conn:
            # 1. 若指定 expected_version，先以 CAS 檢查版本號
            if expected_version is not None:
                cursor = conn.execute(
                    """
                    UPDATE sessions 
                    SET current_round = ?, version = version + 1, updated_at = ? 
                    WHERE session_id = ? AND version = ?;
                    """,
                    (new_round, now.isoformat(), session_id, expected_version),
                )
                if cursor.rowcount == 0:
                    raise OptimisticLockConflictError(
                        f"CAS optimistic lock conflict on session '{session_id}': "
                        f"expected version {expected_version}, but record was modified concurrently."
                    )
            else:
                conn.execute(
                    """
                    UPDATE sessions 
                    SET current_round = ?, version = version + 1, updated_at = ? 
                    WHERE session_id = ?;
                    """,
                    (new_round, now.isoformat(), session_id),
                )

            # 2. 插入輪次紀錄
            conn.execute(
                """
                INSERT INTO turns (
                    session_id, round_index, sender, raw_text, parsed_price,
                    p_break, posterior_mean, posterior_std, timestamp, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session_id,
                    turn.round_index,
                    turn.sender,
                    turn.raw_text,
                    turn.parsed_price,
                    turn.p_break,
                    turn.posterior_mean,
                    turn.posterior_std,
                    turn.timestamp.isoformat(),
                    json.dumps(turn.metadata),
                ),
            )

        # 刷新快照
        updated_session = self.get_session(session_id)
        assert updated_session is not None
        self._atomic_write_json(updated_session)
        return updated_session

    def transition_state(
        self,
        session_id: str,
        new_state: AutopilotState,
        reason: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> NegotiationSession:
        """執行 CAS (Compare-And-Swap) 原子狀態流轉。

        Args:
            session_id: 會話識別碼
            new_state: 目標轉移狀態
            reason: 熔斷或狀態轉移事由
            expected_version: 樂觀鎖預期版本號。若未提供則使用當前讀取之版本。
        """
        session = self.get_session(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")

        current_state = session.state
        allowed_next = VALID_TRANSITIONS.get(current_state, [])
        if new_state not in allowed_next:
            raise InvalidStateTransitionError(
                f"Cannot transition session '{session_id}' from '{current_state.value}' to '{new_state.value}'. "
                f"Allowed transitions: {[s.value for s in allowed_next]}"
            )

        target_version = expected_version if expected_version is not None else session.version
        now = datetime.now(timezone.utc)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions 
                SET state = ?, trip_reason = ?, version = version + 1, updated_at = ? 
                WHERE session_id = ? AND state = ? AND version = ?;
                """,
                (new_state.value, reason, now.isoformat(), session_id, current_state.value, target_version),
            )
            if cursor.rowcount == 0:
                # 樂觀鎖衝突：版本號或當前狀態已被其他並發協程搶先修改
                raise OptimisticLockConflictError(
                    f"CAS optimistic lock conflict on session '{session_id}': "
                    f"expected state '{current_state.value}' with version {target_version}, "
                    f"but record was modified concurrently."
                )

        updated_session = self.get_session(session_id)
        assert updated_session is not None
        self._atomic_write_json(updated_session)
        return updated_session

    def list_sessions(self) -> List[NegotiationSession]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT session_id FROM sessions ORDER BY created_at DESC;").fetchall()
            result = []
            for r in rows:
                s = self.get_session(r["session_id"])
                if s:
                    result.append(s)
            return result

    def rebuild_json_snapshot(self, session_id: str) -> Optional[str]:
        """從唯一真相源 (SQLite WAL) 重建受損或遺失的 JSON 快照快取。
        
        解決跨系統雙寫非原子性：SQLite 為單一真相源 (SSOT)，JSON 為可重建的導出快照。
        """
        sess = self.get_session(session_id)
        if sess:
            return self._atomic_write_json(sess)
        return None

    def _atomic_write_json(self, session: NegotiationSession, max_retries: int = 5) -> str:
        """跨平台安全的原子寫入快照，防範 Windows 鎖檔。"""
        target_path = os.path.join(self.store_dir, f"{session.spec.session_id}.json")
        data_str = session.model_dump_json(indent=2)

        # 在相同目錄下建立暫存檔，確保 os.replace 屬於同一磁碟卷原子操作
        with tempfile.NamedTemporaryFile("w", dir=self.store_dir, delete=False, encoding="utf-8") as tf:
            tf.write(data_str)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name

        # 針對 Windows 防毒或高頻讀寫提供指數退避
        for attempt in range(max_retries):
            try:
                os.replace(temp_name, target_path)
                return target_path
            except PermissionError:
                time.sleep(0.015 * (2 ** attempt))

        # 若最終重試耗盡仍失敗，安全清理暫存檔並拋出例外
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except Exception:
                pass
        raise IOError(f"Failed to atomic replace {target_path} after {max_retries} attempts.")
