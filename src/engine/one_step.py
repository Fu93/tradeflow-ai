"""Compatibility import. The implementation lives in src.legacy.one_step."""
import sys

import src.legacy.one_step as _impl

sys.modules[__name__] = _impl
