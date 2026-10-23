"""Engine package. Submodules load on first use.

This package holds the v2 decision core (``decision``, ``reserve``,
``hazard_fit``, ``numerics``) and the shared engine utilities. The v1
negotiation engines live in ``src.legacy`` and are imported from there
directly; nothing in this package re-exports them.
"""
from __future__ import annotations

import importlib

_LAZY_MODULES = {
    "viz": "src.engine.viz",
    "benchmark": "src.engine.benchmark",
    "tco": "src.engine.tco",
}

_EXPORTS = {
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
