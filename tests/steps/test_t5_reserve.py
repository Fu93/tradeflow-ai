"""T5 reserve: a later higher ask does not move the mean up."""
from __future__ import annotations


def test_later_higher_ask_does_not_raise_the_mean():
    from src.config.heuristics import D_PRIOR_MEAN, D_PRIOR_STRENGTH
    from src.engine.reserve import ReserveState

    state = ReserveState.policy()
    assert state.alpha + state.beta == float(D_PRIOR_STRENGTH)
    assert state.mean() == float(D_PRIOR_MEAN)
    first = state.mean()
    tightened = state.tighten(0.8)
    assert tightened.upper == 0.8
    assert tightened.n_responses == state.n_responses + 1
    assert tightened.mean() < first - 1e-6
    raised = tightened.tighten(1.2)
    assert raised.upper == tightened.upper
    assert raised.mean() <= tightened.mean() + 1e-12
    user = ReserveState.from_moments(0.8, 0.1)
    assert user.mean() == 0.8
    assert abs(user.std() - 0.1) < 1e-6
