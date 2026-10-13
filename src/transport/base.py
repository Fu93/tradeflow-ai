"""TradeFlow v4 — 通訊傳輸抽象基底介面。

職責：定義跨通道（Email、瀏覽器、即時通訊）統一訊息模型與抽象契約。
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """跨通道統一收件訊息資料模型。"""
    channel: str                                       # "email" | "browser" | "whatsapp" | "manual"
    session_id: str
    sender: str
    content: str
    parsed_price: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseTransport(ABC):
    """所有通訊通道之抽象基底類別。"""

    @abstractmethod
    async def send_message(
        self,
        recipient: str,
        content: str,
        subject: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """發送訊息至指定外部通道。"""
        pass

    @abstractmethod
    async def poll_inbound(self) -> List[InboundMessage]:
        """輪詢或抓取新接收的訊息。"""
        pass

    @staticmethod
    def extract_offer(text: str) -> Optional[float]:
        """階梯式價格正則提取器（支援 ¥, $, RMB, 元, 塊, 單價, 底價）。"""
        if not text:
            return None
        clean_text = text.replace(",", "").strip()

        currency = r"(?:[\$¥]|USD|RMB|CNY|NTD)?"
        patterns = [
            # Pattern 1: 明確單價標記 (e.g., "單價 12.5 元", "unit price: $12.5", "12.8/pc")
            re.compile(
                rf"(?:單價|unit\s*price|price|價格|每件|個|pcs?|pc)[\s:：]*{currency}\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*(?:元|塊|刀|/pc)?",
                re.IGNORECASE,
            ),
            # Pattern 2: 讓步條件或底價 (e.g., "最低只能給到 13.2", "can do 13.0", "一口價 12")
            re.compile(
                rf"(?:最低|只能|給到|底價|一口價|can\s*do|give\s*you|offer)[\s:：]*{currency}\s*([0-9]+(?:\.[0-9]{{1,2}})?)\s*(?:元|塊|刀)?",
                re.IGNORECASE,
            ),
            # Pattern 3: 純幣別加數字 (e.g., "¥12.8", "$13.50", "13.2元")
            re.compile(
                rf"(?:[¥\$]|USD|RMB)\s*([0-9]+(?:\.[0-9]{{1,2}})?)|([0-9]+(?:\.[0-9]{{1,2}})?)\s*(?:元|塊|刀)",
                re.IGNORECASE,
            ),
            # Pattern 4: 純數字浮點數 (寬鬆備案，e.g. "14.50")
            re.compile(r"\b([0-9]+\.[0-9]{1,2})\b"),
        ]

        for pat in patterns:
            match = pat.search(clean_text)
            if match:
                groups = [g for g in match.groups() if g is not None]
                if groups:
                    try:
                        return float(groups[0])
                    except ValueError:
                        continue
        return None
