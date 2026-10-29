"""Independent numerical identities and audit-order decision regressions."""
from __future__ import annotations

import itertools
import math
from types import SimpleNamespace

import pytest

from src.desk import service
from src.desk.store import DeskRejected
from src.desk.verify import _guard_send
from src.engine.decision import choose_offer
from src.engine.numerics import betacf
from src.engine.reserve import ReserveState, regularized_beta
from tests.test_submission_readiness import desk as desk, quote


def current_draft(desk):
    store, deal_id, ids = desk
    return next(d for d in store.drafts(deal_id) if d['supplier_id'] == ids['A'] and d['status'] == 'draft')


@pytest.mark.parametrize('order', list(itertools.permutations([30.0, 25.0, 28.0])))
def test_confirmation_order_controls_reference_latest_ask_and_send(desk, order):
    store, deal_id, ids = desk
    pending = {}
    for price in (30.0, 25.0, 28.0):
        pending[price] = service.ingest_message(
            store, deal_id, source='paste', supplier_id=ids['A'], raw_text=f'单价 {price:.2f} 元/把。',
        )['event']['id']
    for price in order:
        snapshot = service.accept_review(store, deal_id, pending[price], ids['A'], chosen_token=f'{price:.2f}')
    card = next(r for r in snapshot['suppliers'] if r['id'] == ids['A'])
    assert card['reference_ask'] == order[0]
    assert card['latest_quote'] == order[-1]
    assert _guard_send(store, current_draft(desk), store.get_deal(deal_id))['counter'] == card['counter']


def test_clock_ties_and_descending_random_ids_cannot_undo_reopening(desk, monkeypatch):
    from src.desk import store as module

    store, deal_id, ids = desk
    quote(desk, 'A', 30)
    counter = itertools.count(10000, -1)
    stamp = store.verify_chain(deal_id)['entries'][-1]['ts']
    monkeypatch.setattr(module, '_now', lambda: stamp)
    monkeypatch.setattr(module, 'uuid', SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=f'{next(counter):032x}')))
    service.record_outcome(store, deal_id, ids['A'], outcome='walk_away', offer=25)
    snapshot = service.reopen_supplier(store, deal_id, ids['A'], human_confirmed=True)
    assert [o['outcome'] for o in store.outcomes(deal_id)] == ['walk_away', 'reopen_supplier']
    assert not next(r for r in snapshot['suppliers'] if r['id'] == ids['A'])['walked_away']
    assert _guard_send(store, current_draft(desk), store.get_deal(deal_id))['counter'] is not None


@pytest.mark.parametrize('policy', [
    {'reserve_prior_mean': .5, 'reserve_prior_std': .1},
    {'hazard_intercept': -2, 'hazard_quote_gap': 3},
    {'reserve_prior_mean': .5, 'reserve_prior_std': 1e-300, 'hazard_intercept': -3, 'hazard_quote_gap': 4},
])
def test_custom_assumptions_and_null_resets_are_replayed_by_send_guard(desk, policy):
    store, deal_id, ids = desk
    quote(desk, 'A', 30)
    service.set_assumptions(store, deal_id, policy)
    service.set_offer_mode(store, deal_id, ids['A'], 'push', human_confirmed=True)
    assert _guard_send(store, current_draft(desk), store.get_deal(deal_id))['counter'] is not None
    service.set_assumptions(store, deal_id, {key: None for key in policy})
    assert _guard_send(store, current_draft(desk), store.get_deal(deal_id))['counter'] is not None


def test_tail_interval_matches_exact_beta_one_twenty_survival():
    lower, upper, point = .9, .95, .925
    state = ReserveState(1, 20, upper=upper, reject_ratios=(lower,))
    expected = ((1-lower)**20 - (1-point)**20) / ((1-lower)**20 - (1-upper)**20)
    assert state.numerically_valid()
    assert state.cdf(point) == pytest.approx(expected, abs=1e-12)
    assert lower < state.mean() < upper
    assert 0 < state.std() < upper-lower


@pytest.mark.parametrize('lower,upper', [(.999999, .9999999), (.5, .500000001), (.01, .010000001)])
def test_narrow_uniform_interval_has_the_exact_mean_and_nonzero_spread(lower, upper):
    state = ReserveState(1, 1, upper=upper, reject_ratios=(lower,))
    assert state.mean() == pytest.approx((lower+upper)/2, abs=1e-15)
    assert state.std() == pytest.approx((upper-lower)/math.sqrt(12), rel=1e-6)
    assert state.cdf((lower+upper)/2) == pytest.approx(.5, abs=1e-6)


def test_large_symmetric_prior_obeys_symmetry():
    assert regularized_beta(.5, 500000, 500000) == pytest.approx(.5, abs=2e-9)
    assert regularized_beta(.499, 500000, 500000) < .5
    assert regularized_beta(.501, 500000, 500000) > .5


def test_tail_posterior_counter_matches_the_independent_scipy_reference():
    # SciPy betainc plus a dense bracket and bounded scalar optimisation;
    # this tail used to suffer CDF cancellation and move the counter by 3 cents.
    state = ReserveState(.5044037742256424, 97.07856610867447,
                         upper=.6499390619775727, reject_ratios=(.2600868760886805,), reference_ask=30)
    choice = choose_offer(ask=19.49817185932718, outside=15.326832329767397,
                          risk_cap=.3183232202519999, state=state)
    assert choice.push_counter == pytest.approx(8.734125548, abs=1e-5)


@pytest.mark.parametrize('spread', [1e-300, 1e300])
def test_extreme_finite_spread_does_not_overflow_or_divide_by_zero(spread):
    state = ReserveState.from_moments(.5, spread)
    assert state.numerically_valid()
    assert math.isfinite(state.std())
    assert state.mean() == pytest.approx(.5)


@pytest.mark.parametrize('alpha,beta', [(0, 1), (-1, 1), (1, 0), (1e308, 1e308), (float('nan'), 1)])
def test_invalid_or_unevaluable_belief_withholds_instead_of_crashing(alpha, beta):
    state = ReserveState(alpha, beta)
    assert math.isnan(state.mean())
    choice = choose_offer(ask=30, outside=30, state=state, risk_cap=.5)
    assert choice.blocked


def test_unconverged_continued_fraction_is_not_reported_as_an_answer():
    with pytest.raises(ArithmeticError, match='did not converge'):
        betacf(.5, .5, .49, max_iter=1)


def test_failed_objective_evaluation_withholds_the_counter(monkeypatch):
    from src.engine import decision

    monkeypatch.setattr(decision, 'objective', lambda *args, **kwargs: float('nan'))
    choice = choose_offer(ask=30, outside=30, risk_cap=.5)
    assert choice.blocked and choice.counter_relation == 'withheld'


def test_invalid_derived_bounds_and_costs_are_rejected_before_persistence(desk):
    store, deal_id, ids = desk
    before = len(store.events(deal_id))
    with pytest.raises(DeskRejected, match='finite arithmetic'):
        service.ingest_message(store, deal_id, source='paste', supplier_id=ids['A'],
                               raw_text='单价 30 元/把。', freight=1e308, fees=0, duty_rate=1e308)
    assert len(store.events(deal_id)) == before
    with pytest.raises(DeskRejected, match='stable arithmetic'):
        service.set_assumptions(store, deal_id, {'unit_sanity_multiple': 1e-320})
    assert store.get_deal(deal_id)['unit_sanity_multiple'] is None
