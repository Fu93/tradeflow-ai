"""The live desk script fails closed, and passes against a desk it can reach."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "desk_verify.py"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_desk_verify_fails_when_the_desk_is_down():
    env = os.environ.copy()
    env["TRADEFLOW_API_KEY"] = "desk-verify-key-0001"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "http://127.0.0.1:9"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAIL" in result.stdout + result.stderr


def test_desk_verify_passes_against_a_running_desk(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("IMAP_", "GMAIL_", "SMTP_")) or name in {"DESK_SMTP_SEND", "TRADEFLOW_LLM_BASE_URL", "OPENAI_BASE_URL"}:
            env.pop(name)
    env.update(
        {
            "TRADEFLOW_API_KEY": "desk-verify-key-0001",
            "TRADEFLOW_DESK_DB": str(tmp_path / "desk.db"),
            "TRADEFLOW_ENV": "sandbox",
            "TRADEFLOW_MARKET_OFFLINE": "1",
            "TRADEFLOW_LEGACY": "0",
            "PORT": str(port),
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "src.app.main"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        up = False
        while time.time() < deadline:
            if server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(base + "/health", timeout=1) as response:
                    up = response.status == 200
                    if up:
                        break
            except OSError:
                time.sleep(0.2)
        assert up, "desk did not open /health"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), base],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "8 passed, 0 failed" in result.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
