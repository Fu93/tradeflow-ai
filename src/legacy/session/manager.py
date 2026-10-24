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
import logging
from typing import Optional, List, Dict
from datetime import datetime, timezone
from pydantic import ValidationError

logger = logging.getLogger("tradeflow.session")

from .schema import (
    NegotiationSession,
    SessionSpec,
    NegotiationTurn,
    AutopilotState,
)

# 測試/多實例隔離用的環境變數覆寫。
# [REGRESSION] SessionManager 舊版硬編 `data/sessions.db` 與 `data/sessions_json`，
# 因此任何並行測試或並行伺服器實例都會寫進同一個真實檔案，互相污染
# （審計實測：test_api.py / test_api_v4.py 會直接寫 repo 的 data/sessions.db）。
# 現在允許 TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR 覆寫，預設值保持不變。
DEFAULT_DB_PATH = os.getenv("TRADEFLOW_DB_PATH", "data/sessions.db")
DEFAULT_STORE_DIR = os.getenv("TRADEFLOW_STORE_DIR", "data/sessions_json")


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
        db_path: str = None,
        store_dir: str = None,
    ):
        # 明確傳入的參數優先；否則採用（可被環境變數覆寫的）模組預設值。
        # 讀取環境變數的時機放在 __init__ 而非 import 期，讓測試的 monkeypatch
        # 或行程中修改仍能生效。
        if db_path is None:
            db_path = os.getenv("TRADEFLOW_DB_PATH", DEFAULT_DB_PATH)
        if store_dir is None:
            store_dir = os.getenv("TRADEFLOW_STORE_DIR", DEFAULT_STORE_DIR)
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

            # [REGRESSION] 若資料庫中已存在一筆損壞的 spec_json（例如舊版寫入的
            # Infinity -> null 的 target_price），model_validate_json 會拋
            # ValidationError 讓每一次 GET /api/v1/sessions 都 500，而且重開
            # 資料庫依然持續損壞 —— 一個匿名請求即可永久打掛整條讀取路徑。
            # 現在把壞列視為「不可用」：記 log、跳過該列，絕不讓整個 LIST/GET
            # 端點因為一筆毒資料而崩潰。
            try:
                spec = SessionSpec.model_validate_json(row["spec_json"])
            except ValidationError as ve:
                logger.error(
                    "Skipping session '%s': its stored spec_json is not a valid SessionSpec "
                    "(%s). The row must be repaired or deleted; the row is excluded so the "
                    "rest of the session store stays readable.",
                    row["session_id"], ve,
                )
                return None
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
                # [P0 REGRESSION] 舊版只看 buyer_agent。人工核准寫入的輪次是
                # buyer_human_approved，於是 /approve 之後 latest_our_offer 變回
                # None，API/儀表板把「已核准的報價」顯示成 ¥0.00。
                if latest_our is None and t.sender in ("buyer_agent", "buyer_human_approved") \
                        and t.parsed_price is not None:
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

    def annotate_last_turn(
        self,
        session_id: str,
        extra: dict,
    ) -> bool:
        """合併額外欄位到該會話最新一則 turn 的 metadata。

        用途：copilot 模式的「引擎建議價」只有等 logrolling/AST 都跑完才
        知道，而對手輪次在更早之前就已 append。/approve 需要一個持久化、
        可稽核的建議價，否則只能退回 target_price（實測會核准錯數字）。

        這是**就地 merge**，不新增輪次、不改 current_round/version 的語義；
        回傳 False 代表沒有可標註的輪次。
        """
        if not isinstance(extra, dict) or not extra:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id, metadata_json FROM turns WHERE session_id = ? "
                "ORDER BY id DESC LIMIT 1;",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            meta.update(extra)
            conn.execute(
                "UPDATE turns SET metadata_json = ? WHERE id = ?;",
                (json.dumps(meta, ensure_ascii=False), row["id"]),
            )
        return True

    def append_turn(
        self,
        session_id: str,
        turn: NegotiationTurn,
        expected_version: Optional[int] = None,
    ) -> NegotiationSession:
        """追加一輪對話並以 CAS 原子遞增版本號。

        [REGRESSION] 舊版的 else 分支（expected_version 為 None 時）**完全沒有**
        版本謂詞，而 RoundActionRequest.expected_version 預設為 None 且 /round
        原樣帶下來，因此主要寫入路徑從不走受保護分支 —— 一個持舊快照的寫入者
        可以靜默把 current_round 由 5 降回 1。
        現在 always-CAS：未提供 expected_version 時，採用「剛讀到」的版本號作為
        預期值（仍然有謂詞），讓真正的並發競爭一定被偵測到。
        """
        session = self.get_session(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found.")

        now = datetime.now(timezone.utc)
        new_round = max(session.current_round, turn.round_index)
        # always-CAS：未指定時以讀到的版本為預期，仍然執行原子比對。
        expected = expected_version if expected_version is not None else session.version

        with self._get_connection() as conn:
            # 1. CAS 版本檢查（所有寫入路徑都帶版本謂詞）
            cursor = conn.execute(
                """
                UPDATE sessions 

                SET current_round = ?, version = version + 1, updated_at = ? 

                WHERE session_id = ? AND version = ?;
                """,
                (new_round, now.isoformat(), session_id, expected),
            )
            if cursor.rowcount == 0:
                raise OptimisticLockConflictError(
                    f"CAS optimistic lock conflict on session '{session_id}': "
                    f"expected version {expected}, but the record was modified concurrently "
                    f"or does not exist."
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
        # [REGRESSION] 舊版為 `assert updated_session is not None`：在 python -O 下
        # 會被移除，寫入失敗將靜默回傳 None。改為明確拋錯。
        if updated_session is None:
            raise RuntimeError(
                f"Persistence invariant violated: session '{session_id}' vanished after a "
                "successful turn append."
            )

        # [P0 FIX — Dashboard Decision panel requires these fields]
        # GET /api/v1/sessions returns NegotiationSession with latest_*_offer.
        # But append_turn never touched those — they stayed None for every round.
        # So the dashboard's "Engine Decision" panel has nothing to show after refresh.
        # Fix: persist last offer from each side based on the turn sender.
        new_latest_opp = turn.parsed_price if turn.sender == "opponent" else updated_session.latest_opponent_offer
        new_latest_our = (
            (turn.parsed_price if turn.sender.startswith("buyer") else None)
            if updated_session.latest_our_offer is None
            else updated_session.latest_our_offer
        )
        if new_latest_opp != updated_session.latest_opponent_offer or new_latest_our != updated_session.latest_our_offer:
            updated_session = updated_session.model_copy(update={
                "latest_opponent_offer": new_latest_opp,
                "latest_our_offer": new_latest_our,
            })
            self._atomic_write_json(updated_session)
            return updated_session

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

        # [REGRESSION] 舊版 `target_version = expected_version if ... else session.version`
        # 是對的，但問題出在呼叫端：manager.py:362 這個三元式在 expected_version 為
        # None 時會取「剛讀到的」版本，而真正持舊版號的呼叫端會被拿來與**剛讀到**
        # 的版本比較 —— 於是合法的舊呼叫端得到 FALSE conflict，而省略版號的呼叫端
        # 反而完全不做版本檢查。現在明確區分：
        #   - expected_version 有給：以它為 CAS 謂詞（真正的樂觀鎖）。
        #   - 未給：以讀到的版本為謂詞（仍保證原子，但不宣稱 caller 持有該版本）。
        target_version = expected_version if expected_version is not None else session.version

        now = datetime.now(timezone.utc)
        # 狀態轉移同樣一律帶版本謂詞（與 append_turn 一致），避免未指定版號時
        # 出現無保護的寫入窗口。
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
        # [REGRESSION] 舊版用 `assert updated_session is not None`。assert 在
        # `python -O` 之下會被移除，於是「寫入失敗」會靜默回傳 None 而不是報錯。
        # 持久化路徑不該用 assert 做控制流。
        if updated_session is None:
            raise RuntimeError(
                f"Persistence invariant violated: session '{session_id}' vanished after a "
                "successful state transition."
            )
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

    def _snapshot_path(self, session_id: str) -> str:
        """解析 session 快照路徑，並確保它落在 store_dir 內。

        [REGRESSION] 舊版直接 `os.path.join(self.store_dir, f"{session_id}.json")`，
        而 session_id 是呼叫端自由提供的字串：
          - `../../models/vocab` 會逃出 store_dir（路徑穿越）；
          - `C:/Users/.../pwn` 會讓 os.path.join **完全丟棄** store_dir，接著
            os.replace 原子覆寫該絕對路徑（可達 seed_data/price_anchor.json，
            而它餵給 demo_data 的目標價與品名）。
        白名單（schema.SAFE_SESSION_ID_RE）+ 解析後必須落在 store_dir 內，雙重防禦。
        """
        from .schema import SAFE_SESSION_ID_RE

        sid = str(session_id)
        if not SAFE_SESSION_ID_RE.fullmatch(sid):
            raise ValueError(
                f"Refusing to write a session snapshot for unsafe session_id {sid!r}: "
                "it must match ^[A-Za-z0-9._-]{1,128}$ (no path separators, no '..')."
            )
        store_root = os.path.realpath(self.store_dir)
        candidate = os.path.join(store_root, f"{sid}.json")
        target = os.path.realpath(candidate)
        # 雙重檢查：即使白名單在未來被放寬，這裡仍確保路徑不會落在 store_dir 之外。
        # 用 normcase 比較，避免 Windows 大小寫/正斜線差異造成誤判。
        if os.path.normcase(os.path.dirname(target)) != os.path.normcase(store_root):
            raise ValueError(
                f"Refusing to write a session snapshot outside the store directory "
                f"({target!r} is not directly inside {store_root!r})."
            )
        return target

    def _atomic_write_json(self, session: NegotiationSession, max_retries: int = 5) -> str:
        """跨平台安全的原子寫入快照，防範 Windows 鎖檔。"""
        target_path = self._snapshot_path(session.spec.session_id)
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
