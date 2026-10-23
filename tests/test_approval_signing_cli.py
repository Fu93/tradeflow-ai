"""The human side of the approval gate: keygen, signing CLI, and policy words.

The server-side verify path had gold tests; the tool a human actually runs
(``python -m src.desk.approval_signing …``) and the env-var words an operator
types to flip the gate were untested. A signing tool that silently mangles a
key, or a policy that downgrades on a typo, is the same risk as a broken
verifier — the approval stops proving what it claims. These tests pin:

1. The CLI round-trips: a keygen blob signs, and the server verifier accepts
   the exact payload it was given and nothing else.
2. Key material is accepted in every documented form (hex, keygen JSON blob,
   bare-hex file) and a malformed one exits with a clean error, not a
   traceback, because this runs in a human's terminal between review and send.
3. ``TRADEFLOW_REQUIRE_SIGNED_APPROVAL`` words are matched exactly; an
   unrecognised word falls through to the environment default instead of
   silently downgrading a production gate.
4. Malformed signature blocks fail every verifier branch, and
   ``approval_is_pinned`` never mistakes an unpinned signer for an approval.
"""
from __future__ import annotations

import json

import pytest

from src.desk.approval_signing import (
    APPROVAL_PUBKEY_ENV,
    REQUIRE_SIGNED_ENV,
    _cli,
    approval_is_pinned,
    approval_policy,
    generate_keypair,
    sign_approval,
    signed_payload,
    verify_address_change,
    verify_signed_approval,
)

PAYLOAD = signed_payload("deal-1", "draft-1", "a" * 64)


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch):
    for name in (APPROVAL_PUBKEY_ENV, REQUIRE_SIGNED_ENV, "TRADEFLOW_ENV"):
        monkeypatch.delenv(name, raising=False)


class TestKeygen:
    def test_keygen_to_stdout_produces_a_working_keypair(self, capsys):
        assert _cli(["keygen"]) == 0
        blob = json.loads(capsys.readouterr().out)
        assert blob["alg"] == "ed25519"
        approval = sign_approval(blob["private_key"], **PAYLOAD)
        assert approval["pubkey"] == blob["public_key"]
        assert verify_signed_approval(approval, PAYLOAD) is True

    def test_keygen_to_a_file_writes_the_blob(self, tmp_path, capsys):
        target = tmp_path / "approval-key.json"
        assert _cli(["keygen", "--out", str(target)]) == 0
        out = capsys.readouterr()
        assert "keypair written to" in out.err
        blob = json.loads(target.read_text(encoding="utf-8"))
        assert blob["private_key"] and blob["public_key"]


class TestSignCommand:
    def test_sign_with_a_bare_hex_key(self, tmp_path, capsys):
        private_hex, public_hex = generate_keypair()
        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        assert _cli(["sign", "--key", private_hex, "--payload", str(payload_file)]) == 0
        approval = json.loads(capsys.readouterr().out)
        assert approval["pubkey"] == public_hex
        assert verify_signed_approval(approval, PAYLOAD) is True

    def test_sign_with_a_keygen_json_blob_file(self, tmp_path, capsys):
        private_hex, _ = generate_keypair()
        key_file = tmp_path / "key.json"
        key_file.write_text(
            json.dumps({"alg": "ed25519", "private_key": private_hex}), encoding="utf-8"
        )
        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        out_file = tmp_path / "approval.json"
        assert _cli(
            ["sign", "--key", str(key_file), "--payload", str(payload_file), "--out", str(out_file)]
        ) == 0
        approval = json.loads(out_file.read_text(encoding="utf-8"))
        assert verify_signed_approval(approval, PAYLOAD) is True

    def test_sign_with_a_bare_hex_key_file(self, tmp_path, capsys):
        private_hex, _ = generate_keypair()
        key_file = tmp_path / "key.hex"
        key_file.write_text(private_hex + "\n", encoding="utf-8")
        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        assert _cli(["sign", "--key", str(key_file), "--payload", str(payload_file)]) == 0
        approval = json.loads(capsys.readouterr().out)
        assert verify_signed_approval(approval, PAYLOAD) is True

    def test_sign_address_writes_a_block_the_server_verifier_accepts(self, tmp_path, capsys):
        private_hex, _ = generate_keypair()
        out_file = tmp_path / "address-change.json"
        assert _cli(
            [
                "sign-address",
                "--key", private_hex,
                "--deal", "deal-1",
                "--supplier", "supplier-1",
                "--address", "new@example.test",
                "--out", str(out_file),
            ]
        ) == 0
        block = json.loads(out_file.read_text(encoding="utf-8"))
        payload = {"deal_id": "deal-1", "supplier_id": "supplier-1", "address": "new@example.test"}
        assert verify_address_change(block, payload) is True
        assert verify_address_change(
            block, {"deal_id": "deal-1", "supplier_id": "supplier-1", "address": "evil@example.test"}
        ) is False

    def test_a_bad_key_fails_with_a_clean_error_not_a_traceback(self, capsys):
        assert _cli(["sign-address", "--key", "not-hex", "--deal", "d", "--supplier", "s",
                     "--address", "a@example.test"]) == 2
        assert "error:" in capsys.readouterr().err

    def test_a_payload_missing_fields_fails_with_a_clean_error(self, tmp_path, capsys):
        private_hex, _ = generate_keypair()
        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps({"deal_id": "deal-1"}), encoding="utf-8")
        assert _cli(["sign", "--key", private_hex, "--payload", str(payload_file)]) == 2
        assert "error:" in capsys.readouterr().err

    def test_a_missing_payload_file_fails_with_a_clean_error(self, tmp_path, capsys):
        private_hex, _ = generate_keypair()
        assert _cli(["sign", "--key", private_hex,
                     "--payload", str(tmp_path / "absent.json")]) == 2
        assert "error:" in capsys.readouterr().err


class TestPolicyWords:
    """An operator types these words; a typo must not silently downgrade the gate."""

    @pytest.mark.parametrize("word", ["1", "true", "yes", "on", "required"])
    def test_required_words_require_even_in_a_sandbox(self, monkeypatch, word):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv(REQUIRE_SIGNED_ENV, word)
        assert approval_policy() == "required"

    @pytest.mark.parametrize("word", ["0", "false", "no", "off", "optional"])
    def test_optional_words_win_even_in_production(self, monkeypatch, word):
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        monkeypatch.setenv(REQUIRE_SIGNED_ENV, word)
        assert approval_policy() == "optional"

    def test_an_unrecognised_word_falls_through_to_the_environment(self, monkeypatch):
        monkeypatch.setenv(REQUIRE_SIGNED_ENV, "maybe")
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        assert approval_policy() == "required"
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        assert approval_policy() == "optional"

    def test_an_unrecognised_word_still_honours_a_pinned_key(self, monkeypatch):
        _private, public = generate_keypair()
        monkeypatch.setenv(REQUIRE_SIGNED_ENV, "maybe")
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        assert approval_policy() == "required"

    def test_words_are_matched_case_insensitively_with_surrounding_space(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv(REQUIRE_SIGNED_ENV, "  Required ")
        assert approval_policy() == "required"


class TestMalformedBlocks:
    def test_a_non_dict_block_fails_everywhere(self):
        for broken in (None, [], "ed25519", 7):
            assert verify_signed_approval(broken, PAYLOAD) is False
            assert verify_address_change(broken, PAYLOAD) is False
            assert approval_is_pinned(broken) is False

    def test_non_string_pubkey_or_sig_fails_everywhere(self):
        private_hex, _ = generate_keypair()
        block = sign_approval(private_hex, **PAYLOAD)
        for field in ("pubkey", "sig"):
            broken = dict(block)
            broken[field] = 123
            assert verify_signed_approval(broken, PAYLOAD) is False
            assert verify_address_change(broken, PAYLOAD) is False

    def test_pinned_is_false_without_a_policy_and_true_only_for_the_pinned_key(self, monkeypatch):
        private_hex, public_hex = generate_keypair()
        block = sign_approval(private_hex, **PAYLOAD)
        assert approval_is_pinned(block) is False
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public_hex)
        assert approval_is_pinned(block) is True
        _other, other_public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, other_public)
        assert approval_is_pinned(block) is False
        assert approval_is_pinned({"alg": "ed25519", "pubkey": None, "sig": None}) is False
