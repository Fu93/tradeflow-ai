"""TradeFlow v4 — 雙向遠端指令協調中樞 (Webhook Hub)。

職責：
1. 異步事件橋接：為處於熔斷（CIRCUIT_TRIPPED_ESCALATED）的談判會話建立 `asyncio.Event` 掛起鎖。
2. 指令解碼與派發：接收手機端（WhatsApp / Telegram / 微信）發送的指令（1=妥協, 2=堅持, 3=撤退 或 自訂數字）。
3. 瞬間解鎖喚醒：派發指令並喚醒瀏覽器自駕代理恢復執行。
"""
from __future__ import annotations
import asyncio
from typing import Dict, Optional, Tuple
from ..legacy.session.schema import RemoteAuthCommand


class SessionExecutionBridge:
    """單一會話的異步執行與授權橋樑。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.auth_event: asyncio.Event = asyncio.Event()
        self.resolved_command: Optional[RemoteAuthCommand] = None
        self.custom_price: Optional[float] = None

    def unlock(self, command: RemoteAuthCommand, custom_price: Optional[float] = None) -> None:
        self.resolved_command = command
        self.custom_price = custom_price
        self.auth_event.set()


class WebhookHub:
    """全域 Webhook 雙向指令排程器（單例/中樞）。

    [REGRESSION] `_bridges` 舊版永不清理：只有 `wait_for_auth` 的 finally 會
    unregister，而 dispatch_command 路徑不會，因此長期執行下是無界記憶體成長。
    現在加上容量上限（超過時淘汰最舊的未授權橋樑）。
    """

    # register_bridge 併發存在上限（防止未清理的橋樑無界累積）。
    MAX_BRIDGES = 256

    def __init__(self):
        self._bridges: Dict[str, SessionExecutionBridge] = {}

    def register_bridge(self, session_id: str) -> SessionExecutionBridge:
        if session_id not in self._bridges and len(self._bridges) >= self.MAX_BRIDGES:
            # 淘汰最舊的一筆（dict 保序），避免無界成長。
            oldest = next(iter(self._bridges), None)
            if oldest is not None:
                self._bridges.pop(oldest, None)
        bridge = SessionExecutionBridge(session_id=session_id)
        self._bridges[session_id] = bridge
        return bridge

    def get_bridge(self, session_id: str) -> Optional[SessionExecutionBridge]:
        return self._bridges.get(session_id)

    def unregister_bridge(self, session_id: str) -> None:
        self._bridges.pop(session_id, None)

    def dispatch_command(self, session_id: str, raw_command: str) -> Tuple[RemoteAuthCommand, Optional[float]]:
        """解析手機端回傳之文字/按鈕指令並解鎖對應橋樑。"""
        from ..engine.bilingual import EnglishExecutiveCommandParser, ExecutiveActionEnum
        parsed = EnglishExecutiveCommandParser.parse(raw_command)
        custom_price: Optional[float] = parsed.override_price

        if parsed.action == ExecutiveActionEnum.CONCEDE:
            resolved = RemoteAuthCommand.CONCEDE
        elif parsed.action == ExecutiveActionEnum.HOLD_FIRM:
            resolved = RemoteAuthCommand.HOLD_FIRM
        elif parsed.action == ExecutiveActionEnum.WALK_AWAY:
            resolved = RemoteAuthCommand.WALK_AWAY
        elif parsed.action in (ExecutiveActionEnum.OVERRIDE_PRICE, ExecutiveActionEnum.CUSTOM_TACTIC):
            resolved = RemoteAuthCommand.OVERRIDE_PRICE
        else:
            resolved = RemoteAuthCommand.HOLD_FIRM

        bridge = self.get_bridge(session_id)
        if bridge:
            bridge.unlock(resolved, custom_price)

        return resolved, custom_price

    async def wait_for_auth(
        self, session_id: str, timeout_sec: float = 300.0
    ) -> Tuple[RemoteAuthCommand, Optional[float]]:
        """異步等待主管手機授權指令，超時則預設安全堅持 (HOLD_FIRM)。"""
        bridge = self.get_bridge(session_id)
        if not bridge:
            bridge = self.register_bridge(session_id)

        try:
            await asyncio.wait_for(bridge.auth_event.wait(), timeout=timeout_sec)
            # [REGRESSION] 舊版此處用 `assert bridge.resolved_command is not None`。
            # assert 在 `python -O` 下會被移除，授權路徑就會靜默回傳 None，這是
            # 控制流用 assert 的典型危害。改為明確拋錯。
            if bridge.resolved_command is None:
                raise RuntimeError(
                    "Authorization invariant violated: bridge event was set without a "
                    "resolved command."
                )
            return bridge.resolved_command, bridge.custom_price
        except asyncio.TimeoutError:
            # 超時安全降級守衛：維持底線，不自作主張妥協
            bridge.unlock(RemoteAuthCommand.HOLD_FIRM, None)
            return RemoteAuthCommand.HOLD_FIRM, None
        finally:
            self.unregister_bridge(session_id)


# 全域單例
hub = WebhookHub()
