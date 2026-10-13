"""TradeFlow v4 — 雙向遠端指令協調中樞 (Webhook Hub)。

職責：
1. 異步事件橋接：為處於熔斷（CIRCUIT_TRIPPED_ESCALATED）的談判會話建立 `asyncio.Event` 掛起鎖。
2. 指令解碼與派發：接收手機端（WhatsApp / Telegram / 微信）發送的指令（1=妥協, 2=堅持, 3=撤退 或 自訂數字）。
3. 瞬間解鎖喚醒：派發指令並喚醒瀏覽器自駕代理恢復執行。
"""
from __future__ import annotations
import asyncio
from typing import Dict, Optional, Tuple
from ..session.schema import RemoteAuthCommand


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
    """全域 Webhook 雙向指令排程器（單例/中樞）。"""

    def __init__(self):
        self._bridges: Dict[str, SessionExecutionBridge] = {}

    def register_bridge(self, session_id: str) -> SessionExecutionBridge:
        bridge = SessionExecutionBridge(session_id=session_id)
        self._bridges[session_id] = bridge
        return bridge

    def get_bridge(self, session_id: str) -> Optional[SessionExecutionBridge]:
        return self._bridges.get(session_id)

    def unregister_bridge(self, session_id: str) -> None:
        self._bridges.pop(session_id, None)

    def dispatch_command(self, session_id: str, raw_command: str) -> Tuple[RemoteAuthCommand, Optional[float]]:
        """解析手機端回傳之文字/按鈕指令並解鎖對應橋樑。"""
        cmd_clean = str(raw_command).strip().upper()
        custom_price: Optional[float] = None

        if cmd_clean in ("1", "CONCEDE", "妥協", "讓步"):
            resolved = RemoteAuthCommand.CONCEDE
        elif cmd_clean in ("2", "HOLD_FIRM", "HOLD", "堅持", "不讓步"):
            resolved = RemoteAuthCommand.HOLD_FIRM
        elif cmd_clean in ("3", "WALK_AWAY", "QUIT", "撤退", "破局"):
            resolved = RemoteAuthCommand.WALK_AWAY
        else:
            # 嘗試解析自訂價格 (如 "13.2" 或 "¥13.20")
            cleaned_price = cmd_clean.replace("¥", "").replace("$", "").replace("元", "").strip()
            try:
                custom_price = float(cleaned_price)
                resolved = RemoteAuthCommand.OVERRIDE_PRICE
            except ValueError:
                raise ValueError(f"Unrecognized mobile command: '{raw_command}'. Expected 1, 2, 3 or custom price.")

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
            assert bridge.resolved_command is not None
            return bridge.resolved_command, bridge.custom_price
        except asyncio.TimeoutError:
            # 超時安全降級守衛：維持底線，不自作主張妥協
            bridge.unlock(RemoteAuthCommand.HOLD_FIRM, None)
            return RemoteAuthCommand.HOLD_FIRM, None
        finally:
            self.unregister_bridge(session_id)


# 全域單例
hub = WebhookHub()
