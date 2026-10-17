"""TradeFlow v5 — 談判會話與狀態機資料結構契約。

職責：定義談判會話 (Session)、出價輪次、熔斷狀態、副駕駛狀態、以及人工授權指令之 Pydantic 資料契約。
"""
from __future__ import annotations
import re
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator

from src.config.heuristics import PRIOR_RESERVE_MEAN, PRIOR_RESERVE_STD, SILENCE_CONCESSION_RATIO


# 會話識別碼白名單 (Session id whitelist)。
# session_id 由呼叫端自由提供，且被 manager._atomic_write_json 直接拼進檔案路徑
# （store_dir/{session_id}.json）。若不限制字元集，`../../models/vocab` 會逃出
# store_dir，`C:/.../pwn` 更會讓 os.path.join 直接丟棄 store_dir，然後 os.replace
# 原子覆寫該路徑（可達 seed_data/price_anchor.json）。因此在此以白名單驗證；
# manager 另有一層「解析後必須落在 store_dir 內」的防禦。
SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class AutopilotState(str, Enum):
    # 基礎與自駕狀態
    IDLE = "IDLE"
    AUTOPILOT_CRUISING = "AUTOPILOT_CRUISING"
    CIRCUIT_TRIPPED_ESCALATED = "CIRCUIT_TRIPPED_ESCALATED"
    AWAITING_REMOTE_AUTH = "AWAITING_REMOTE_AUTH"
    DEAL_CLOSED = "DEAL_CLOSED"
    WALK_AWAY = "WALK_AWAY"

    # v5 採購副駕駛 (Copilot) 狀態
    COPILOT_MONITORING = "COPILOT_MONITORING"
    DISCREPANCY_ALERT = "DISCREPANCY_ALERT"
    AWAITING_HUMAN_DECISION = "AWAITING_HUMAN_DECISION"
    HUMAN_APPROVED_APPLIED = "HUMAN_APPROVED_APPLIED"


class RemoteAuthCommand(str, Enum):
    CONCEDE = "CONCEDE"                # 指令 1: 妥協讓步
    HOLD_FIRM = "HOLD_FIRM"            # 指令 2: 堅持底線不讓步
    WALK_AWAY = "WALK_AWAY"            # 指令 3: 立即破局撤退
    OVERRIDE_PRICE = "OVERRIDE_PRICE"  # 自訂覆寫報價


class CircuitBreakerTriggerReason(str, Enum):
    HIGH_BREAKDOWN_PROB = "HIGH_BREAKDOWN_PROB"       # P_break >= 15%
    CEILING_BREACH = "CEILING_BREACH"                 # 對手出價 > 1.35 * Target
    CONCESSION_STAGNATION = "CONCESSION_STAGNATION"   # 連續兩輪無讓步
    ANOMALOUS_VOLATILITY = "ANOMALOUS_VOLATILITY"     # 跳價速率異常


class NegotiationTurn(BaseModel):
    round_index: int
    sender: str                                       # "opponent" | "buyer_agent" | "human"
    raw_text: str
    parsed_price: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    p_break: Optional[float] = None
    posterior_mean: Optional[float] = None
    posterior_std: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    logrolling_proposal: Optional[Dict[str, Any]] = None


class SessionSpec(BaseModel):
    # [REGRESSION] 數值欄位原本毫無界限，且未關閉非有限浮點。Pydantic 接受非標準
    # JSON 字面量 `Infinity`；`model_dump_json()` 再把它序列化成 `null`，於是
    # SQLite 的 spec_json 存進 target_price:null，之後每一次讀取
    # （manager.get_session 的 model_validate_json）都拋 ValidationError -> 500，
    # 且重開資料庫仍持續損壞。任何匿名呼叫端都能用一個便宜請求永久打掛
    # GET /api/v1/sessions。故所有數值欄位加上有限性與正/範圍界限。
    session_id: str
    product_name: str = Field(..., min_length=1, max_length=512)
    target_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    ceiling_price: float = Field(..., gt=0.0, allow_inf_nan=False)
    moq: int = Field(default=500, ge=1, le=10_000_000)
    max_rounds: int = Field(default=5, ge=1, le=100)
    prior_reserve_mean: float = Field(default=PRIOR_RESERVE_MEAN, gt=0.0, allow_inf_nan=False)
    prior_reserve_std: float = Field(default=PRIOR_RESERVE_STD, gt=0.0, allow_inf_nan=False)
    supplier_id: str = Field(default="default_supplier", max_length=256)
    supplier_channel: str = Field(default="web_chat", max_length=64)
    corporate_mandate: Optional[Dict[str, Any]] = None

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str) -> str:
        """拒絕可逃出 store_dir 或作為絕對路徑使用的 session_id。"""
        if not isinstance(v, str) or not SAFE_SESSION_ID_RE.fullmatch(v):
            raise ValueError(
                "session_id must match ^[A-Za-z0-9._-]{1,128}$ "
                "(path separators, drive prefixes and '..' are not allowed)"
            )
        return v


class NegotiationSession(BaseModel):
    spec: SessionSpec
    state: AutopilotState = AutopilotState.IDLE
    history: List[NegotiationTurn] = Field(default_factory=list)
    current_round: int = 0
    latest_opponent_offer: Optional[float] = None
    latest_our_offer: Optional[float] = None
    trip_reason: Optional[str] = None
    silence_until_ts: Optional[float] = Field(default=None, description="該供應商獨立戰略沉默截止時間戳 (Epoch Seconds)")
    version: int = Field(default=1, description="CAS 樂觀鎖版本號，防止高並發競態與髒寫覆蓋")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def check_break_silence(self, new_offer: Optional[float], concession_made: bool = False) -> bool:
        """
        沉默狀態機即時破冰喚醒 (Break Silence Trigger):
        當對手主動降價 >= 3% (低於上次報價之 97%) 或給出交期/定金等實質讓步時，
        瞬間解除沉默，恢復活躍博弈跟進。
        """
        if self.silence_until_ts is None:
            return False

        if concession_made:
            self.silence_until_ts = None
            return True

        if new_offer is not None and self.latest_opponent_offer is not None:
            # 降價幅度 >= 3%
            if new_offer <= self.latest_opponent_offer * SILENCE_CONCESSION_RATIO:
                self.silence_until_ts = None
                return True

        return False
