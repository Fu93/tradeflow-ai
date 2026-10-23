"""The round safety gate, asserted directly.

Before the extraction this was nineteen inline lines inside an 877-line handler,
which meant the safety decision could only be exercised by standing up a whole
session. The ordering below is the contract, not an implementation detail.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.app.round.circuit_breaker import RoundSafetyInput, circuit_breaker_reason


def _input(**overrides):
    base = dict(
        recommended_price=12.0,
        requires_escalation=False,
        primary_intent=None,
        tactical_rationale="",
        breakdown_probability=0.01,
        risk_tolerance=None,
        breakdown_trip_threshold=0.15,
        opponent_price=11.0,
        ceiling_price=16.0,
    )
    base.update(overrides)
    return RoundSafetyInput(**base)


class TestProceed:
    def test_a_healthy_round_is_not_blocked(self):
        assert circuit_breaker_reason(_input()) is None

    def test_a_counter_exactly_at_the_ceiling_is_not_blocked(self):
        """At the cap is inside the cap; only above it is a breach."""
        assert circuit_breaker_reason(_input(recommended_price=16.0)) is None

    def test_an_opponent_offer_exactly_at_the_ceiling_is_not_blocked(self):
        assert circuit_breaker_reason(_input(opponent_price=16.0)) is None

    def test_a_zero_opponent_price_is_not_a_breach(self):
        """`opp_price and opp_price > ceiling` — a falsy price skips the check."""
        assert circuit_breaker_reason(_input(opponent_price=0.0)) is None


class TestNoCounter:
    def test_a_missing_recommendation_blocks_with_the_only_sentence_that_notifies(self):
        reason = circuit_breaker_reason(_input(recommended_price=None))
        assert reason is not None
        assert "walk-away hazard cap" in reason

    def test_no_counter_wins_over_a_security_escalation(self):
        """There is no price to escalate about, so the reason is the price."""
        reason = circuit_breaker_reason(
            _input(recommended_price=None, requires_escalation=True, primary_intent=SimpleNamespace(value="IDENTITY_PROBE"))
        )
        assert "walk-away hazard cap" in reason


class TestSecurityEscalation:
    def test_an_escalation_beats_a_breakdown_trip(self):
        """An attack pattern is not a price problem; it is reported first."""
        intent = SimpleNamespace(value="OFFLINE_BYPASS_DIVERT")
        reason = circuit_breaker_reason(
            _input(
                requires_escalation=True,
                primary_intent=intent,
                tactical_rationale="asks to move off-platform",
                breakdown_probability=0.99,
            )
        )
        assert reason.startswith("Security escalation: OFFLINE_BYPASS_DIVERT")
        assert "asks to move off-platform" in reason

    def test_an_enum_primary_intent_is_rendered_by_value(self):
        from src.engine.bilingual import VendorTacticEnum

        reason = circuit_breaker_reason(
            _input(requires_escalation=True, primary_intent=VendorTacticEnum.OFFLINE_BYPASS_DIVERT)
        )
        assert reason.startswith("Security escalation: OFFLINE_BYPASS_DIVERT")


class TestBreakdownTrip:
    def test_a_breakdown_probability_above_the_cap_blocks(self):
        reason = circuit_breaker_reason(_input(breakdown_probability=0.4))
        assert reason is not None
        assert "40.0%" in reason
        assert "15%" in reason

    def test_the_users_tolerance_replaces_the_default_cap(self):
        reason = circuit_breaker_reason(_input(breakdown_probability=0.10, risk_tolerance=0.05))
        assert "10.0%" in reason and "5%" in reason

    def test_a_probability_below_the_cap_proceeds(self):
        assert circuit_breaker_reason(_input(breakdown_probability=0.14)) is None


class TestCeilingBreach:
    def test_an_opponent_above_the_ceiling_blocks(self):
        reason = circuit_breaker_reason(_input(opponent_price=20.0))
        assert "Opponent offer ¥20.00" in reason

    def test_the_engine_above_the_ceiling_blocks(self):
        """The last branch. If this fires, the solver and the clamp disagree."""
        reason = circuit_breaker_reason(_input(recommended_price=17.0))
        assert "Engine recommended price ¥17.00" in reason

    def test_the_engine_breach_is_reported_after_the_opponent_breach(self):
        reason = circuit_breaker_reason(_input(recommended_price=17.0, opponent_price=20.0))
        assert reason.startswith("Opponent offer")
