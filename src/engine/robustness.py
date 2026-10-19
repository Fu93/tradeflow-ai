"""Compatibility import. The implementation lives in src.legacy.robustness."""
import sys

import src.legacy.robustness as _impl

sys.modules[__name__] = _impl
