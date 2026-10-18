"""The app entrypoint says so when its listen port is already taken."""
from __future__ import annotations

import inspect
import socket
import unittest
from pathlib import Path

from src.app.main import (
    listen_port_available,
    port_has_listener,
    port_in_use_message,
    serve_http,
    wait_for_free_port,
)

ROOT = Path(__file__).resolve().parents[1]


class TestListenPort(unittest.TestCase):
    def test_taken_port_is_reported_with_a_port_hint(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        try:
            self.assertFalse(listen_port_available("127.0.0.1", port))
        finally:
            held.close()
        self.assertTrue(listen_port_available("127.0.0.1", port))
        message = port_in_use_message(port)
        self.assertIn(f"Port {port} is already in use", message)
        self.assertIn("PORT=<free-port>", message)
        self.assertIn("-m src.app.main", message)

    def test_connect_probe_does_not_bind(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        held.bind(("127.0.0.1", 0))
        port = held.getsockname()[1]
        held.close()
        self.assertFalse(port_has_listener("0.0.0.0", port))
        self.assertTrue(listen_port_available("127.0.0.1", port))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        busy = listener.getsockname()[1]
        try:
            self.assertTrue(port_has_listener("127.0.0.1", busy))
            self.assertFalse(listen_port_available("127.0.0.1", busy))
            pauses = []
            with self.assertRaises(SystemExit) as caught:
                wait_for_free_port(
                    "127.0.0.1", busy, attempts=2, pause=lambda _delay: pauses.append(_delay)
                )
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(pauses, [1])
        finally:
            listener.close()
        self.assertTrue(listen_port_available("127.0.0.1", busy))

    def test_one_server_process_and_healthcheck_does_not_bind(self):
        source = inspect.getsource(serve_http)
        self.assertIn("workers=1", source)
        self.assertIn("reload=False", source)
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        exec_cmds = [
            line.strip() for line in docker.splitlines() if line.strip().startswith("CMD [")
        ]
        self.assertEqual(exec_cmds, ['CMD ["python", "-m", "src.app.main"]'])
        health = [line for line in docker.splitlines() if "HEALTHCHECK" in line or "urlopen" in line]
        self.assertTrue(health)
        self.assertIn("urlopen", docker)
        self.assertNotIn("socket.bind", docker)
        self.assertNotIn("uvicorn", docker)
        rendered = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertNotIn("dockerCommand:", rendered)
        self.assertNotIn("\n      - key: PORT\n", rendered)


if __name__ == "__main__":
    unittest.main()
