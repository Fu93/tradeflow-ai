"""Compatibility import. The implementation lives in src.legacy.logrolling."""
import sys

import src.legacy.logrolling as _impl

sys.modules[__name__] = _impl
