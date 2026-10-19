"""Compatibility import. The implementation lives in src.legacy.coupled."""
import sys

import src.legacy.coupled as _impl

sys.modules[__name__] = _impl
