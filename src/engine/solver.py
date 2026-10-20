"""Compatibility import. The implementation lives in src.legacy.solver."""
import sys

import src.legacy.solver as _impl

sys.modules[__name__] = _impl
