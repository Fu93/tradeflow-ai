"""Submission regressions: enforce constraints on issued money, not optimiser flags."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.desk import service
from src.desk.decide import issued_offer
from src.desk.store import DeskStore
from src.desk.verify import _guard_send
from src.engine.decision import choose_offer
from src.engine.hazard_fit import incremental_hazard, load_hazard_fit
from src.engine.reserve import ReserveState


@pytest.mark.parametrize('ask,outside', [(30, 25), (30, 20), (30, 10), (15, 13)])
@pytest.mark.parametrize('cap', [0, .001, .01])
def test_infeasible_rival_price_never_produces_a_counter(ask, outside, cap):
    choice = choose_offer(ask=ask, outside=outside, risk_cap=cap)
    assert choice.blocked
    assert issued_offer(choice, 40, ask, 'default')[0] is None
    assert issued_offer(choice, 40, ask, 'push')[0] is None


@pytest.mark.parametrize('ask', [3.85, 12.34, 27.8, 30])
@pytest.mark.parametrize('cap', [0, .0001, .01, .05, .5])
def test_issued_minor_units_stay_in_the_risk_and_price_interval(ask, cap):
    fit = load_hazard_fit()
    choice = choose_offer(ask=ask, outside=ask, risk_cap=cap, state=ReserveState.policy().with_reference(ask))
    for mode in ('default', 'push'):
        amount, _ = issued_offer(choice, ask, ask, mode)
        assert amount is not None
        assert amount <= ask
        assert incremental_hazard(ask, amount, 0, fit) <= cap + 1e-9


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADEFLOW_CHAIN_KEY', 'local-regression-chain')
    for key in ('TRADEFLOW_LLM_API_KEY', 'OPENAI_API_KEY', 'NVIDIA_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    store = DeskStore(str(tmp_path / 'desk.db'))
    snap = service.create_deal(
        store, product='umbrella', quantity=1000, target_price=20, ceiling_price=40,
        risk_tolerance=.5, seed=11, currency='CNY',
        suppliers=[{'name': n, 'address': f'{n.lower()}@example.test', 'channel': 'email'} for n in 'ABCD'],
    )
    yield store, snap['deal']['id'], {r['name']: r['id'] for r in snap['suppliers']}
    store.close()


def quote(desk, name, price, **parts):
    store, deal_id, ids = desk
    event = service.ingest_message(
        store, deal_id, source='paste', raw_text=f'单价 {price:.2f} 元/把。', supplier_id=ids[name], **parts,
    )['event']
    return service.accept_review(store, deal_id, event['id'], ids[name], chosen_token=f'{price:.2f}')


def test_nonzero_duty_counter_lands_at_the_rival_and_send_guard_agrees(desk):
    store, deal_id, ids = desk
    quote(desk, 'A', 30, freight=0, fees=0, duty_rate=.2)
    snapshot = quote(desk, 'B', 25, freight=0, fees=0, duty_rate=0)
    card = next(r for r in snapshot['suppliers'] if r['id'] == ids['A'])
    assert card['counter'] == 20.83
    assert card['counter'] * 1.2 <= 25
    draft = next(d for d in store.drafts(deal_id) if d['supplier_id'] == ids['A'] and d['status'] == 'draft')
    assert _guard_send(store, draft, store.get_deal(deal_id))['counter'] == 20.83


def test_agreed_landed_headline_recomputes_duty_on_the_agreed_price(desk):
    store, deal_id, ids = desk
    quote(desk, 'A', 30, freight=0, fees=0, duty_rate=.2)
    quote(desk, 'B', 25, freight=0, fees=0, duty_rate=0)
    snapshot = service.record_outcome(store, deal_id, ids['A'], outcome='agreed', agreed_price=20.83)
    assert snapshot['headline']['agreed_landed'] == pytest.approx(20.83 * 1.2)
    assert snapshot['headline']['best_first_landed'] == 25


def test_mixed_cost_bases_do_not_claim_a_realised_saving(desk):
    store, deal_id, ids = desk
    quote(desk, 'A', 30, freight=0, fees=0, duty_rate=.2)
    quote(desk, 'B', 25)
    snapshot = service.record_outcome(store, deal_id, ids['A'], outcome='agreed', agreed_price=20.83)
    assert not snapshot['headline']['counted']


def test_hazard_assumption_change_withholds_an_unsafe_old_counter_and_its_draft(desk):
    store, deal_id, ids = desk
    quote(desk, 'A', 30)
    quote(desk, 'B', 25)
    snapshot = service.set_assumptions(store, deal_id, {'hazard_quote_gap': 100})
    card = next(r for r in snapshot['suppliers'] if r['id'] == ids['A'])
    assert card['counter'] is None and card['push_counter'] is None
    assert not any(d['status'] == 'draft' and d['supplier_id'] == ids['A'] for d in store.drafts(deal_id))


def test_record_checks_actual_hazard_even_when_caller_claims_it_passed(desk):
    from src.desk.record import build_record

    store, deal_id, ids = desk
    quote(desk, 'A', 30)
    draft = next(d for d in store.drafts(deal_id) if d['supplier_id'] == ids['A'])
    record = store.get_decision(draft['decision_id'])['record']
    args = {k: record[k] for k in (
        'ask', 'ceiling', 'prior_alpha', 'prior_beta', 'prior_n', 'prior_source', 'unconfirmed',
        'reserve_upper', 'reference_ask',
    )}
    built = build_record(
        **args, deal_id=deal_id, supplier_id=ids['A'], currency='CNY', events=[],
        offer=25, counter=25, push_counter=25, half=25, double=25,
        risk_cap=.01, hazard_within_cap=True, inputs_stored=True,
    )
    assert built.counter is None
    assert not next(c for c in built.checks if c.name == 'hazard_within_cap').passed


def test_walked_away_supplier_has_no_rank_and_does_not_change_active_basis(desk):
    store, deal_id, ids = desk
    quote(desk, 'A', 30, freight=0, fees=0, duty_rate=.2)
    quote(desk, 'B', 25)
    snapshot = service.record_outcome(store, deal_id, ids['B'], outcome='walk_away', offer=25)
    a = next(r for r in snapshot['suppliers'] if r['id'] == ids['A'])
    b = next(r for r in snapshot['suppliers'] if r['id'] == ids['B'])
    assert b['walked_away'] and b['rank'] is None and b['counter'] is None
    assert a['rank'] == 1
    assert snapshot['ranking_basis'] == 'landed_cost'


@pytest.mark.parametrize('cap,expected_calls', [(1, 1), (2, 2), (3, 3)])
def test_paid_cap_counts_every_retry_and_optional_provider_attempt(monkeypatch, cap, expected_calls):
    from src.desk import extract
    from src.engine.llm_config import model_calls_today

    monkeypatch.setenv('TRADEFLOW_LLM_API_KEY', 'fake-nebius-key')
    monkeypatch.setenv('NVIDIA_API_KEY', 'fake-nvidia-key')
    monkeypatch.setenv('TRADEFLOW_LLM_DAILY_CALLS', str(cap))
    with patch.object(extract, '_execute_hop', return_value=({'mentions': []}, 'timeout', .01, '')) as call:
        result = extract.extract_mentions('单价 28 元/把。')
    assert call.call_count == expected_calls
    assert model_calls_today()['used'] == expected_calls
    assert len(result['attempts']) == expected_calls
    if cap < 3:
        assert result['error'] == 'extractor_daily_cap'
