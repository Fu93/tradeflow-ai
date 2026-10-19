"""Engine package. Submodules load on first use.

The desk path imports ``decision``, ``reserve``, and ``hazard_fit`` by
name. The v1 engines live in ``src.legacy`` and are reached through the
compatibility modules in this package.
"""
from __future__ import annotations

import importlib

_LAZY_MODULES = {
    "viz": "src.engine.viz",
    "benchmark": "src.engine.benchmark",
    "model": "src.engine.model",
    "solver": "src.engine.solver",
    "robustness": "src.engine.robustness",
    "proposal": "src.engine.proposal",
    "multi_solver": "src.engine.multi_solver",
    "bayesian_v2": "src.engine.bayesian_v2",
    "tco": "src.engine.tco",
}

_EXPORTS = {
    "solve": ("src.engine.solver", "solve"),
    "recommend": ("src.engine.solver", "recommend"),
    "bayes_update": ("src.engine.solver", "bayes_update"),
    "compute_belief_confidence": ("src.engine.solver", "compute_belief_confidence"),
    "MultiSupplierSolver": ("src.engine.multi_solver", "MultiSupplierSolver"),
    "RobustBayesianInferenceEngine": ("src.engine.bayesian_v2", "RobustBayesianInferenceEngine"),
    "robust_bayes_update": ("src.engine.bayesian_v2", "robust_bayes_update"),
    "compute_calibrated_deal_prob": ("src.engine.bayesian_v2", "compute_calibrated_deal_prob"),
    "BayesianBeliefState": ("src.engine.bayesian_v2", "BayesianBeliefState"),
    "TCOEngine": ("src.engine.tco", "TCOEngine"),
    "TradeTermsSpec": ("src.engine.tco", "TradeTermsSpec"),
    "SupplierOfferedTerms": ("src.engine.tco", "SupplierOfferedTerms"),
    "TCOBreakdown": ("src.engine.tco", "TCOBreakdown"),
}


def __getattr__(name: str):
    if name in _LAZY_MODULES:
        module = importlib.import_module(_LAZY_MODULES[name])
        globals()[name] = module
        return module
    if name in _EXPORTS:
        mod_name, attr = _EXPORTS[name]
        module = importlib.import_module(mod_name)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_MODULES) + list(_EXPORTS)
