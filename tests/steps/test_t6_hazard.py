"""T6 hazard: one module, incremental hazard is monotone, and the ask has increment zero."""
from __future__ import annotations


def test_incremental_hazard_is_zero_at_the_ask_and_monotone():
    from src.engine.hazard_fit import incremental_hazard, load_hazard_fit

    fit = load_hazard_fit()
    assert incremental_hazard(15.6, 15.6, 0.0, fit) == 0.0
    near = incremental_hazard(15.6, 15.0, 0.0, fit)
    far = incremental_hazard(15.6, 12.0, 0.0, fit)
    assert far >= near >= 0.0
