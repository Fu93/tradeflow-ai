"""Compatibility import. The implementation lives in src.legacy.proposal."""
import sys

import src.legacy.proposal as _impl

sys.modules[__name__] = _impl
