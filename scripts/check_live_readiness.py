"""Configuration preflight, not a claim of live supplier delivery.

Default: inspect environment only, never create a database or connect externally.
--probe-db checks an existing database with a rolled-back write.
--connect tests configured SMTP/IMAP authentication without sending or reading mail.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = {"__FILL_ME__", "local-demo-key-change-me", "local-demo-chain-key-change-me"}


def inspect_configuration(intake="auto", probe_db=False, connect=False):
    from nacl.bindings import crypto_core_ed25519_is_valid_point
    from src.desk.approval_signing import approval_policy, required_approval_pubkey
    from src.desk.store import db_path
    from scripts.backup_desk_db import _verify

    checks = []

    def record(name, ok, detail, *, warning=False):
        checks.append({"name": name, "status": "PASS" if ok else "WARN" if warning else "FAIL", "detail": detail})

    def value(name):
        text = os.environ.get(name, "").strip()
        return "" if text in SAMPLES else text

    def require(names, label):
        missing = [name for name in names if not value(name)]
        record(label, not missing, "configured" if not missing else "missing: " + ", ".join(missing))
        return not missing

    def port(name, default):
        try:
            number = int(value(name) or str(default))
            if not 1 <= number <= 65535:
                raise ValueError
            return number
        except ValueError:
            record(name, False, "must be an integer between 1 and 65535")
            return None

    record("API key", len(value("TRADEFLOW_API_KEY")) >= 16, "requires a non-sample key of at least 16 characters")
    require(("TRADEFLOW_CHAIN_KEY",), "Audit chain key")
    mode = os.environ.get("TRADEFLOW_ENV", "sandbox").strip().lower()
    record("Environment mode", mode in {"production", "sandbox"}, "use production or sandbox")
    production = mode == "production"
    record(
        "Production mode",
        production,
        "production" if production else "sandbox; not a production deployment",
        warning=True,
    )
    policy = approval_policy()
    public = required_approval_pubkey()
    valid = False
    try:
        valid = bool(public and crypto_core_ed25519_is_valid_point(bytes.fromhex(public)))
    except (ValueError, TypeError):
        pass
    record(
        "Signed approval",
        valid if policy == "required" else False,
        "required; valid public key pinned"
        if policy == "required" and valid
        else "required; public key missing or invalid"
        if policy == "required"
        else "optional policy; this does not establish signed approvals",
        warning=policy != "required",
    )

    imap_fields = ("IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD")
    gmail_fields = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
    if intake == "auto":
        intake = (
            "imap"
            if any(value(k) for k in imap_fields)
            else "gmail"
            if any(value(k) for k in gmail_fields)
            else "manual"
        )
    imap_ok, imap_port = False, None
    if intake == "imap":
        imap_ok = require(imap_fields, "IMAP intake")
        imap_port = port("IMAP_PORT", 993)
        tls = os.environ.get("IMAP_SSL", "1").strip().lower() not in {"0", "false", "no"}
        record("IMAP TLS", tls, "implicit TLS required for a real mailbox")
        imap_ok = imap_ok and imap_port is not None and tls
    elif intake == "gmail":
        require(gmail_fields, "Gmail intake")
        record(
            "Gmail live access", False, "OAuth access and inbox ingestion still need a real-account pilot", warning=True
        )
    else:
        record("Manual intake", True, "paste/import is available; automatic mailbox intake not selected")

    smtp_requested = os.environ.get("DESK_SMTP_SEND", "").strip() == "1"
    smtp_ok, smtp_port = False, None
    if smtp_requested:
        smtp_ok = require(("SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"), "SMTP configuration")
        smtp_port = port("SMTP_PORT", 465)
        smtp_ok = smtp_ok and smtp_port is not None
    else:
        record("SMTP delivery", False, "disabled; approval is recorded without transmitting email", warning=True)

    if probe_db:
        path = Path(db_path()).resolve()
        if not path.is_file():
            record("Database write probe", False, "database does not exist; start the configured service first")
        else:
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)) as conn:
                    _verify(conn)
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS _readiness_probe (id INTEGER PRIMARY KEY CHECK (id=1), at TEXT)"
                        )
                        conn.execute(
                            "INSERT INTO _readiness_probe(id,at) VALUES(1,'operator-probe') ON CONFLICT(id) DO UPDATE SET at=excluded.at"
                        )
                    finally:
                        conn.rollback()
                record(
                    "Database write probe",
                    True,
                    "rolled-back write succeeded; persistence across redeploy is unverified",
                )
            except (OSError, sqlite3.Error, ValueError) as exc:
                record("Database write probe", False, "probe failed: " + type(exc).__name__)
    else:
        record(
            "Database write probe",
            False,
            "not requested; use --probe-db against the intended existing database",
            warning=True,
        )

    if connect and smtp_ok:
        import smtplib

        try:
            # Match EmailStdTransport's current implicit-TLS send path.
            with smtplib.SMTP_SSL(
                value("SMTP_HOST"), smtp_port, timeout=10, context=ssl.create_default_context()
            ) as client:
                client.login(value("EMAIL_USERNAME"), value("EMAIL_PASSWORD"))
            record("SMTP authentication", True, "authenticated; no message sent and delivery not verified")
        except Exception as exc:
            record("SMTP authentication", False, "authentication failed: " + type(exc).__name__)
    if connect and imap_ok:
        import imaplib

        try:
            with imaplib.IMAP4_SSL(
                value("IMAP_HOST"), imap_port, ssl_context=ssl.create_default_context(), timeout=10
            ) as client:
                client.login(value("IMAP_USERNAME"), value("IMAP_PASSWORD"))
            record("IMAP authentication", True, "authenticated; no mailbox selected or messages read")
        except Exception as exc:
            record("IMAP authentication", False, "authentication failed: " + type(exc).__name__)
    if not connect:
        record(
            "External authentication", False, "not tested; --connect explicitly enables login-only probes", warning=True
        )

    registry = ROOT / "docs/constants_registry.json"
    registry_count = len(json.loads(registry.read_text())["constants"]) if registry.is_file() else None
    return {
        "ok": not any(row["status"] == "FAIL" for row in checks),
        "intake": intake,
        "checks": checks,
        "registry_count": registry_count,
        "production_live_verified": False,
        "mail_delivery_verified": False,
        "scope": "configuration and explicitly requested probes; real supplier pilot and redeploy persistence remain separate",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", choices=("auto", "manual", "imap", "gmail"), default="auto")
    parser.add_argument("--probe-db", action="store_true")
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = inspect_configuration(args.intake, args.probe_db, args.connect)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for row in report["checks"]:
            print(f"[{row['status']}] {row['name']}: {row['detail']}")
        print("CONFIGURATION OK" if report["ok"] else "CONFIGURATION INCOMPLETE")
        print("Real deployment, supplier delivery, and persistence are not certified by this check.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
