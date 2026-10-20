"""Compatibility import. The implementation lives in src.legacy.bayesian_v2."""
import sys

import src.legacy.bayesian_v2 as _impl

sys.modules[__name__] = _impl
