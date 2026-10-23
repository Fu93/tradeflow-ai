"""Regression tests for the 2026-10-23 product-hardening pass.

Every test here pins a defect that was reproduced against the code as it stood
before the pass, so the property cannot silently regress:

* Security — the published sandbox token was a universal API-key bypass, an
  unpinned approval signature was accepted, and the request body had no size
  limit.
* Correctness — an extreme prior std collapsed the truncated Beta so the desk
  issued the full ask, the record and the draft body disagreed about the price,
  a duplicate quote inflated the provenance label, and blocked suppliers
  reported a price of zero.
* Performance — the audit verify response carried the whole chain.

The tests are grouped by the invariant they protect, not by the file they touch.
"""
from __future__ import annotations

import json
import math
import os

import pytest
from fastapi.testclient import TestClient

from src.app import main as api_main
from src.desk import audit_chain
from src.desk.approval_signing import (
    APPROVAL_PUBKEY_ENV,
    approval_is_pinned,
    approval_policy,
    generate_keypair,
    sign_approval,
    signed_payload,
    verify_signed_approval,
)
from src.desk.money import is_quantised, quantise, quantise_offer, to_minor
from src.desk.record import build_record
from src.desk.store import DeskStore
from src.engine.decision import choose_offer
from src.engine.hazard_fit import incremental_hazard, load_hazard_fit, quote_gap, sigmoid
from src.engine.reserve import ReserveState
from src.engine.tco import TCOEngine, SupplierOfferedTerms, TradeTermsSpec

SUPPLIERS = [
    {
        "name": "Ningbo Works",
        "address": "sales@ningbo.example",
        "channel": "email",
    }
]


# ---------------------------------------------------------------------------
# Security: the published sandbox token must not be a bypass
# ---------------------------------------------------------------------------

class TestSandboxTokenIsNotABypass:
    def test_demo_token_is_rejected_when_a_real_key_is_configured(self, monkeypatch):
        """A constant in a public repo must never authenticate a configured server.

        Before: `supplied != configured and supplied != SANDBOX_DEMO_AUTH` let the
        published token through even when TRADEFLOW_API_KEY was set.
        """
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_API_KEY", "a-real-configured-key-1234")
        client = TestClient(api_main.app)
        response = client.get(
            "/api/v1/sessions",
            headers={"X-API-Key": api_main.SANDBOX_DEMO_AUTH},
        )
        assert response.status_code == 401, response.text

    def test_the_real_key_still_works(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_API_KEY", "a-real-configured-key-1234")
        client = TestClient(api_main.app)
        response = client.get(
            "/api/v1/sessions",
            headers={"X-API-Key": "a-real-configured-key-1234"},
        )
        assert response.status_code == 200, response.text

    def test_sandbox_without_a_key_stays_open_for_the_demo(self, monkeypatch):
        """The documented demo behaviour is preserved; it is only the bypass that is gone."""
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
        client = TestClient(api_main.app)
        assert client.get("/api/v1/sessions").status_code == 200


# ---------------------------------------------------------------------------
# Security: approval policy is fail-closed, not opt-in
# ---------------------------------------------------------------------------

class TestApprovalPolicy:
    def test_production_requires_a_signed_approval(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "production")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        monkeypatch.delenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", raising=False)
        assert approval_policy() == "required"

    def test_pinning_a_key_requires_the_signature(self, monkeypatch):
        """Pinning a key is the operator declaring intent; it must enforce, not decorate."""
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", raising=False)
        _private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        assert approval_policy() == "required"

    def test_sandbox_without_a_key_stays_optional(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv(APPROVAL_PUBKEY_ENV, raising=False)
        monkeypatch.delenv("TRADEFLOW_REQUIRE_SIGNED_APPROVAL", raising=False)
        assert approval_policy() == "optional"

    def test_an_unpinned_signer_is_not_an_approval(self, monkeypatch):
        """A valid signature from a key the desk does not pin proves nothing."""
        _pinned_private, pinned_public = generate_keypair()
        attacker_private, attacker_public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, pinned_public)
        payload = signed_payload("deal", "draft", "a" * 64)
        forged = sign_approval(attacker_private, "deal", "draft", "a" * 64)
        assert verify_signed_approval(forged, payload) is False
        assert approval_is_pinned(forged) is False

        genuine = sign_approval(_pinned_private, "deal", "draft", "a" * 64)
        assert verify_signed_approval(genuine, payload) is True
        assert approval_is_pinned(genuine) is True

    def test_a_signature_does_not_transfer_to_another_draft(self, monkeypatch):
        private, public = generate_keypair()
        monkeypatch.setenv(APPROVAL_PUBKEY_ENV, public)
        approval = sign_approval(private, "deal", "draft-one", "b" * 64)
        assert verify_signed_approval(approval, signed_payload("deal", "draft-two", "b" * 64)) is False


# ---------------------------------------------------------------------------
# Security: request bodies are bounded before they are buffered
# ---------------------------------------------------------------------------

class TestBodySizeLimit:
    def test_an_oversized_body_is_refused_with_413(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_MAX_REQUEST_BYTES", "1024")
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        client = TestClient(api_main.app)
        response = client.post(
            "/api/v1/tco/evaluate",
            content=b"{" + b" " * 4096 + b"}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413, response.text
        assert response.json()["limit_bytes"] == 1024

    def test_a_normal_body_still_passes(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_MAX_REQUEST_BYTES", "1048576")
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        client = TestClient(api_main.app)
        response = client.post("/api/v1/tco/evaluate", json={"raw_price": 12.0})
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Security/audit: the chain must be orderable
# ---------------------------------------------------------------------------

class TestChainTimestamps:
    def _store(self, tmp_path) -> DeskStore:
        return DeskStore(str(tmp_path / "desk.db"))

    def _deal(self, store: DeskStore) -> str:
        deal = store.create_deal(
            product="Widget", quantity=100, target_price=10.0, ceiling_price=12.0,
            risk_tolerance=0.15, seed=1, suppliers=SUPPLIERS,
        )
        return deal["id"]

    def test_a_backdated_entry_is_refused(self, tmp_path, monkeypatch):
        """A keyed MAC over a false clock is still a valid MAC, so refuse the write."""
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        store = self._store(tmp_path)
        deal_id = self._deal(store)
        store.append_chain(deal_id, "note", {"n": 1})
        with pytest.raises(audit_chain.ChainTimestampRegression):
            audit_chain.append(
                store.conn, deal_id, "note", "1999-01-01T00:00:00+00:00", {"n": 2}
            )

    def test_a_backdated_chain_fails_verification(self, tmp_path, monkeypatch):
        """The check has to exist at read time too, for rows written before the guard.

        The append-only trigger blocks a plain UPDATE, which is itself a defence.
        An attacker with database write access drops the trigger first — that is
        the scenario this test models, and it is why the read-time check exists.
        """
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        store = self._store(tmp_path)
        deal_id = self._deal(store)
        store.append_chain(deal_id, "note", {"n": 1})
        store.append_chain(deal_id, "note", {"n": 2})
        store.conn.execute("DROP TRIGGER IF EXISTS desk_chain_no_update")
        # Rewrite the second row's timestamp the way a key holder would, then
        # recompute its MAC so the link itself stays valid.
        row = store.conn.execute(
            "SELECT * FROM desk_chain WHERE deal_id = ? AND seq = 2", (deal_id,)
        ).fetchone()
        payload = json.loads(row["payload_json"])
        forged_ts = "1999-01-01T00:00:00+00:00"
        forged_mac = audit_chain.mac_with_key(
            b"test-chain-key", deal_id, 2, row["type"], forged_ts, payload, row["prev_hash"],
            row["key_id"],
        )
        store.conn.execute(
            "UPDATE desk_chain SET ts = ?, entry_hash = ? WHERE deal_id = ? AND seq = 2",
            (forged_ts, forged_mac, deal_id),
        )
        store.conn.commit()
        result = store.verify_links(deal_id)
        assert result["ok"] is False
        assert result["reason"] == "timestamp_regression"

    def test_normal_appends_verify(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        store = self._store(tmp_path)
        deal_id = self._deal(store)
        for index in range(4):
            store.append_chain(deal_id, "note", {"n": index})
        assert store.verify_links(deal_id)["ok"] is True


# ---------------------------------------------------------------------------
# Correctness: a belief that cannot be evaluated withholds the price
# ---------------------------------------------------------------------------

class TestBeliefNumerics:
    def test_an_extreme_std_does_not_collapse_the_cdf(self):
        """std=1e-5 used to give alpha+beta≈9e8 and cdf(mean)=0.0.

        That reads as "this supplier never accepts anything", so the desk issued
        the full ask as a counter and labelled it the supplier's posterior.
        """
        for std in (1e-3, 1e-4, 1e-5, 1e-6, 1e-10):
            state = ReserveState.from_moments(0.9, std)
            assert math.isfinite(state.alpha) and math.isfinite(state.beta)
            assert state.alpha + state.beta <= 1e6 + 1e-6
            assert state.cdf(0.9) == pytest.approx(0.5, abs=0.02), std
            assert state.numerically_valid() is True

    def test_a_non_finite_std_is_treated_as_no_spread(self):
        state = ReserveState.from_moments(0.9, float("nan"))
        assert math.isfinite(state.alpha)
        assert state.numerically_valid() is True

    def test_an_unevaluable_belief_blocks_the_offer(self):
        broken = ReserveState(alpha=float("nan"), beta=1.0)
        assert broken.numerically_valid() is False
        choice = choose_offer(ask=10.0, outside=12.0, state=broken, risk_cap=0.15)
        assert choice.blocked is True
        assert choice.default_counter == 0.0
        assert choice.counter_relation == "withheld"
        assert "no usable probability mass" in choice.push_note

    def test_a_healthy_belief_is_not_blocked(self):
        choice = choose_offer(ask=10.0, outside=12.0, state=ReserveState.policy(), risk_cap=0.15)
        assert choice.blocked is False
        assert 0.0 < choice.default_counter <= 10.0

    def test_a_duplicate_quote_does_not_inflate_the_provenance(self):
        """Thirty re-ingests of one quote used to count as thirty responses."""
        state = ReserveState.policy().with_reference(10.0)
        once = state.tighten(0.9)
        repeated = once
        for _ in range(30):
            repeated = repeated.tighten(0.9)
        assert repeated.n_responses == once.n_responses == 1
        assert repeated.source == "labelled_policy"

    def test_a_tighter_bound_still_counts(self):
        state = ReserveState.policy().with_reference(10.0)
        state = state.tighten(0.9).tighten(0.8)
        assert state.n_responses == 2
        assert state.upper == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Correctness: the record and the message agree about the price
# ---------------------------------------------------------------------------

class TestMoneyQuantisation:
    def test_quantise_is_half_up_not_bankers(self):
        """round(0.005) is 0 under banker's rounding, which is wrong for money."""
        assert quantise(0.005) == pytest.approx(0.01)
        assert quantise(0.015) == pytest.approx(0.02)
        assert quantise(8.125) == pytest.approx(8.13)
        assert to_minor(0.005) == 1

    def test_rounding_never_lifts_an_offer_above_the_ceiling(self):
        """A ceiling of 8.1952 used to pass the check while the body read 8.20."""
        offer = quantise_offer(8.1952, ceiling=8.1952, ask=10.0)
        assert offer == pytest.approx(8.19)
        assert offer <= 8.1952

    def test_a_half_cent_is_not_shaved_off_the_offer(self):
        offer = quantise_offer(4.1952, ceiling=20.0, ask=4.40)
        assert offer == pytest.approx(4.20)

    def test_the_body_text_matches_the_record_counter(self):
        from src.desk.project import draft_body

        counter = quantise_offer(8.196919283, ceiling=20.0, ask=9.0)
        record = build_record(
            deal_id="d", supplier_id="s", currency="CNY", events=[],
            ask=9.0, ceiling=20.0, offer=counter, counter=counter,
            push_counter=counter, half=counter, double=counter,
            prior_alpha=1.0, prior_beta=1.0, prior_n=0, prior_source="labelled_policy",
            unconfirmed=0, hazard_within_cap=True,
            risk_cap=0.15, reference_ask=9.0, inputs_stored=True,
        )
        body = draft_body("Widget", float(record.counter), 100, "zh", "CNY")
        assert f"{float(record.counter):.2f}" in body
        assert to_minor(float(record.counter)) == to_minor(8.20)

    def test_a_sub_cent_counter_is_withheld(self):
        """The check exists so a future caller cannot reintroduce the mismatch."""
        record = build_record(
            deal_id="d", supplier_id="s", currency="CNY", events=[],
            ask=9.0, ceiling=20.0, offer=8.196919283, counter=8.196919283,
            push_counter=8.196919283, half=8.196919283, double=8.196919283,
            prior_alpha=1.0, prior_beta=1.0, prior_n=0, prior_source="labelled_policy",
            unconfirmed=0, hazard_within_cap=True,
            risk_cap=0.15, reference_ask=9.0, inputs_stored=True,
        )
        assert record.counter is None
        failed = {item.name for item in record.checks if not item.passed}
        assert "counter_quantised" in failed

    def test_is_quantised_accepts_two_decimal_values(self):
        assert is_quantised(15.6) is True
        assert is_quantised(15.60) is True
        assert is_quantised(8.196919283) is False


# ---------------------------------------------------------------------------
# Performance: the audit surface does not return the whole chain by default
# ---------------------------------------------------------------------------

class TestAuditVerifyIsPaginated:
    def test_the_default_response_is_a_summary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_API_KEY", "a-real-configured-key-1234")
        db_path = str(tmp_path / "desk.db")
        monkeypatch.setenv("TRADEFLOW_DESK_DB", db_path)
        from src.desk.store import reset_stores

        reset_stores()
        store = DeskStore(db_path)
        deal = store.create_deal(
            product="Widget", quantity=100, target_price=10.0, ceiling_price=12.0,
            risk_tolerance=0.15, seed=1, suppliers=SUPPLIERS,
        )
        for index in range(5):
            store.append_chain(deal["id"], "note", {"n": index})
        store.close()
        reset_stores()

        client = TestClient(api_main.app)
        headers = {"X-API-Key": "a-real-configured-key-1234"}
        summary = client.get(f"/api/v1/desk/deals/{deal['id']}/audit/verify", headers=headers)
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["ok"] is True
        assert body["count"] == 5
        assert "entries" not in body

        window = client.get(
            f"/api/v1/desk/deals/{deal['id']}/audit/verify",
            params={"include_entries": True, "limit": 2},
            headers=headers,
        ).json()
        assert len(window["entries"]) == 2
        assert window["truncated"] is True

        reset_stores()


# ---------------------------------------------------------------------------
# Correctness: blocked suppliers do not report a price of zero
# ---------------------------------------------------------------------------

class TestBlockedOffersAreMasked:
    def test_a_blocked_supplier_is_none_not_zero(self):
        import numpy as np

        from src.legacy.coupled import solve_coupled

        inconsistent = ReserveState.policy().add_rejection(0.95).tighten(0.2)
        solved = solve_coupled(
            np.asarray([1.0], dtype=np.float64),
            np.asarray([0.9], dtype=np.float64),
            np.asarray([0.1], dtype=np.float64),
            ceiling=1.2,
            reserves=[inconsistent],
            n_samples=100,
            rng=np.random.default_rng(7),
        )
        assert solved["blocked"][0] is True
        assert solved["offers"][0] == 0.0
        assert solved["offers_masked"][0] is None

    def test_a_healthy_supplier_keeps_its_number(self):
        import numpy as np

        from src.legacy.coupled import solve_coupled

        solved = solve_coupled(
            np.asarray([1.0], dtype=np.float64),
            np.asarray([0.9], dtype=np.float64),
            np.asarray([0.1], dtype=np.float64),
            ceiling=1.2,
            reserves=[ReserveState.policy()],
            n_samples=100,
            rng=np.random.default_rng(7),
        )
        assert solved["offers_masked"][0] == pytest.approx(solved["offers"][0])


# ---------------------------------------------------------------------------
# Correctness: TCO does not crash on the documented "unset means skip"
# ---------------------------------------------------------------------------

class TestTCOUnsetBaselines:
    def test_an_unset_baseline_lead_time_skips_the_penalty(self):
        breakdown = TCOEngine.calculate_effective_price(
            TradeTermsSpec(),
            SupplierOfferedTerms(raw_unit_price=10.0, offered_lead_days=45),
        )
        assert breakdown.lead_time_penalty == 0.0

    def test_an_unset_baseline_moq_skips_the_penalty(self):
        breakdown = TCOEngine.calculate_effective_price(
            TradeTermsSpec(),
            SupplierOfferedTerms(raw_unit_price=10.0, offered_moq=5000),
        )
        assert breakdown.moq_holding_penalty == 0.0

    def test_a_zero_baseline_moq_does_not_divide_by_zero(self):
        breakdown = TCOEngine.calculate_effective_price(
            TradeTermsSpec(baseline_moq=0),
            SupplierOfferedTerms(raw_unit_price=10.0, offered_moq=5000),
        )
        assert breakdown.moq_holding_penalty == 0.0

    def test_a_deposit_with_no_lead_time_does_not_crash(self):
        breakdown = TCOEngine.calculate_effective_price(
            TradeTermsSpec(),
            SupplierOfferedTerms(raw_unit_price=10.0, deposit_pct=0.5),
        )
        assert breakdown.payment_cost_penalty == 0.0


# ---------------------------------------------------------------------------
# Performance: memoisation must not change a number
# ---------------------------------------------------------------------------

class TestHazardMemoisationIsExact:
    def test_the_increment_still_matches_the_definition(self):
        """The cached zero-gap base must equal the direct two-evaluation form."""
        fit = load_hazard_fit()
        for ask, offer, effect in (
            (10.0, 9.0, 0.0),
            (10.0, 7.5, 0.0),
            (12.0, 6.0, 0.1),
            (1.0, 1.0, 0.0),
        ):
            direct = max(
                sigmoid(fit.intercept + fit.gap_coef * quote_gap(ask, offer) + effect)
                - sigmoid(fit.intercept + effect),
                0.0,
            )
            assert incremental_hazard(ask, offer, effect, fit) == pytest.approx(direct, abs=1e-12)

    def test_repeated_calls_are_stable(self):
        fit = load_hazard_fit()
        first = [incremental_hazard(10.0, 9.0, 0.0, fit) for _ in range(5)]
        assert len(set(first)) == 1


# ---------------------------------------------------------------------------
# Readiness is separate from liveness
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_ready_reports_not_ready_without_a_chain_key(self, monkeypatch):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.delenv("TRADEFLOW_CHAIN_KEY", raising=False)
        client = TestClient(api_main.app)
        response = client.get("/ready")
        assert response.status_code == 503
        assert "chain_key" in response.json()["blocking"]

    def test_ready_reports_ready_when_every_dependency_is_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADEFLOW_ENV", "sandbox")
        monkeypatch.setenv("TRADEFLOW_CHAIN_KEY", "test-chain-key")
        monkeypatch.setenv("TRADEFLOW_DESK_DB", str(tmp_path / "desk.db"))
        from src.desk.store import reset_stores

        reset_stores()
        client = TestClient(api_main.app)
        response = client.get("/ready")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"
        reset_stores()

    def test_health_stays_cheap_and_open(self):
        client = TestClient(api_main.app)
        assert client.get("/health").status_code == 200
