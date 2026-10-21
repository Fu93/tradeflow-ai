"""Gold tests: Ed25519 human approval signatures (accountability gate).

Four properties under test:
1. A valid keypair signs and verifies over the exact reviewed payload.
2. A tampered payload, a wrong key, or malformed material never verifies.
3. A server holding the HMAC chain key cannot forge a human approval:
   a forged signature block is refused and no chain entry is appended.
4. When TRADEFLOW_APPROVAL_PUBKEY is configured, unsigned sends are
   refused (fail-closed), and a correctly signed send is recorded with
   the signature inside the audit chain entry.
"""
from __future__ import annotations

import secrets

import pytest


def _deal(store, currency="CNY"):
    from src.desk.service import create_deal

    return create_deal(
        store,
        product="Widget",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        currency=currency,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )


def _reviewed_draft(store):
    """Drive a deal to one reviewable draft, mirroring the p0-spine path."""
    from src.desk.service import accept_review, ingest_message

    opened = _deal(store)
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingested = ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    accepted = accept_review(store, deal_id, ingested["event"]["id"], supplier, chosen_token="14.80")
    draft = next(item for item in store.drafts(deal_id) if item["status"] == "draft")
    return deal_id, draft, accepted


def _send(store, draft, approval=None):
    import asyncio

    from src.desk.service import draft_digest, send_draft

    return asyncio.run(
        send_draft(
            store,
            draft["id"],
            human_approved=True,
            body_sha256=draft_digest(draft["body"]),
            approval=approval,
        )
    )


class TestSignVerifyRoundTrip:
    def test_keypair_signs_and_verifies(self):
        from src.desk.approval_signing import (
            sign_approval,
            signed_payload,
            verify_signed_approval,
        )

        private_hex, public_hex = _keypair()
        payload = signed_payload("deal-1", "draft-1", "a" * 64)
        approval = sign_approval(private_hex, **payload)
        assert approval["alg"] == "ed25519"
        assert approval["pubkey"] == public_hex
        assert verify_signed_approval(approval, payload) is True

    def test_tampered_payload_never_verifies(self):
        from src.desk.approval_signing import sign_approval, signed_payload, verify_signed_approval

        private_hex, _ = _keypair()
        payload = signed_payload("deal-1", "draft-1", "a" * 64)
        approval = sign_approval(private_hex, **payload)
        tampered = signed_payload("deal-1", "draft-1", "b" * 64)
        assert verify_signed_approval(approval, tampered) is False
        other_draft = signed_payload("deal-1", "draft-2", "a" * 64)
        assert verify_signed_approval(approval, other_draft) is False

    def test_wrong_key_and_malformed_material_never_verify(self, monkeypatch):
        from src.desk.approval_signing import (
            APPROVAL_PUBKEY_ENV,
            sign_approval,
            signed_payload,
            verify_signed_approval,
        )

        private_hex, _ = _keypair()
        _other_private, other_public = _keypair()
        payload = signed_payload("deal-1", "draft-1", "a" * 64)
        approval = sign_approval(private_hex, **payload)
        # A configured required pubkey that is not the signer's key fails.
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, other_public)
        assert verify_signed_approval(approval, payload) is False
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        for broken in (
            {"alg": "ed25519", "pubkey": approval["pubkey"], "sig": "z" * 128},
            {"alg": "hmac-sha256", "pubkey": approval["pubkey"], "sig": approval["sig"]},
            {"alg": "ed25519", "pubkey": "not-hex-" + "a" * 56, "sig": approval["sig"]},
            {"alg": "ed25519", "pubkey": approval["pubkey"], "sig": "short"},
            {},
        ):
            assert verify_signed_approval(broken, payload) is False

    def test_required_pubkey_pins_the_approver(self, monkeypatch):
        from src.desk.approval_signing import (
            APPROVAL_PUBKEY_ENV,
            sign_approval,
            signed_payload,
            verify_signed_approval,
        )

        private_hex, public_hex = _keypair()
        payload = signed_payload("deal-1", "draft-1", "a" * 64)
        approval = sign_approval(private_hex, **payload)
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public_hex)
        assert verify_signed_approval(approval, payload) is True


def _keypair():
    from src.desk.approval_signing import generate_keypair

    return generate_keypair()


class TestServerCannotForge:
    def test_forged_signature_block_is_refused_and_no_entry_appended(self, tmp_path):
        from src.desk.approval_signing import signed_payload
        from src.desk.service import SendRefused
        from src.desk.store import DeskStore

        store = DeskStore(str(tmp_path / "desk.db"))
        deal_id, draft, _ = _reviewed_draft(store)
        forged = {
            "alg": "ed25519",
            "pubkey": "a" * 64,
            "sig": secrets.token_hex(64),
        }
        with pytest.raises(SendRefused, match="signature does not verify"):
            _send(store, draft, approval=forged)
        after = [item["type"] for item in store.verify_chain(deal_id)["entries"]]
        assert "send_approved" not in after
        store.close()


class TestFailClosedAndRecording:
    def test_unsigned_send_still_works_when_no_pubkey_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRADEFLOW_APPROVAL_PUBKEY", raising=False)
        from src.desk.store import DeskStore

        store = DeskStore(str(tmp_path / "desk.db"))
        deal_id, draft, _ = _reviewed_draft(store)
        updated = _send(store, draft, approval=None)
        assert updated["status"] in {"approved", "sent"}
        store.close()

    def test_valid_signature_is_recorded_inside_the_chain_entry(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRADEFLOW_APPROVAL_PUBKEY", raising=False)
        from src.desk.approval_signing import sign_approval, signed_payload
        from src.desk.service import draft_digest
        from src.desk.store import DeskStore

        store = DeskStore(str(tmp_path / "desk.db"))
        deal_id, draft, _ = _reviewed_draft(store)
        private_hex, _ = _keypair()
        payload = signed_payload(deal_id, draft["id"], draft_digest(draft["body"]))
        approval = sign_approval(private_hex, **payload)
        updated = _send(store, draft, approval=approval)
        assert updated["status"] in {"approved", "sent"}
        entries = store.verify_chain(deal_id)["entries"]
        approved_entry = next(item for item in entries if item["type"] == "send_approved")
        recorded = approved_entry["payload"]["approval"]
        assert recorded["alg"] == "ed25519"
        assert recorded["pubkey"] == approval["pubkey"]
        assert recorded["sig"] == approval["sig"]
        store.close()

    def test_required_pubkey_refuses_unsigned_send(self, tmp_path, monkeypatch):
        from src.desk.store import DeskStore

        store = DeskStore(str(tmp_path / "desk.db"))
        deal_id, draft, _ = _reviewed_draft(store)
        private_hex, public_hex = _keypair()
        monkeypatch.setenv("TRADEFLOW_APPROVAL_PUBKEY", public_hex)
        from src.desk.service import SendRefused

        with pytest.raises(SendRefused, match="requires a signed approval"):
            _send(store, draft, approval=None)
        entries = store.verify_chain(deal_id)["entries"]
        assert "send_approved" not in [item["type"] for item in entries]
        store.close()

    def test_required_pubkey_accepts_signature_from_that_key(self, tmp_path, monkeypatch):
        from src.desk.store import DeskStore

        store = DeskStore(str(tmp_path / "desk.db"))
        deal_id, draft, _ = _reviewed_draft(store)
        private_hex, public_hex = _keypair()
        monkeypatch.setenv("TRADEFLOW_APPROVAL_PUBKEY", public_hex)
        from src.desk.approval_signing import sign_approval, signed_payload
        from src.desk.service import draft_digest

        payload = signed_payload(deal_id, draft["id"], draft_digest(draft["body"]))
        approval = sign_approval(private_hex, **payload)
        updated = _send(store, draft, approval=approval)
        assert updated["status"] in {"approved", "sent"}
        store.close()
