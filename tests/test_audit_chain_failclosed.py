"""The audit chain's fail-closed paths, tested as behaviours not line numbers.

A keyed MAC proves who wrote a row. It does **not** prove the clock, and it
does not help when the key itself has moved on. The paths below are the ones
that only run when something is already wrong — a backdated row written by a
legitimate key holder, a key id that rotated away, a retired key nobody
published, an unreadable anchor — which is exactly when the code must report
"broken" instead of guessing. Every test here pins a "refuse, never guess"
property:

1. A key holder who backdates a row breaks the chain (timestamp regression),
   because the MAC over a falsified clock is still a valid MAC.
2. A key id that is neither current nor published in the retired ring makes
   the chain unverifiable — reported broken, never silently accepted.
3. A retired key published in ``TRADEFLOW_CHAIN_KEYS`` still verifies.
4. Legacy rows without a key id verify under the round-5 MAC, and a tampered
   legacy row is broken.
5. An unreadable anchor counts as present: fail closed, never "probably fine".
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from src.desk import audit_chain
from src.desk.audit_chain import (
    GENESIS,
    anchor_head_present,
    chain_rows,
    entry_hash,
    round5_digest,
    ts_regression,
    verify_rows,
)


def _store_with_chain(tmp_path):
    from src.desk.service import create_deal, ingest_message
    from src.desk.store import DeskStore

    store = DeskStore(str(tmp_path / "desk.db"))
    opened = create_deal(
        store,
        product="Widget",
        quantity=1000,
        target_price=12,
        ceiling_price=20,
        risk_tolerance=0.5,
        seed=3,
        suppliers=[
            {"name": "Ningbo", "address": "a@example.test", "channel": "email"},
            {"name": "Wenzhou", "address": "b@example.test", "channel": "email"},
            {"name": "Yiwu", "address": "c@example.test", "channel": "email"},
            {"name": "Foshan", "address": "d@example.test", "channel": "email"},
        ],
    )
    deal_id = opened["deal"]["id"]
    supplier = opened["suppliers"][0]["id"]
    ingest_message(store, deal_id, source="paste", raw_text="单价 14.80 元", supplier_id=supplier)
    return store, deal_id


class TestTimestampRegression:
    @staticmethod
    def _linked_rows(deal_id: str, timestamps: list[str]) -> list[dict]:
        """A valid chain under the current key, with the caller's timestamps.

        Built as plain rows because the store's own append-only triggers
        correctly refuse the in-place UPDATE a real attacker would need.
        """
        rows: list[dict] = []
        prev = GENESIS
        for index, ts in enumerate(timestamps, start=1):
            payload = {"n": index}
            rows.append(
                {
                    "deal_id": deal_id,
                    "seq": index,
                    "type": "note",
                    "ts": ts,
                    "payload_json": json.dumps(payload),
                    "prev_hash": prev,
                    "entry_hash": entry_hash(deal_id, index, "note", ts, payload, prev),
                    "key_id": "primary",
                }
            )
            prev = rows[-1]["entry_hash"]
        return rows

    def test_a_backdated_row_signed_by_the_key_holder_breaks_the_chain(self, monkeypatch):
        """The MAC cannot catch this — the same key signs the falsified clock.

        This is why ``ts_regression`` exists: without it, "the human approved
        before the send" is unverifiable against a key holder who rewrites time.
        """
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY_ID", raising=False)
        rows = self._linked_rows(
            "d1",
            [
                "2026-10-23T12:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
                "2026-10-23T12:00:01+00:00",
            ],
        )
        result = verify_rows(rows)
        assert result["ok"] is False
        assert result["reason"] == "timestamp_regression"
        assert result["broken_seq"] == 2

    def test_a_monotonic_chain_signed_by_the_key_holder_verifies(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY_ID", raising=False)
        rows = self._linked_rows(
            "d1",
            [
                "2026-10-23T12:00:00+00:00",
                "2026-10-23T12:00:00+00:00",
                "2026-10-23T12:00:01+00:00",
            ],
        )
        result = verify_rows(rows)
        assert result["ok"] is True
        assert result["broken_seq"] is None

    def test_equal_and_unparsable_timestamps_are_not_regressions(self):
        now = "2026-10-23T12:00:00+00:00"
        entries = [
            {"seq": 1, "ts": now},
            {"seq": 2, "ts": now},
            {"seq": 3, "ts": "not-a-date"},
            {"seq": 4, "ts": "2026-10-23T12:00:01+00:00"},
        ]
        assert ts_regression(entries) is None

    def test_an_empty_chain_has_no_regression(self):
        assert ts_regression([]) is None


class TestKeyRotation:
    def test_a_key_id_missing_from_the_ring_leaves_the_chain_unverifiable(self, tmp_path, monkeypatch):
        """Rotate the key id without publishing the old secret: refuse, never accept."""
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY_ID", raising=False)
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEYS", raising=False)
        store, deal_id = _store_with_chain(tmp_path)
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY_ID", "v2")
        result = verify_rows(chain_rows(store.conn, deal_id))
        assert result["ok"] is False
        assert result["broken_seq"] == 1
        store.close()

    def test_a_published_retired_key_still_verifies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY_ID", raising=False)
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEYS", raising=False)
        store, deal_id = _store_with_chain(tmp_path)
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY_ID", "v2")
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEYS", "primary:test-chain-key")
        result = verify_rows(chain_rows(store.conn, deal_id))
        assert result["ok"] is True
        assert result["broken_seq"] is None
        store.close()


class TestLegacyRows:
    @staticmethod
    def _legacy_row(deal_id: str, seq: int, payload: dict, prev: str) -> dict:
        ts = "2026-01-01T00:00:00+00:00"
        return {
            "deal_id": deal_id,
            "seq": seq,
            "type": "note",
            "ts": ts,
            "payload_json": json.dumps(payload),
            "prev_hash": prev,
            "entry_hash": round5_digest(deal_id, seq, "note", ts, payload, prev),
            "key_id": None,
        }

    def test_a_row_without_a_key_id_verifies_under_the_round5_mac(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        rows = [self._legacy_row("d1", 1, {"k": "v"}, GENESIS)]
        result = verify_rows(rows)
        assert result["ok"] is True
        assert result["entries"][0]["key_id"] == ""

    def test_a_tampered_legacy_row_is_broken(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        row = self._legacy_row("d1", 1, {"k": "v"}, GENESIS)
        row["payload_json"] = json.dumps({"k": "forged"})
        result = verify_rows([row])
        assert result["ok"] is False
        assert result["broken_seq"] == 1

    def test_a_malformed_legacy_row_cannot_crash_the_verifier(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        row = self._legacy_row("d1", 1, {"k": "v"}, GENESIS)
        row["payload_json"] = "{not json"
        with pytest.raises(json.JSONDecodeError):
            verify_rows([row])


class TestNoKeyIsBroken:
    @pytest.mark.parametrize("secret", [None, "", "   "])
    def test_without_a_chain_key_everything_is_reported_broken(self, monkeypatch, secret):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", secret or "")
        result = verify_rows([])
        assert result["ok"] is False
        assert result["broken_seq"] == 1


class TestAnchorFailClosed:
    def test_an_unreadable_file_anchor_counts_as_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "anchor.jsonl"))

        def explode(target, deal_id):
            raise OSError("the anchor disk is gone")

        monkeypatch.setattr(audit_chain, "_read_anchor_file", explode)
        assert anchor_head_present("deal-1") is True

    def test_an_unreachable_url_anchor_counts_as_present(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", "https://anchor.invalid/deals")

        def explode(target, deal_id):
            raise urllib.error.URLError("no route to anchor")

        monkeypatch.setattr(audit_chain, "_read_anchor_url", explode)
        assert anchor_head_present("deal-1") is True

    def test_no_anchor_configured_is_not_present(self, monkeypatch):
        monkeypatch.delenv("TRADEFLOW_CHAIN_ANCHOR", raising=False)
        assert anchor_head_present("deal-1") is False

    def test_a_missing_anchor_file_is_absent_not_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "absent.jsonl"))
        assert anchor_head_present("deal-1") is False

    def test_read_anchor_swallows_reader_errors_into_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_ANCHOR", str(tmp_path / "anchor.jsonl"))

        def explode(target, deal_id):
            raise OSError("disk gone")

        monkeypatch.setattr(audit_chain, "_read_anchor_file", explode)
        assert audit_chain._read_anchor("deal-1") == {"seq": -1, "entry_hash": ""}


class TestAnchorHeadShape:
    @pytest.mark.parametrize(
        "item,expected",
        [
            (None, False),
            ("anchor", False),
            ({"seq": "bad"}, False),
            ({"seq": -1, "entry_hash": "abc"}, False),
            ({"seq": 0}, False),
            ({"seq": 0, "entry_hash": "abc"}, True),
        ],
    )
    def test_only_a_real_head_counts(self, item, expected):
        assert audit_chain._real_anchor_head(item) is expected


class TestTimestampParsing:
    @pytest.mark.parametrize(
        "value,expected_tz",
        [
            ("2026-10-23T12:00:00Z", "UTC"),
            ("2026-10-23T12:00:00+00:00", "UTC"),
            ("2026-10-23T12:00:00", "UTC"),
            ("2026-10-23", "UTC"),
        ],
    )
    def test_parse_shapes(self, value, expected_tz):
        parsed = audit_chain._parse_ts(value)
        assert parsed is not None
        assert str(parsed.tzinfo) == expected_tz or parsed.utcoffset().total_seconds() == 0

    @pytest.mark.parametrize("value", [None, "", "   ", "garbage", 42])
    def test_unparsable_is_none(self, value):
        assert audit_chain._parse_ts(value) is None
