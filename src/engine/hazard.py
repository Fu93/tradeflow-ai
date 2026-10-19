"""Compatibility import. The implementation lives in src.legacy.hazard."""
import sys

import src.legacy.hazard as _impl

sys.modules[__name__] = _impl
