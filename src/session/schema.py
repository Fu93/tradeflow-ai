"""TradeFlow v4 — 談判會話與狀態機資料結構契約。

職責：定義談判會話 (Session)、出價輪次、熔斷狀態、以及人工授權指令之 Pydantic 資料契約。
"""
from __future__ import annotations
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class AutopilotState(str, Enum):
    IDLE = "IDLE"
    AUTOPILOT_CRUISING = "AUTOPILOT_CRUISING"
    CIRCUIT_TRIPPED_ESCALATED = "CIRCUIT_TRIPPED_ESCALATED"
    AWAITING_REMOTE_AUTH = "AWAITING_REMOTE_AUTH"
    DEAL_CLOSED = "DEAL_CLOSED"
    WALK_AWAY = "WALK_AWAY"


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


class SessionSpec(BaseModel):
    session_id: str
    product_name: str
    target_price: float
    ceiling_price: float
    moq: int = 500
    max_rounds: int = 5
    prior_reserve_mean: float = 1.08
    prior_reserve_std: float = 0.10
    supplier_id: str = "default_supplier"
    supplier_channel: str = "web_chat"                # "web_chat" | "email" | "manual"


class NegotiationSession(BaseModel):
    spec: SessionSpec
    state: AutopilotState = AutopilotState.IDLE
    history: List[NegotiationTurn] = Field(default_factory=list)
    current_round: int = 0
    latest_opponent_offer: Optional[float] = None
    latest_our_offer: Optional[float] = None
    trip_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
