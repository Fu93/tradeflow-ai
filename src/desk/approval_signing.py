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

Server policy is fail-closed, and it is now a *policy*, not a side effect of
an environment variable being present:

- A pinned ``TRADEFLOW_APPROVAL_PUBKEY`` → signed approval is **required**.
  Pinning a key is the operator declaring the intent to enforce it.
- ``TRADEFLOW_ENV=production`` → signed approval is **required**, and the send
  gate refuses to send at all when no key is pinned. A production desk that
  accepts an unsigned boolean as proof of human approval has no
  non-repudiation property, only the appearance of one.
- ``TRADEFLOW_REQUIRE_SIGNED_APPROVAL`` overrides that either way, explicitly.
- Sandbox / demo / tests with no pinned key default to optional, so existing
  callers keep working.

The same policy, key and verification rules cover **supplier address changes**
(``address_change_policy``, ``sign_address_change``), which were previously
gated by a client-supplied ``human_confirmed`` boolean — the real
business-email-compromise path on a desk with one shared API key.

When a key *is* pinned, every send must carry a valid signature from that exact
key; unsigned sends and signatures from any other key are refused.

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
REQUIRE_SIGNED_ENV = "TRADEFLOW_REQUIRE_SIGNED_APPROVAL"
REQUIRE_ADDRESS_SIGNED_ENV = "TRADEFLOW_REQUIRE_ADDRESS_SIGNATURE"

# Values accepted by TRADEFLOW_REQUIRE_SIGNED_APPROVAL. Anything unrecognised
# falls through to the environment default, so a typo cannot silently downgrade
# a production gate.
_REQUIRED_WORDS = {"1", "true", "yes", "on", "required"}
_OPTIONAL_WORDS = {"0", "false", "no", "off", "optional"}


def generate_keypair() -> tuple[str, str]:
    """Return (private_hex, public_hex). The private half never leaves the caller."""
    signing = SigningKey.generate()
    return signing.encode().hex(), signing.verify_key.encode().hex()


def approval_policy() -> str:
    """Whether a signed human approval is mandatory: ``"required"`` or ``"optional"``.

    Resolution order, highest first:

    1. ``TRADEFLOW_REQUIRE_SIGNED_APPROVAL`` — an explicit operator decision.
    2. A pinned ``TRADEFLOW_APPROVAL_PUBKEY`` — pinning a key *is* the intent to
       enforce it. Requiring the signature whenever a key is configured keeps
       the documented contract and removes the old failure mode where the gate
       silently became optional because an environment variable was absent.
    3. ``TRADEFLOW_ENV=production`` — required. A production deployment that
       accepts an unsigned boolean as proof of human approval is not a
       non-repudiation story; it is decoration.
    4. Otherwise (sandbox / demo / tests) — optional, preserving the behaviour
       existing callers depend on.
    """
    explicit = os.environ.get(REQUIRE_SIGNED_ENV, "").strip().lower()
    if explicit in _REQUIRED_WORDS:
        return "required"
    if explicit in _OPTIONAL_WORDS:
        return "optional"
    if required_approval_pubkey() is not None:
        return "required"
    env = os.environ.get("TRADEFLOW_ENV", "sandbox").strip().lower()
    return "required" if env == "production" else "optional"


def address_change_policy() -> str:
    """Whether a supplier address change needs a signature.

    Same resolution as :func:`approval_policy`, with its own override variable:

    1. ``TRADEFLOW_REQUIRE_ADDRESS_SIGNATURE`` — an explicit operator decision.
    2. A pinned ``TRADEFLOW_APPROVAL_PUBKEY`` — pinning a key declares the intent
       to enforce it for every human decision on the desk.
    3. ``TRADEFLOW_ENV=production`` — required, for the reason below.
    4. Otherwise optional, preserving existing callers.

    Why this exists at all
    ----------------------
    A supplier address change was gated by a client-supplied
    ``human_confirmed: true`` boolean. That is not a control: any caller holding
    the API key — which is the desk's only credential, shared by every buyer in
    the organisation — could move a supplier's email to their own address and
    have the next counter sent there. That is the business-email-compromise path,
    and it does not need a broken machine, only a shared credential.

    A signature binds ``{deal_id, supplier_id, address}`` to a key that lives on
    a human's machine. The server can still refuse to send, but it can no longer
    be talked into sending somewhere new by a JSON field.
    """
    explicit = os.environ.get(REQUIRE_ADDRESS_SIGNED_ENV, "").strip().lower()
    if explicit in _REQUIRED_WORDS:
        return "required"
    if explicit in _OPTIONAL_WORDS:
        return "optional"
    if required_approval_pubkey() is not None:
        return "required"
    env = os.environ.get("TRADEFLOW_ENV", "sandbox").strip().lower()
    return "required" if env == "production" else "optional"


def signed_payload(deal_id: str, draft_id: str, body_sha256: str) -> dict[str, str]:
    """The exact fields a human approves. Sign only what the signer saw."""
    return {"deal_id": deal_id, "draft_id": draft_id, "body_sha256": body_sha256}


def _sign_block(private_hex: str, payload: dict[str, str]) -> dict[str, str]:
    """Sign one canonical payload. The only signing implementation on the desk."""
    private = bytes.fromhex(private_hex)
    signing = SigningKey(private)
    sig = signing.sign(canonical(payload)).signature.hex()
    return {
        "alg": ALG,
        "pubkey": signing.verify_key.encode().hex(),
        "sig": sig,
    }


def sign_approval(private_hex: str, deal_id: str, draft_id: str, body_sha256: str) -> dict[str, str]:
    """Sign one approval. Run this on the human's machine, not on the server."""
    return _sign_block(private_hex, signed_payload(deal_id, draft_id, body_sha256))


def required_approval_pubkey() -> str | None:
    """The pubkey that approvals must carry, when the desk enforces signed approvals."""
    value = os.environ.get(APPROVAL_PUBKEY_ENV, "").strip().lower()
    return value or None


def _verify_block(approval: Any, payload: dict[str, str]) -> bool:
    """The one place that reasons about what a valid signature block means.

    Malformed input never raises here; it only fails. When a required pubkey
    is configured, the block must be signed by exactly that key.

    [SECURITY 2026-10-23] A supplied block is always checked against the
    pinned key when one exists, and the ``pubkey`` field is never trusted as
    a claim of identity on its own: a signature by a key the server did not
    pin is a *valid signature by an unknown signer*, which is not the same
    thing as a human approval. ``approval_is_pinned`` reports that
    separately so a caller can refuse an unpinned signer explicitly instead
    of relying on the absence of a policy.

    Every human-decision signature on the desk — send approvals and supplier
    address changes alike — goes through this function, so the rules cannot
    drift between them.
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


def verify_signed_approval(approval: Any, payload: dict[str, str]) -> bool:
    """Verify one approval block against the payload and the server policy."""
    return _verify_block(approval, payload)


def approval_is_pinned(approval: Any) -> bool:
    """True when the block was signed by the key this deployment pins.

    A cryptographically valid signature from an unpinned key proves only that
    *some* key signed it. On a desk that has pinned a key, that is not an
    approval and must not be recorded as one.
    """
    if not isinstance(approval, dict):
        return False
    required = required_approval_pubkey()
    if required is None:
        return False
    return str(approval.get("pubkey") or "") == required


def address_change_payload(deal_id: str, supplier_id: str, address: str) -> dict[str, str]:
    """The exact fields a human confirms when a supplier's address changes.

    Sign only what the signer saw. The address is the whole point: a signature
    over ``{deal, supplier}`` without the destination would let a signer approve
    a *change* and an attacker choose *where to*.
    """
    return {"deal_id": deal_id, "supplier_id": supplier_id, "address": address}


def sign_address_change(private_hex: str, deal_id: str, supplier_id: str, address: str) -> dict[str, str]:
    """Sign one supplier-address change. Run this on the human's machine."""
    return _sign_block(private_hex, address_change_payload(deal_id, supplier_id, address))


def verify_address_change(approval: Any, payload: dict[str, str]) -> bool:
    """Verify one address-change block.

    Deliberately the *same* verifier as a send approval — both delegate to
    :func:`_verify_block`, so there is exactly one implementation of what a
    valid human signature means on this desk.
    """
    return _verify_block(approval, payload)



def _load_private_key(key_text: str) -> str:
    """Accept a bare hex string, or a file holding hex or a keygen JSON blob."""
    if os.path.isfile(key_text):
        with open(key_text, encoding="utf-8") as handle:
            key_text = handle.read().strip()
        try:
            key_text = json.loads(key_text)["private_key"]
        except (json.JSONDecodeError, KeyError):
            pass  # a bare hex key file is also accepted
    return key_text.strip()


def _write_out(blob: str, out: str) -> None:
    if out == "-":
        print(blob)
    else:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(blob + "\n")


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="approval-signing",
        description="Human-side Ed25519 approval key and signature tool. "
        "The private key is generated and used locally and is never sent to the server.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    keygen = sub.add_parser("keygen", help="Generate a keypair and print it. Keep the private half secret.")
    keygen.add_argument("--out", default="-", help="Create a new private key file (0600); existing files are refused.")

    sign = sub.add_parser("sign", help="Sign one approval payload read from a JSON file.")
    sign.add_argument("--key", required=True, help="Private key hex, or a file containing it.")
    sign.add_argument("--payload", required=True, help="JSON file with {deal_id, draft_id, body_sha256}.")
    sign.add_argument("--out", default="-", help="Write the approval block JSON to a file instead of stdout.")

    address = sub.add_parser(
        "sign-address",
        help="Sign one supplier address change. Run this on the human's machine.",
    )
    address.add_argument("--key", required=True, help="Private key hex, or a file containing it.")
    address.add_argument("--deal", required=True, help="Deal id.")
    address.add_argument("--supplier", required=True, help="Supplier id.")
    address.add_argument("--address", required=True, help="The new supplier email address.")
    address.add_argument("--out", default="-", help="Write the signature block JSON to a file instead of stdout.")

    args = parser.parse_args(argv)
    try:
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
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(blob + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                print(f"keypair written to {path} (private key, mode 0600)", file=sys.stderr)
            return 0
        if args.cmd == "sign":
            with open(args.payload, encoding="utf-8") as handle:
                payload = json.load(handle)
            approval = sign_approval(
                _load_private_key(args.key),
                str(payload["deal_id"]),
                str(payload["draft_id"]),
                str(payload["body_sha256"]),
            )
            _write_out(json.dumps(approval, indent=2, sort_keys=True), args.out)
            return 0
        if args.cmd == "sign-address":
            block = sign_address_change(
                _load_private_key(args.key),
                str(args.deal),
                str(args.supplier),
                str(args.address),
            )
            _write_out(json.dumps(block, indent=2, sort_keys=True), args.out)
            return 0
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        # A human runs this between review and send. A traceback on a typoed
        # key or a malformed payload file is noise at exactly the wrong moment.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
