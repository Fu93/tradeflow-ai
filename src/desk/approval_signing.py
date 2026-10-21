"""Ed25519 human approval signatures for the desk send gate.

The HMAC audit chain proves the server wrote what it wrote. It cannot prove
a human approved a send: the server holds the HMAC key, so it can append
any chain entry, including a fake approval. This module adds the missing
property, accountability at the human level:

    the human signs the approval with a private Ed25519 key that never
    leaves their machine; the server verifies with the public half.

A server holding every HMAC key still cannot forge one human approval,
because it never sees a private key.

Signed payload = exactly what the approving human reviewed:
``{"deal_id", "draft_id", "body_sha256"}``. The transmission digest is
computed by the server and is recorded in the same chain entry, but it is
not part of the signature, because the signer never saw it. Sign only what
the signer saw.

Server policy is fail-closed: when ``TRADEFLOW_APPROVAL_PUBKEY`` is set,
every send must carry a valid signature from that exact key; unsigned
sends are refused. Without the variable the gate is opt-in per request,
preserving the pre-existing behaviour for existing callers.

Boundary (stated honestly): this is engineering non-repudiation for the
approval action, not legal-grade accountability. Key-to-identity binding
and trusted timestamps are out of scope here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from src.desk.audit_chain import canonical

ALG = "ed25519"
PUBKEY_HEX_LEN = 64  # 32 bytes
SIG_HEX_LEN = 128  # 64 bytes
APPROVAL_PUBKEY_ENV = "TRADEFLOW_APPROVAL_PUBKEY"


def generate_keypair() -> tuple[str, str]:
    """Return (private_hex, public_hex). The private half never leaves the caller."""
    signing = SigningKey.generate()
    return signing.encode().hex(), signing.verify_key.encode().hex()


def signed_payload(deal_id: str, draft_id: str, body_sha256: str) -> dict[str, str]:
    """The exact fields a human approves. Sign only what the signer saw."""
    return {"deal_id": deal_id, "draft_id": draft_id, "body_sha256": body_sha256}


def sign_approval(private_hex: str, deal_id: str, draft_id: str, body_sha256: str) -> dict[str, str]:
    """Sign one approval. Run this on the human's machine, not on the server."""
    private = bytes.fromhex(private_hex)
    signing = SigningKey(private)
    payload = signed_payload(deal_id, draft_id, body_sha256)
    sig = signing.sign(canonical(payload)).signature.hex()
    return {
        "alg": ALG,
        "pubkey": signing.verify_key.encode().hex(),
        "sig": sig,
    }


def required_approval_pubkey() -> str | None:
    """The pubkey that approvals must carry, when the desk enforces signed approvals."""
    value = os.environ.get(APPROVAL_PUBKEY_ENV, "").strip().lower()
    return value or None


def verify_signed_approval(approval: Any, payload: dict[str, str]) -> bool:
    """Verify one approval block against the payload and the server policy.

    Malformed input never raises here; it only fails. When a required pubkey
    is configured, the approval must be signed by exactly that key.
    """
    if not isinstance(approval, dict):
        return False
    if approval.get("alg") != ALG:
        return False
    pubkey = approval.get("pubkey")
    sig = approval.get("sig")
    if not isinstance(pubkey, str) or not isinstance(sig, str):
        return False
    if len(pubkey) != PUBKEY_HEX_LEN or len(sig) != SIG_HEX_LEN:
        return False
    required = required_approval_pubkey()
    if required is not None and pubkey != required:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(pubkey))
        verify_key.verify(canonical(payload), bytes.fromhex(sig))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="approval-signing",
        description="Human-side Ed25519 approval key and signature tool. "
        "The private key is generated and used locally and is never sent to the server.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    keygen = sub.add_parser("keygen", help="Generate a keypair and print it. Keep the private half secret.")
    keygen.add_argument("--out", default="-", help="Write the private key to a file (0600) instead of stdout.")

    sign = sub.add_parser("sign", help="Sign one approval payload read from a JSON file.")
    sign.add_argument("--key", required=True, help="Private key hex, or a file containing it.")
    sign.add_argument("--payload", required=True, help="JSON file with {deal_id, draft_id, body_sha256}.")
    sign.add_argument("--out", default="-", help="Write the approval block JSON to a file instead of stdout.")

    args = parser.parse_args(argv)
    if args.cmd == "keygen":
        private_hex, public_hex = generate_keypair()
        blob = json.dumps(
            {"alg": ALG, "private_key": private_hex, "public_key": public_hex},
            indent=2,
            sort_keys=True,
        )
        if args.out == "-":
            print(blob)
        else:
            path = os.path.abspath(args.out)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(blob + "\n")
            os.chmod(path, 0o600)
            print(f"keypair written to {path} (private key, mode 0600)", file=sys.stderr)
        return 0
    if args.cmd == "sign":
        key_text = args.key
        if os.path.isfile(key_text):
            with open(key_text, encoding="utf-8") as handle:
                key_text = handle.read().strip()
            try:
                key_text = json.loads(key_text)["private_key"]
            except (json.JSONDecodeError, KeyError):
                pass  # a bare hex key file is also accepted
        with open(args.payload, encoding="utf-8") as handle:
            payload = json.load(handle)
        approval = sign_approval(
            key_text.strip(),
            str(payload["deal_id"]),
            str(payload["draft_id"]),
            str(payload["body_sha256"]),
        )
        blob = json.dumps(approval, indent=2, sort_keys=True)
        if args.out == "-":
            print(blob)
        else:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(blob + "\n")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
