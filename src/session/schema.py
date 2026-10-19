"""Compatibility import. The implementation lives in src.legacy.session.schema."""
import sys

import src.legacy.session.schema as _impl

sys.modules[__name__] = _impl
