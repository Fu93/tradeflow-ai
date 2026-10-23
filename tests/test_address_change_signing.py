"""Supplier address changes need a signature, not a JSON boolean.

A supplier address change redirects where the next counter is sent. The desk
has one shared API key, so ``human_confirmed: true`` was reachable by anyone
holding that key — the business-email-compromise path, which needs no broken
machine, only a shared credential. These tests pin the control that replaced
it.
"""
from __future__ import annotations

import pytest

from src.desk.approval_signing import (
    APPROVAL_PUBKEY_ENV,
    REQUIRE_ADDRESS_SIGNED_ENV,
    address_change_payload,
    address_change_policy,
    generate_keypair,
    sign_address_change,
    verify_address_change,
)
from src.desk.store import DeskRejected


class TestPolicy:
    def test_production_requires_an_address_signature(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        monkeypatch.delenv(REQUIRE_ADDRESS_SIGNED_ENV, raising=False)
        assert address_change_policy() == "required"

    def test_pinning_a_key_requires_an_address_signature(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv(REQUIRE_ADDRESS_SIGNED_ENV, raising=False)
        _private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        assert address_change_policy() == "required"

    def test_sandbox_without_a_key_stays_optional(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        monkeypatch.delenv(REQUIRE_ADDRESS_SIGNED_ENV, raising=False)
        assert address_change_policy() == "optional"

    def test_the_override_wins_either_way(self, monkeypatch):
        monkeypatch.setenv(REQUIRE_ADDRESS_SIGNED_ENV, "required")
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        assert address_change_policy() == "required"
        monkeypatch.setenv(REQUIRE_ADDRESS_SIGNED_ENV, "optional")
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        assert address_change_policy() == "optional"


class TestSignature:
    def test_a_signature_binds_the_address_not_just_the_change(self):
        """Signing {deal, supplier} would let an attacker choose the destination."""
        private, public = generate_keypair()
        block = sign_address_change(private, "deal", "supplier", "new@example.test")
        payload = address_change_payload("deal", "supplier", "new@example.test")
        assert verify_address_change(block, payload) is True
        assert verify_address_change(block, address_change_payload("deal", "supplier", "evil@example.test")) is False

    def test_a_signature_does_not_transfer_to_another_supplier(self):
        private, _public = generate_keypair()
        block = sign_address_change(private, "deal", "supplier-a", "shared@example.test")
        assert (
            verify_address_change(block, address_change_payload("deal", "supplier-b", "shared@example.test"))
            is False
        )

    def test_an_unpinned_signer_is_not_a_confirmation(self, monkeypatch):
        pinned_private, pinned_public = generate_keypair()
        attacker_private, _attacker_public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, pinned_public)
        forged = sign_address_change(attacker_private, "deal", "supplier", "new@example.test")
        assert (
            verify_address_change(forged, address_change_payload("deal", "supplier", "new@example.test"))
            is False
        )
        genuine = sign_address_change(pinned_private, "deal", "supplier", "new@example.test")
        assert (
            verify_address_change(genuine, address_change_payload("deal", "supplier", "new@example.test"))
            is True
        )


class TestTheGate:
    def _deal(self, tmp_path, monkeypatch):
        from src.desk.service import create_deal
        from src.desk.store import DeskStore

        monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        store = DeskStore(str(tmp_path / "desk.db"))
        snapshot = create_deal(
            store,
            product="Widget",
            quantity=100,
            target_price=10.0,
            ceiling_price=12.0,
            risk_tolerance=0.15,
            seed=1,
            suppliers=[
                {"name": "Ningbo", "address": "old@example.test", "channel": "email"},
                {"name": "Wenzhou", "address": "wenzhou@example.test", "channel": "email"},
                {"name": "Yiwu", "address": "yiwu@example.test", "channel": "email"},
                {"name": "Shaoxing", "address": "shaoxing@example.test", "channel": "email"},
            ],
        )
        return store, snapshot["deal"]["id"], snapshot["suppliers"][0]["id"]

    def test_an_unsigned_change_is_refused_when_a_key_is_pinned(self, tmp_path, monkeypatch):
        from src.desk.service import confirm_supplier_address

        _private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            with pytest.raises(DeskRejected, match="signed confirmation"):
                confirm_supplier_address(
                    store, deal_id, supplier_id,
                    address="new@example.test", human_confirmed=True,
                )
        finally:
            store.close()

    def test_a_forged_signature_is_refused(self, tmp_path, monkeypatch):
        from src.desk.service import confirm_supplier_address

        pinned_private, pinned_public = generate_keypair()
        attacker_private, _public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, pinned_public)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            forged = sign_address_change(attacker_private, deal_id, supplier_id, "new@example.test")
            with pytest.raises(DeskRejected, match="signed confirmation"):
                confirm_supplier_address(
                    store, deal_id, supplier_id,
                    address="new@example.test", human_confirmed=True, address_change=forged,
                )
        finally:
            store.close()

    def test_a_valid_signature_changes_the_address_and_is_recorded(self, tmp_path, monkeypatch):
        from src.desk.service import confirm_supplier_address

        private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            block = sign_address_change(private, deal_id, supplier_id, "new@example.test")
            confirm_supplier_address(
                store, deal_id, supplier_id,
                address="new@example.test", human_confirmed=True, address_change=block,
            )
            assert store.supplier(deal_id, supplier_id)["address"] == "new@example.test"
            chain = store.verify_chain(deal_id)
            entries = [item for item in chain["entries"] if item["type"] == "supplier_address"]
            assert entries, "no supplier_address chain entry"
            recorded = entries[-1]["payload"]["address_change"]
            assert recorded["pubkey"] == public
            assert recorded["sig"] == block["sig"]
        finally:
            store.close()

    def test_a_signature_for_a_different_address_is_refused(self, tmp_path, monkeypatch):
        """The signature must cover the address actually being written."""
        from src.desk.service import confirm_supplier_address

        private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            block = sign_address_change(private, deal_id, supplier_id, "other@example.test")
            with pytest.raises(DeskRejected, match="signed confirmation"):
                confirm_supplier_address(
                    store, deal_id, supplier_id,
                    address="new@example.test", human_confirmed=True, address_change=block,
                )
        finally:
            store.close()

    def test_a_bad_signature_is_refused_even_when_the_policy_is_optional(self, tmp_path, monkeypatch):
        """Offering a signature means being held to it."""
        from src.desk.service import confirm_supplier_address

        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            with pytest.raises(DeskRejected, match="not valid"):
                confirm_supplier_address(
                    store, deal_id, supplier_id,
                    address="new@example.test", human_confirmed=True,
                    address_change={"alg": "ed25519", "pubkey": "00" * 32, "sig": "11" * 64},
                )
        finally:
            store.close()

    def test_the_boolean_alone_still_works_in_sandbox_without_a_key(self, tmp_path, monkeypatch):
        """Backwards compatibility: the documented demo path is unchanged."""
        from src.desk.service import confirm_supplier_address

        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        store, deal_id, supplier_id = self._deal(tmp_path, monkeypatch)
        try:
            confirm_supplier_address(
                store, deal_id, supplier_id, address="new@example.test", human_confirmed=True
            )
            assert store.supplier(deal_id, supplier_id)["address"] == "new@example.test"
        finally:
            store.close()
