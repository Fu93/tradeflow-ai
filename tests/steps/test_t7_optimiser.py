"""T7 optimiser: the desk counter is one call to choose_offer."""
from __future__ import annotations


def test_desk_decide_matches_choose_offer():
    from src.desk.decide import decide_supplier
    from src.engine.decision import choose_offer
    from src.engine.reserve import ReserveState

    state = ReserveState.policy().with_reference(15.60)
    choice = decide_supplier(
        ask=15.60,
        rivals=[14.0],
        ceiling=20.0,
        state=state,
        risk_cap=0.5,
    )
    direct = choose_offer(
        ask=15.60,
        outside=min(15.60, 20.0, 14.0),
        state=state,
        effect=0.0,
        risk_cap=0.5,
    )
    assert choice.default_counter == direct.default_counter
    assert direct.default_counter == 14.0
    assert direct.optimiser == "golden_section"
    assert direct.push_counter <= direct.default_counter
    assert choice.optimiser == direct.optimiser
