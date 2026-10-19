"""In-process IMAP4rev1 server for tests.

Labelled test fixture. It speaks enough of the protocol for ``imaplib`` to
log in, read unseen messages, and mark them seen. It is not a mail provider.
"""
from __future__ import annotations

import socket
import socketserver
import threading
import time


def _tokens(line: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quoted = False
    for ch in line:
        if quoted:
            if ch == '"':
                quoted = False
                out.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
            continue
        if ch == '"':
            quoted = True
            continue
        if ch == " ":
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        fixture: ImapFixture = self.server.fixture  # type: ignore[attr-defined]
        self.connection.settimeout(30)
        self.wfile.write(b"* OK IMAP4rev1 fixture ready\r\n")
        self.wfile.flush()
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            parts = _tokens(line)
            if len(parts) < 2:
                continue
            tag, command = parts[0], parts[1].upper()
            args = parts[2:]
            if command == "CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
                self._ok(tag, "CAPABILITY completed")
            elif command == "LOGIN":
                user = args[0] if args else ""
                password = args[1] if len(args) > 1 else ""
                if user != fixture.username or password != fixture.password:
                    self._no(tag, "LOGIN failed")
                    continue
                self._ok(tag, "LOGIN completed")
            elif command in {"SELECT", "EXAMINE"}:
                with fixture.lock:
                    count = len(fixture.messages)
                self.wfile.write(f"* {count} EXISTS\r\n".encode())
                self.wfile.write(b"* 0 RECENT\r\n")
                self.wfile.write(b"* FLAGS (\\Seen)\r\n")
                self.wfile.write(b"* OK [UIDVALIDITY 1] UIDs\r\n")
                self.wfile.write(b"* OK [READ-WRITE] selected\r\n")
                self._ok(tag, "SELECT completed")
            elif command == "SEARCH":
                wanted = {item.upper() for item in args}
                unseen_only = "UNSEEN" in wanted
                with fixture.lock:
                    ids = []
                    for index, message in enumerate(fixture.messages, start=1):
                        if unseen_only and message["seen"]:
                            continue
                        ids.append(str(index))
                listing = " ".join(ids)
                self.wfile.write(f"* SEARCH {listing}\r\n".encode())
                self._ok(tag, "SEARCH completed")
            elif command == "FETCH":
                try:
                    number = int(args[0])
                except (IndexError, ValueError):
                    self._bad(tag, "FETCH missing sequence")
                    continue
                with fixture.lock:
                    if number < 1 or number > len(fixture.messages):
                        self._no(tag, "FETCH no such message")
                        continue
                    payload = fixture.messages[number - 1]["raw"]
                header = f"* {number} FETCH (RFC822 {{{len(payload)}}}\r\n".encode()
                self.wfile.write(header)
                self.wfile.write(payload)
                self.wfile.write(b")\r\n")
                self.wfile.flush()
                self._ok(tag, "FETCH completed")
            elif command == "STORE":
                try:
                    number = int(args[0])
                except (IndexError, ValueError):
                    self._bad(tag, "STORE missing sequence")
                    continue
                with fixture.lock:
                    if number < 1 or number > len(fixture.messages):
                        self._no(tag, "STORE no such message")
                        continue
                    fixture.messages[number - 1]["seen"] = True
                self.wfile.write(f"* {number} FETCH (FLAGS (\\Seen))\r\n".encode())
                self._ok(tag, "STORE completed")
            elif command == "NOOP":
                self._ok(tag, "NOOP completed")
            elif command == "LOGOUT":
                self.wfile.write(b"* BYE fixture closing\r\n")
                self._ok(tag, "LOGOUT completed")
                return
            else:
                self._bad(tag, "unknown command")

    def _ok(self, tag: str, text: str) -> None:
        self.wfile.write(f"{tag} OK {text}\r\n".encode())
        self.wfile.flush()

    def _no(self, tag: str, text: str) -> None:
        self.wfile.write(f"{tag} NO {text}\r\n".encode())
        self.wfile.flush()

    def _bad(self, tag: str, text: str) -> None:
        self.wfile.write(f"{tag} BAD {text}\r\n".encode())
        self.wfile.flush()


class ImapFixture:
    """Local mailbox. ``append`` drops an unseen RFC822 message into INBOX."""

    def __init__(self, username: str = "desk", password: str = "desk") -> None:
        self.username = username
        self.password = password
        self.messages: list[dict] = []
        self.lock = threading.Lock()
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fixture = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    break
            except OSError:
                time.sleep(0.05)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def append(self, raw: bytes) -> None:
        with self.lock:
            self.messages.append({"raw": raw, "seen": False})

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
