"""Compatibility import. The implementation lives in src.legacy.orchestrator."""
import sys

import src.legacy.orchestrator as _impl

sys.modules[__name__] = _impl
