"""Live-readiness self-check: can this desk actually run in production?

This is the honest gate between "the demo works" and "the desk is live".
It checks the configuration a real deployment needs (keys, mail channels,
audit chain, approval mode), reports the data-provenance reality, and
prints a verdict. It does not connect anywhere unless --connect is given.

Verdicts:
  LIVE-READY       every hard requirement is met
  NOT LIVE-READY   at least one hard requirement failed (reasons listed)

Data provenance is printed regardless of verdict. It is the boundary
note in executable form: no constant is hard-coded without a row in the
registry, and the registry says what each number IS (policy default,
guard, or mathematical identity) — none of them is a measured factory
quote. That is a property of the prototype and stays true in production;
calibration to real deals is the pilot's job, not this script's.

Usage:
  python scripts/check_live_readiness.py            # config-only
  python scripts/check_live_readiness.py --connect  # + SMTP/IMAP logins
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "__FILL_ME__"

RESULTS: list[tuple[str, str, str]] = []  # (name, status, detail)


def check(name: str, ok: bool, detail: str, warn: bool = False) -> bool:
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    RESULTS.append((name, status, detail))
    print(f"  [{status}] {name} -- {detail}")
    return ok or warn


def _set(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and value != PLACEHOLDER:
            return name
    return ""


def env_checks() -> None:
    print("== Hard requirements ==")
    key = os.environ.get("TRADEFLOW_API_KEY", "").strip()
    check(
        "API key",
        bool(key) and key != PLACEHOLDER and len(key) >= 16,
        "TRADEFLOW_API_KEY of 16+ chars" if key else "TRADEFLOW_API_KEY is unset",
    )
    chain = os.environ.get("TRADEFLOW_CHAIN_KEY", "").strip()
    check(
        "Audit chain key",
        bool(chain) and chain != PLACEHOLDER,
        "HMAC chain key present" if chain else "TRADEFLOW_CHAIN_KEY is unset; desk sends refuse to start",
    )
    intake = _set("IMAP_HOST", "GMAIL_CLIENT_ID")
    check(
        "Mail intake channel",
        bool(intake),
        f"{intake} configured" if intake else "neither IMAP_* nor GMAIL_* is set; quotes cannot arrive",
    )
    smtp = os.environ.get("DESK_SMTP_SEND", "").strip()
    if smtp == "1":
        have_host = _set("SMTP_HOST")
        have_creds = _set("EMAIL_USERNAME", "EMAIL_PASSWORD")
        check(
            "Mail out (SMTP) enabled",
            have_host == "SMTP_HOST" and bool(have_creds),
            "DESK_SMTP_SEND=1 with SMTP credentials"
            if have_host == "SMTP_HOST" and have_creds
            else "DESK_SMTP_SEND=1 but SMTP credentials are incomplete",
        )
    else:
        check(
            "Mail out (SMTP)",
            False,
            "DESK_SMTP_SEND is not 1: sends are recorded, not transmitted (sandbox)",
            warn=True,
        )
    pubkey = os.environ.get("TRADEFLOW_APPROVAL_PUBKEY", "").strip()
    check(
        "Human approval mode",
        bool(pubkey) and pubkey != PLACEHOLDER,
        "Ed25519 signed approvals enforced (non-repudiable)" if pubkey
        else "TRADEFLOW_APPROVAL_PUBKEY unset: approvals are the server-side human_confirmed flag",
        warn=not pubkey,
    )


def db_check() -> None:
    print("== Persistence ==")
    sys.path.insert(0, str(ROOT))
    from src.desk.store import DeskStore, db_path

    path = db_path()
    ok = True
    detail = f"desk DB at {path} is writable"
    try:
        store = DeskStore(path)
        store.close()
    except Exception as exc:  # pragma: no cover - environment-specific
        ok = False
        detail = f"cannot open desk DB at {path}: {exc}"
    check("Desk database", ok, detail)


def provenance_section() -> None:
    print("== Data provenance (read this before claiming anything) ==")
    registry = json.loads((ROOT / "docs" / "constants_registry.json").read_text(encoding="utf-8"))
    counts = {"c": 0, "d": 0, "n": 0}
    for row in registry["constants"].values():
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    print(
        f"  173-slot registry: {counts['c']} cited mathematical identities, "
        f"{counts['d']} labelled policy defaults, {counts['n']} numerical guards. "
        "No measured factory number is in the code."
    )
    print(
        "  Walk-away hazard: a real eBay Best Offer 2012-13 fit, transferred to "
        "factory negotiation and labelled as such. Not a measured Chinese factory rate."
    )
    print(
        "  Intent model: trained on a programmatic synthetic set (66 templates, "
        "500 items); held-out accuracy 63.08% is a synthetic-set figure."
    )
    print(
        "  The only numbers that become real in production are the ones a person "
        "confirms on the desk. Calibration to live deals is the pilot's job."
    )


def connect_checks() -> None:
    print("== Connection tests (--connect) ==")
    host = os.environ.get("SMTP_HOST", "").strip()
    if host and host != PLACEHOLDER:
        import smtplib

        try:
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as client:
                client.starttls()
                client.login(os.environ.get("EMAIL_USERNAME", ""), os.environ.get("EMAIL_PASSWORD", ""))
            check("SMTP login", True, f"authenticated against {host}")
        except Exception as exc:
            check("SMTP login", False, f"{host}: {exc}")
    else:
        check("SMTP login", False, "SMTP_HOST not configured", warn=True)
    imap_host = os.environ.get("IMAP_HOST", "").strip()
    if imap_host and imap_host != PLACEHOLDER:
        import imaplib

        try:
            with imaplib.IMAP4_SSL(imap_host, int(os.environ.get("IMAP_PORT", "993"))) as client:
                client.login(os.environ.get("IMAP_USERNAME", ""), os.environ.get("IMAP_PASSWORD", ""))
            check("IMAP login", True, f"authenticated against {imap_host}")
        except Exception as exc:
            check("IMAP login", False, f"{imap_host}: {exc}")
    else:
        check("IMAP login", False, "IMAP_HOST not configured", warn=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="Also test real SMTP/IMAP logins")
    args = parser.parse_args(argv)

    print("TradeFlow live-readiness self-check")
    env_checks()
    db_check()
    if args.connect:
        connect_checks()
    provenance_section()

    hard_fail = any(status == "FAIL" and name not in ("Mail out (SMTP)",) for name, status, _ in RESULTS)
    # Mail out is a mode choice, not a defect: sandbox sends are recorded.
    print("-" * 70)
    if hard_fail:
        print("VERDICT: NOT LIVE-READY. Fix the [FAIL] rows above; nothing else counts.")
        return 1
    print("VERDICT: LIVE-READY on configuration. Real usage still needs one live pilot: a real")
    print("mailbox, a real supplier thread, and a human on the send button.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
