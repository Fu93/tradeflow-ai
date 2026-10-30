"""Operator checks must not bless incomplete credentials or disclose their values."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("operator_check", ROOT / "scripts/check_live_readiness.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "EMAIL_USERNAME",
        "EMAIL_PASSWORD",
        "IMAP_HOST",
        "IMAP_USERNAME",
        "IMAP_PASSWORD",
        "IMAP_PORT",
        "GMAIL_CLIENT_ID",
        "GMAIL_CLIENT_SECRET",
        "GMAIL_REFRESH_TOKEN",
        "TRADEFLOW_APPROVAL_PUBKEY",
        "DESK_SMTP_SEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-gmail-client")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-gmail-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test-gmail-refresh")
    monkeypatch.setenv("TRADEFLOW_ENV", "production")
    monkeypatch.setenv("TRADEFLOW_API_KEY", "operator-test-key-not-for-real-use")
    monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "operator-test-chain-not-for-real-use")
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "0")
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "missing.db"))
    return tmp_path


def test_partial_smtp_credentials_cannot_pass(configured, monkeypatch, capsys):
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_USERNAME", "operator@example.test")
    assert operator.main([]) == 1


def test_partial_imap_credentials_cannot_pass(configured, monkeypatch, capsys):
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    assert operator.main([]) == 1


def test_invalid_required_public_key_cannot_pass(configured, monkeypatch, capsys):
    monkeypatch.setenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", "1")
    monkeypatch.setenv("TRADEFLOW_APPROVAL_PUBKEY", "not-a-valid-key")
    assert operator.main([]) == 1


def test_repeated_invocations_do_not_keep_old_failure_state(configured, monkeypatch, capsys):
    monkeypatch.setenv("TRADEFLOW_API_KEY", "")
    assert operator.main([]) == 1
    monkeypatch.setenv("TRADEFLOW_API_KEY", "operator-test-key-not-for-real-use")
    assert operator.main([]) == 0


def test_manual_intake_does_not_need_mail_and_does_not_create_a_database(configured, monkeypatch, capsys):
    for key in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert operator.main(["--intake", "manual", "--json"]) == 0
    assert not (configured / "missing.db").exists()
    output = capsys.readouterr().out
    assert "operator-test-key-not-for-real-use" not in output
    assert "operator-test-chain-not-for-real-use" not in output
    assert '"production_live_verified": false' in output


def test_probe_requires_an_existing_database(configured, capsys):
    assert operator.main(["--intake", "manual", "--probe-db"]) == 1
    assert not (configured / "missing.db").exists()


def test_probe_verifies_schema_and_rolls_back_its_write(configured, monkeypatch):
    import sqlite3
    from src.desk.store import DeskStore

    path = configured / "desk.db"
    store = DeskStore(str(path))
    store.close()
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(path))
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert operator.inspect_configuration(intake="manual", probe_db=True)["ok"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == before
    wrong = configured / "wrong.db"
    with sqlite3.connect(wrong) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    monkeypatch.setenv("TRADEFLOW_DESK_DB", str(wrong))
    assert not operator.inspect_configuration(intake="manual", probe_db=True)["ok"]


def test_connection_error_cannot_disclose_password_and_never_sends(configured, monkeypatch, capsys):
    from unittest.mock import MagicMock
    import smtplib

    secret = "synthetic-password-do-not-print"
    monkeypatch.setenv("DESK_SMTP_SEND", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_USERNAME", "operator@example.test")
    monkeypatch.setenv("EMAIL_PASSWORD", secret)
    client = MagicMock()
    client.__enter__.return_value = client
    client.login.side_effect = RuntimeError(secret)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    assert operator.main(["--connect", "--intake", "manual", "--json"]) == 1
    assert secret not in capsys.readouterr().out
    client.send_message.assert_not_called()
    assert factory.call_args.kwargs["context"].check_hostname


def test_invalid_port_or_environment_fails_without_traceback(configured, monkeypatch):
    monkeypatch.setenv("TRADEFLOW_ENV", "prod-typo")
    assert not operator.inspect_configuration(intake="manual")["ok"]
    monkeypatch.setenv("TRADEFLOW_ENV", "production")
    monkeypatch.setenv("IMAP_HOST", "imap.example.test")
    monkeypatch.setenv("IMAP_USERNAME", "operator@example.test")
    monkeypatch.setenv("IMAP_PASSWORD", "synthetic-password")
    monkeypatch.setenv("IMAP_PORT", "wrong-port")
    assert not operator.inspect_configuration(intake="imap")["ok"]
