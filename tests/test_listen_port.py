"""The app entrypoint says so when its listen port is already taken."""
from __future__ import annotations

import socket
import unittest

from src.app.main import listen_port_available, port_in_use_message


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


if __name__ == "__main__":
    unittest.main()
