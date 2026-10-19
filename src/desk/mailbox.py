"""Read-only mailbox intake.

IMAP uses the standard library. Gmail uses ``urllib`` against the read-only
message methods and, only when drafts are enabled, the drafts collection.
This module has no send call.
"""
from __future__ import annotations

import base64
import email
import email.utils
import imaplib
import json
import os
import urllib.parse
import urllib.request
from typing import Any


def imap_settings_from_env() -> dict[str, Any] | None:
    host = os.environ.get("IMAP_HOST", "").strip()
    username = os.environ.get("IMAP_USERNAME", "").strip()
    password = os.environ.get("IMAP_PASSWORD", "").strip()
    if not (host and username and password):
        return None
    port_raw = os.environ.get("IMAP_PORT", "").strip()
    use_ssl = os.environ.get("IMAP_SSL", "1").strip() not in {"0", "false", "False", "no"}
    if port_raw:
        port = int(port_raw)
    else:
        port = 993 if use_ssl else 143
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_ssl": use_ssl,
    }


def gmail_configured() -> bool:
    needed = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    return all(os.environ.get(key, "").strip() for key in needed)


def gmail_drafts_enabled() -> bool:
    return os.environ.get("GMAIL_DRAFTS", "").strip() == "1" and gmail_configured()


def parse_rfc822(raw: bytes) -> dict[str, Any]:
    message = email.message_from_bytes(raw)
    _name, address = email.utils.parseaddr(message.get("From") or "")
    return {
        "sender": address or "",
        "message_id": (message.get("Message-ID") or "").strip(),
        "subject": message.get("Subject") or "",
        "body": _body(message),
        "fixture": message.get("X-TradeFlow-Fixture"),
    }


def _body(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if part.get_content_type() != "text/plain" or "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    text = message.get_payload()
    return text if isinstance(text, str) else ""


def _rfc822_from_fetch(payload: list) -> bytes | None:
    if not payload:
        return None
    first = payload[0]
    if isinstance(first, tuple) and len(first) >= 2 and isinstance(first[1], (bytes, bytearray)):
        return bytes(first[1])
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def pull_unseen(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
) -> list[dict[str, Any]]:
    """Log in with imaplib, read UNSEEN, then mark those messages seen."""
    client_cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
    found: list[dict[str, Any]] = []
    with client_cls(host, port, timeout=30) as client:
        client.login(username, password)
        client.select("INBOX")
        status, data = client.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return found
        numbers = data[0].split()
        pulled: list[bytes] = []
        for number in numbers:
            fetched, payload = client.fetch(number, "(RFC822)")
            if fetched != "OK":
                continue
            raw = _rfc822_from_fetch(payload)
            if raw is None:
                continue
            item = parse_rfc822(raw)
            item["raw"] = raw
            found.append(item)
            pulled.append(number)
        for number in pulled:
            client.store(number, "+FLAGS", "\\Seen")
    return found


def _request(opener, url: str, *, token: str | None = None, data: bytes | None = None, method: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    return opener.open(request, timeout=30)


def _read_json(response) -> dict[str, Any]:
    with response as handle:
        body = handle.read()
    return json.loads(body.decode("utf-8"))


def refresh_access_token(opener) -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    payload = _read_json(opener.open(request, timeout=30))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Gmail token refresh did not return an access token")
    return str(token)


def gmail_list_unread(opener, token: str) -> list[str]:
    query = urllib.parse.urlencode({"q": "is:unread"})
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{query}"
    payload = _read_json(_request(opener, url, token=token))
    return [str(item["id"]) for item in payload.get("messages") or [] if item.get("id")]


def gmail_get_raw(opener, token: str, message_id: str) -> bytes:
    url = (
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
        + urllib.parse.quote(message_id, safe="")
        + "?format=raw"
    )
    payload = _read_json(_request(opener, url, token=token))
    encoded = str(payload.get("raw") or "")
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def create_gmail_draft(opener, token: str, raw_message: bytes) -> str:
    """Create a draft. This posts to the drafts collection only."""
    if not gmail_drafts_enabled():
        raise RuntimeError("Gmail drafts are not configured")
    encoded = base64.urlsafe_b64encode(raw_message).decode("ascii")
    body = json.dumps({"message": {"raw": encoded}}).encode("utf-8")
    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    payload = _read_json(_request(opener, url, token=token, data=body, method="POST"))
    return str(payload.get("id") or "")


def pull_gmail_unread(opener) -> list[dict[str, Any]]:
    token = refresh_access_token(opener)
    found = []
    for message_id in gmail_list_unread(opener, token):
        raw = gmail_get_raw(opener, token, message_id)
        item = parse_rfc822(raw)
        item["raw"] = raw
        item["gmail_id"] = message_id
        if not item["message_id"]:
            item["message_id"] = f"gmail:{message_id}"
        found.append(item)
    return found
