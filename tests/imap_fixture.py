"""In-process IMAP4rev1 server for tests.

Labelled test fixture. It speaks enough of the protocol for ``imaplib`` to
log in, EXAMINE the inbox, and UID-fetch with BODY.PEEK. It is not a mail
provider, and a fetch does not mark a message seen.
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
                self._select(tag, command)
            elif command == "UID":
                self._uid(tag, args)
            elif command == "LOGOUT":
                self.wfile.write(b"* BYE fixture closing\r\n")
                self._ok(tag, "LOGOUT completed")
                return
            elif command == "NOOP":
                self._ok(tag, "NOOP completed")
            else:
                self._bad(tag, "unknown command")

    def _select(self, tag: str, command: str) -> None:
        fixture: ImapFixture = self.server.fixture  # type: ignore[attr-defined]
        with fixture.lock:
            count = len(fixture.messages)
        self.wfile.write(f"* {count} EXISTS\r\n".encode())
        self.wfile.write(b"* 0 RECENT\r\n")
        self.wfile.write(b"* FLAGS (\\Seen)\r\n")
        self.wfile.write(b"* OK [UIDVALIDITY 1] UIDs\r\n")
        if command == "EXAMINE":
            self.wfile.write(b"* OK [READ-ONLY] examined\r\n")
            self._ok(tag, "[READ-ONLY] EXAMINE completed")
        else:
            self.wfile.write(b"* OK [READ-WRITE] selected\r\n")
            self._ok(tag, "SELECT completed")

    def _uid(self, tag: str, args: list[str]) -> None:
        if not args:
            self._bad(tag, "UID missing subcommand")
            return
        sub = args[0].upper()
        fixture: ImapFixture = self.server.fixture  # type: ignore[attr-defined]
        if sub == "SEARCH":
            with fixture.lock:
                ids = [str(index) for index in range(1, len(fixture.messages) + 1)]
            self.wfile.write(f"* SEARCH {' '.join(ids)}\r\n".encode())
            self._ok(tag, "SEARCH completed")
            return
        if sub == "FETCH":
            try:
                number = int(args[1])
            except (IndexError, ValueError):
                self._bad(tag, "FETCH missing uid")
                return
            with fixture.lock:
                if number < 1 or number > len(fixture.messages):
                    self._no(tag, "FETCH no such message")
                    return
                payload = fixture.messages[number - 1]["raw"]
            header = f"* {number} FETCH (UID {number} BODY[] {{{len(payload)}}}\r\n".encode()
            self.wfile.write(header)
            self.wfile.write(payload)
            self.wfile.write(b")\r\n")
            self.wfile.flush()
            self._ok(tag, "FETCH completed")
            return
        self._bad(tag, "unknown UID command")

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
        self._server.allow_reuse_address = True
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
