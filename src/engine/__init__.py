import importlib

from . import model
from . import solver
from . import robustness
from . import proposal
from . import multi_solver
from . import bayesian_v2
from . import tco

# viz pulls matplotlib and benchmark pulls the plotting path. The web process
# does not need either at import, so they load on first attribute access.
_LAZY_MODULES = {"viz", "benchmark"}


def __getattr__(name):
    if name in _LAZY_MODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

from .solver import solve, recommend, bayes_update, compute_belief_confidence
from .multi_solver import MultiSupplierSolver
from .bayesian_v2 import (
    RobustBayesianInferenceEngine,
    robust_bayes_update,
    compute_calibrated_deal_prob,
    BayesianBeliefState,
)
from .tco import (
    TCOEngine,
    TradeTermsSpec,
    SupplierOfferedTerms,
    TCOBreakdown,
)

__all__ = [
    "model",
    "solver",
    "robustness",
    "viz",
    "benchmark",
    "proposal",
    "multi_solver",
    "bayesian_v2",
    "tco",
    "solve",
    "recommend",
    "bayes_update",
    "compute_belief_confidence",
    "MultiSupplierSolver",
    "RobustBayesianInferenceEngine",
    "robust_bayes_update",
    "compute_calibrated_deal_prob",
    "BayesianBeliefState",
    "TCOEngine",
    "TradeTermsSpec",
    "SupplierOfferedTerms",
    "TCOBreakdown",
]
