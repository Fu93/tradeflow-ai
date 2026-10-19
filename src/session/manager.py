"""Compatibility import. The implementation lives in src.legacy.session.manager."""
import sys

import src.legacy.session.manager as _impl

sys.modules[__name__] = _impl
