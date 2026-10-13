"""TradeFlow v4 — 談判工作階段與狀態機模組。"""
from .schema import (
    AutopilotState,
    RemoteAuthCommand,
    CircuitBreakerTriggerReason,
    NegotiationTurn,
    SessionSpec,
    NegotiationSession,
)
from .manager import (
    SessionManager,
    InvalidStateTransitionError,
    VALID_TRANSITIONS,
)

__all__ = [
    "AutopilotState",
    "RemoteAuthCommand",
    "CircuitBreakerTriggerReason",
    "NegotiationTurn",
    "SessionSpec",
    "NegotiationSession",
    "SessionManager",
    "InvalidStateTransitionError",
    "VALID_TRANSITIONS",
]
