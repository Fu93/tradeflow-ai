"""Compatibility import. The implementation lives in src.legacy.model."""
import sys

import src.legacy.model as _impl

sys.modules[__name__] = _impl
