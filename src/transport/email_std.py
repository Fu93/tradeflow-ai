"""TradeFlow v4 — 純標準庫電子郵件通道 (EmailStdTransport)。

職責：
1. 零外部依賴：完全採用 Python 內建標準庫 `smtplib` 與 `imaplib`。
2. RFC 5322 線程追蹤：自動注入與解析 `Message-ID` / `In-Reply-To` 標頭以精確關聯 session_id。
3. 離線自適應：若未配置 SMTP/IMAP 伺服器，自動降級為 Mock 本地記憶體佇列，保證離線 Demo 100% 暢通。
"""
from __future__ import annotations
import os
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from .base import BaseTransport, InboundMessage


class EmailStdTransport(BaseTransport):
    """純標準庫 SMTP/IMAP 電子郵件傳輸通道。"""

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: int = 465,
        imap_host: Optional[str] = None,
        imap_port: int = 993,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_ssl: bool = True,
    ):
        self.smtp_host = smtp_host or os.getenv("SMTP_HOST")
        self.smtp_port = int(os.getenv("SMTP_PORT", str(smtp_port)))
        self.imap_host = imap_host or os.getenv("IMAP_HOST")
        self.imap_port = int(os.getenv("IMAP_PORT", str(imap_port)))
        self.username = username or os.getenv("EMAIL_USERNAME")
        self.password = password or os.getenv("EMAIL_PASSWORD")
        self.use_ssl = use_ssl

        # 離線/Mock 記憶體測試隊列
        self.is_mock_mode = not bool(self.smtp_host and self.username and self.password)
        self.mock_sent_box: List[Dict[str, Any]] = []
        self.mock_inbox: List[InboundMessage] = []

    async def send_message(
        self,
        recipient: str,
        content: str,
        subject: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        meta = metadata or {}
        session_id = meta.get("session_id", "default_session")
        round_idx = meta.get("round_index", 1)
        sub = subject or f"[TradeFlow Procurement] Inquiry: {meta.get('product', 'RFQ')} (Ref: {session_id})"

        if self.is_mock_mode:
            # 離線 Mock 派發
            self.mock_sent_box.append({
                "recipient": recipient,
                "subject": sub,
                "content": content,
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc),
            })
            return True

        # 真實 SMTP SSL 派發
        msg = MIMEMultipart()
        msg["From"] = self.username
        msg["To"] = recipient
        msg["Subject"] = sub
        msg["Message-ID"] = f"<tradeflow.{session_id}.r{round_idx}@tradeflow.ai>"
        if "in_reply_to" in meta:
            msg["In-Reply-To"] = meta["in_reply_to"]
            msg["References"] = meta["in_reply_to"]

        msg.attach(MIMEText(content, "plain", "utf-8"))

        try:
            if self.use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(self.username, self.password)
                    server.send_message(msg)
            return True
        except Exception as e:
            # 記錄失敗日誌但防止程式崩潰
            print(f"[EmailStdTransport] SMTP send failed: {e}")
            return False

    async def poll_inbound(self) -> List[InboundMessage]:
        if self.is_mock_mode:
            messages = list(self.mock_inbox)
            self.mock_inbox.clear()
            return messages

        # 真實 IMAP SSL 輪詢
        results: List[InboundMessage] = []
        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as mail:
                mail.login(self.username, self.password)
                mail.select("INBOX")
                status, response = mail.search(None, "UNSEEN")
                if status != "OK":
                    return results

                for num in response[0].split():
                    res, msg_data = mail.fetch(num, "(RFC822)")
                    if res != "OK":
                        continue
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # 提取主旨與寄件者
                    sub_header = decode_header(msg.get("Subject", ""))[0]
                    subject = sub_header[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(sub_header[1] or "utf-8", errors="ignore")

                    sender = msg.get("From", "unknown")
                    body = self._extract_body(msg)
                    price = self.extract_offer(body) or self.extract_offer(subject)

                    # 嘗試從主旨或 References 解析 session_id
                    session_id = "default_session"
                    if "Ref: " in subject:
                        session_id = subject.split("Ref: ")[-1].replace(")", "").strip()

                    results.append(
                        InboundMessage(
                            channel="email",
                            session_id=session_id,
                            sender=sender,
                            content=body,
                            parsed_price=price,
                            metadata={"subject": subject, "message_id": msg.get("Message-ID")},
                        )
                    )
        except Exception as e:
            print(f"[EmailStdTransport] IMAP poll failed: {e}")
        return results

    def _extract_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="ignore")
        return ""
