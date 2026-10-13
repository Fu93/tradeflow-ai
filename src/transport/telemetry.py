"""TradeFlow v4 — 手機端即時遙測通知中樞 (Telemetry Notifier)。

職責：
1. 零成本/免企業審核手機通道：
   - WhatsApp: CallMeBot API (HTTP GET 直接推播個人 WhatsApp)
   - 微信 (WeChat): Server醬 Turbo / PushPlus (HTTP POST 直達微信服務號彈窗)
   - Telegram: Bot API
2. 結構化卡片排版：發送熔斷警報卡片、指令選單、以及成交祝賀戰報。
3. 離線自適應：若無環境變數憑證，自動以記憶體隊列記錄，確保單元測試與離線展示 100% 穩定。
"""
from __future__ import annotations
import os
import json
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


class TelemetryNotifier:
    """手機端即時遙測派發器。"""

    def __init__(
        self,
        whatsapp_phone: Optional[str] = None,
        whatsapp_apikey: Optional[str] = None,
        wechat_sendkey: Optional[str] = None,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
    ):
        self.whatsapp_phone = whatsapp_phone or os.getenv("WHATSAPP_PHONE")
        self.whatsapp_apikey = whatsapp_apikey or os.getenv("WHATSAPP_APIKEY")
        self.wechat_sendkey = wechat_sendkey or os.getenv("SERVERCHAN_SENDKEY")
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID")

        # 離線/Mock 測試記錄隊列
        self.mock_history: List[Dict[str, Any]] = []

    def notify_circuit_breaker(
        self,
        session_id: str,
        product: str,
        opponent_offer: float,
        p_break: float,
        advice_text: str,
        proposed_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """派發緊急熔斷警報至主管手機。"""
        title = f"[TradeFlow 熔斷警報] {product} 談判需授權"
        advice_line = f"• 引擎建議還價: ¥{proposed_price:.2f}" if proposed_price else ""
        content = (
            f"【TradeFlow 採購自駕斷路器觸發】\n"
            f"• 標的商品: {product}\n"
            f"• 對手當前報價: ¥{opponent_offer:.2f}\n"
            f"• 破局風險: {p_break:.1%} (已超過 15% 安全閾值!)\n"
            f"{advice_line}\n"
            f"• 策略說明: {advice_text}\n\n"
            f"請直接在手機回覆數字指令：\n"
            f"[ 1 ] 妥協: 依引擎建議讓步報價\n"
            f"[ 2 ] 堅持: 維持當前底線不退讓\n"
            f"[ 3 ] 撤退: 立即終止並宣告破局\n"
            f"[ 自訂金額 ]: 如直接回覆 13.0"
        )
        return self._dispatch_all(session_id, title, content, event_type="CIRCUIT_BREAKER")

    def notify_deal_closed(
        self,
        session_id: str,
        product: str,
        final_price: float,
        savings_pct: float,
        rounds: int,
    ) -> Dict[str, Any]:
        """派發談判順利成交賀報。"""
        title = f"[TradeFlow 談判成功] {product} 達成交易！"
        content = (
            f"【TradeFlow 採購成果戰報】\n"
            f"• 標的商品: {product}\n"
            f"• 最終成交單價: ¥{final_price:.2f}\n"
            f"• 採購節省幅度: {savings_pct:.1%}\n"
            f"• 協商總輪數: {rounds} 輪\n"
            f"• 執行狀態: 100% 無人值守自動談判完成！"
        )
        return self._dispatch_all(session_id, title, content, event_type="DEAL_CLOSED")

    def _dispatch_all(
        self, session_id: str, title: str, content: str, event_type: str
    ) -> Dict[str, Any]:
        result = {
            "session_id": session_id,
            "event_type": event_type,
            "title": title,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channels_sent": [],
        }

        # 1. 記錄於本地 Mock 隊列（保證測試與離線展示）
        self.mock_history.append(result)

        # 2. WhatsApp (CallMeBot)
        if self.whatsapp_phone and self.whatsapp_apikey:
            if self._send_whatsapp(title + "\n\n" + content):
                result["channels_sent"].append("whatsapp")

        # 3. 微信 (Server醬 Turbo)
        if self.wechat_sendkey:
            if self._send_wechat(title, content):
                result["channels_sent"].append("wechat")

        # 4. Telegram Bot
        if self.telegram_token and self.telegram_chat_id:
            if self._send_telegram(title + "\n\n" + content):
                result["channels_sent"].append("telegram")

        return result

    def _send_whatsapp(self, text: str) -> bool:
        try:
            encoded_text = urllib.parse.quote(text)
            url = f"https://api.callmebot.com/whatsapp.php?phone={self.whatsapp_phone}&text={encoded_text}&apikey={self.whatsapp_apikey}"
            req = urllib.request.Request(url, headers={"User-Agent": "TradeFlow-Bot/4.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TelemetryNotifier] WhatsApp push error: {e}")
            return False

    def _send_wechat(self, title: str, desp: str) -> bool:
        try:
            url = f"https://sctapi.ftqq.com/{self.wechat_sendkey}.send"
            data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "TradeFlow-Bot/4.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TelemetryNotifier] WeChat push error: {e}")
            return False

    def _send_telegram(self, text: str) -> bool:
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = json.dumps({"chat_id": self.telegram_chat_id, "text": text}).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json", "User-Agent": "TradeFlow-Bot/4.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"[TelemetryNotifier] Telegram push error: {e}")
            return False


# 全域單例
notifier = TelemetryNotifier()
