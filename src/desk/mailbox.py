"""Read-only mailbox intake.

IMAP uses the standard library. Login is refused unless the connection is
TLS with the default certificate check, or the host is the loopback test
fixture. Messages are read with BODY.PEEK on a read-only EXAMINE, so the
seen flag is left alone. Gmail uses ``urllib`` against the read methods and,
only when drafts are enabled, the drafts collection. This module has no
send call.
"""
from __future__ import annotations

import base64
import datetime as dt
import email
import email.utils
import imaplib
import json
import logging
import os
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("tradeflow.desk.mailbox")


class MailboxError(RuntimeError):
    """The mailbox could not be read without weakening TLS."""


def lookback_days() -> int:
    raw = os.environ.get("IMAP_LOOKBACK_DAYS", "14").strip() or "14"
    try:
        value = int(raw)
    except ValueError:
        return 14
    if value < 1:
        return 1
    return value


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


def loopback(host: str) -> bool:
    """True for the loopback addresses only.

    Public so the poller can draw the fixture-trust boundary: IMAP_SSL=0 is
    accepted exclusively for loopback (the labelled test fixture server),
    which is exactly the mailbox whose X-TradeFlow-Fixture marker is trusted.
    """
    return _loopback(host)


def _loopback(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def parse_rfc822(raw: bytes) -> dict[str, Any]:
    from src.desk.input_checks import message_id_or_blank, references_or_blank

    message = email.message_from_bytes(raw)
    _name, address = email.utils.parseaddr(message.get("From") or "")
    message_id = message_id_or_blank(message.get("Message-ID") or "")
    references = references_or_blank(message.get("References") or "")
    return {
        "sender": address or "",
        "message_id": message_id,
        "references": references,
        "subject": message.get("Subject") or "",
        "body": _body(message),
        "fixture": message.get("X-TradeFlow-Fixture"),
    }


MAX_MAIL_BODY_CHARS = 20_000


def _decode_part(message: email.message.Message) -> str:
    """Decode one MIME part, capping the text and refusing poison charsets.

    [SECURITY 2026-10-24] Two defects lived here. An attacker-chosen
    ``charset="x-nope"`` raised ``LookupError`` out of the whole mailbox
    pull — one poison email stalled ingestion of everything behind it,
    every poll, forever. And a multi-megabyte text part was decoded and
    stored whole, with no cap, because the HTTP body limit never sees
    IMAP or Gmail. Now: an unknown charset is replaced with utf-8, and the
    decoded text is capped at ``MAX_MAIL_BODY_CHARS`` — the same ceiling
    the paste API applies — with the truncation recorded by the caller.
    """
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if len(text) > MAX_MAIL_BODY_CHARS:
            return text[:MAX_MAIL_BODY_CHARS]
        return text
    raw_payload = message.get_payload()
    if isinstance(raw_payload, str):
        return raw_payload[:MAX_MAIL_BODY_CHARS]
    return ""


def _body(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if part.get_content_type() != "text/plain" or "attachment" in disposition:
                continue
            return _decode_part(part)
        return ""
    return _decode_part(message)


def _rfc822_from_fetch(payload: list) -> bytes | None:
    if not payload:
        return None
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _uidvalidity(client: imaplib.IMAP4) -> str:
    blobs: list[str] = []
    for values in (getattr(client, "untagged_responses", None) or {}).values():
        for value in values or []:
            if isinstance(value, bytes):
                blobs.append(value.decode("utf-8", "replace"))
            else:
                blobs.append(str(value))
    match = re.search(r"UIDVALIDITY\s+(\d+)", " ".join(blobs))
    return match.group(1) if match else "1"


def _since_token(days: int) -> str:
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days))
    return moment.strftime("%d-%b-%Y")


def _open_imap(host: str, port: int, username: str, password: str, use_ssl: bool) -> imaplib.IMAP4:
    client: imaplib.IMAP4
    if use_ssl:
        client = imaplib.IMAP4_SSL(host, port, ssl_context=_tls_context(), timeout=30)
    elif _loopback(host):
        client = imaplib.IMAP4(host, port, timeout=30)
    else:
        raise MailboxError("IMAP login requires TLS. Set IMAP_SSL=1.")
    client.login(username, password)
    return client


def pull_window(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
    skip_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Read the lookback window without changing flags.

    Returns ``(messages, uidvalidity)``. Each message carries ``uid`` and
    ``external_id``. Already recorded ids are skipped after the header is
    known, and the seen flag is never written.
    """
    known = skip_ids or set()
    found: list[dict[str, Any]] = []
    try:
        client = _open_imap(host, port, username, password, use_ssl)
    except ssl.SSLError as exc:
        raise MailboxError("The mail server certificate could not be verified.") from exc
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("The mailbox could not be read.") from exc
    try:
        try:
            status, _data = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailboxError("IMAP EXAMINE failed")
            uidvalidity = _uidvalidity(client)
            since = _since_token(lookback_days())
            status, data = client.uid("SEARCH", None, "SINCE", since)  # type: ignore[arg-type]
            if status != "OK" or not data or not data[0]:
                return found, uidvalidity
            for uid in data[0].split():
                uid_text = uid.decode("ascii", "replace") if isinstance(uid, bytes) else str(uid)
                uid_key = f"uid:{uidvalidity}:{uid_text}"
                if uid_key in known:
                    continue
                header_id = _header_message_id(client, uid_text)
                if header_id and header_id in known:
                    continue
                fetched, payload = client.uid("FETCH", uid_text, "(BODY.PEEK[])")
                if fetched != "OK":
                    continue
                raw = _rfc822_from_fetch(payload or [])
                if raw is None:
                    continue
                # [SECURITY 2026-10-24] One unparseable email used to unwind
                # the whole window: the poison message was never marked seen,
                # so every poll re-failed on it and every message behind it
                # in the lookback window was never ingested — a permanent,
                # silent stall caused by one inbound mail. A parse failure is
                # deterministic, so the item is marked poison and the caller
                # records it as seen; the fetch itself stays outside the
                # guard, because a connection failure SHOULD abort the poll
                # and be retried.
                try:
                    item = parse_rfc822(raw)
                except Exception as exc:  # noqa: BLE001 - one poison mail must not stall the pipeline
                    logger.warning(
                        "Unparseable message skipped (uid=%s): %s", uid_text, type(exc).__name__
                    )
                    found.append(
                        {
                            "poison": True,
                            "external_id": uid_key,
                            "uid": uid_text,
                            "uidvalidity": uidvalidity,
                        }
                    )
                    continue
                item["raw"] = raw
                item["uid"] = uid_text
                item["uidvalidity"] = uidvalidity
                external = item["message_id"] or uid_key
                item["external_id"] = external
                if external in known or uid_key in known:
                    continue
                found.append(item)
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass
    except MailboxError:
        raise
    except ssl.SSLError as exc:
        raise MailboxError("The mail server certificate could not be verified.") from exc
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("The mailbox could not be read.") from exc
    return found, uidvalidity


def _header_message_id(client: imaplib.IMAP4, uid_text: str) -> str:
    """Message-ID from the header, so a known id is not fetched again."""
    try:
        fetched, payload = client.uid("FETCH", uid_text, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    except (imaplib.IMAP4.error, OSError):
        return ""
    if fetched != "OK":
        return ""
    raw = _rfc822_from_fetch(payload or [])
    if not raw:
        return ""
    head = raw.split(b"\r\n\r\n", 1)[0]
    match = re.search(br"(?im)^message-id:\s*(<[^>\r\n]+>)", head)
    if match is None:
        return ""
    return match.group(1).decode("ascii", "replace").strip()


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


def gmail_list_recent(opener, token: str) -> list[str]:
    ids: list[str] = []
    page: str | None = None
    for _page in range(5):
        params = {"q": f"newer_than:{lookback_days()}d"}
        if page:
            params["pageToken"] = page
        query = urllib.parse.urlencode(params)
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{query}"
        payload = _read_json(_request(opener, url, token=token))
        ids.extend(str(item["id"]) for item in payload.get("messages") or [] if item.get("id"))
        page = payload.get("nextPageToken") or None
        if not page:
            break
    return ids


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


def create_gmail_draft(opener, token: str, raw_message: bytes, draft_id: str | None = None) -> str:
    """Create or replace a draft. This talks to the drafts collection only."""
    if not gmail_drafts_enabled():
        raise RuntimeError("Gmail drafts are not configured")
    encoded = base64.urlsafe_b64encode(raw_message).decode("ascii")
    body = json.dumps({"message": {"raw": encoded}}).encode("utf-8")
    if draft_id:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts/" + urllib.parse.quote(draft_id, safe="")
        payload = _read_json(_request(opener, url, token=token, data=body, method="PUT"))
        return str(payload.get("id") or draft_id)
    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    payload = _read_json(_request(opener, url, token=token, data=body, method="POST"))
    return str(payload.get("id") or "")


def pull_gmail_window(opener, skip_ids: set[str] | None = None) -> list[dict[str, Any]]:
    token = refresh_access_token(opener)
    known = skip_ids or set()
    found = []
    for message_id in gmail_list_recent(opener, token):
        marker = f"gmail:{message_id}"
        if marker in known:
            continue
        raw = gmail_get_raw(opener, token, message_id)
        # Same poison guard as the IMAP path: one unparseable Gmail message
        # must be skipped and recorded as seen, not stall the whole window.
        try:
            item = parse_rfc822(raw)
        except Exception as exc:  # noqa: BLE001 - one poison mail must not stall the pipeline
            logger.warning(
                "Unparseable Gmail message skipped (id=%s): %s", message_id, type(exc).__name__
            )
            found.append({"poison": True, "external_id": marker, "gmail_id": message_id})
            continue
        item["raw"] = raw
        item["gmail_id"] = message_id
        if not item["message_id"]:
            item["message_id"] = marker
        item["external_id"] = item["message_id"]
        if item["external_id"] in known:
            continue
        found.append(item)
    return found
