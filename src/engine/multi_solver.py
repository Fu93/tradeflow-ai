"""Compatibility import. The implementation lives in src.legacy.multi_solver."""
import sys

import src.legacy.multi_solver as _impl

sys.modules[__name__] = _impl
