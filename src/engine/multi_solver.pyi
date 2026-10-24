"""Static view of the compatibility shim at src/engine/multi_solver.py.

The shim rebinds sys.modules[__name__] to the real module at import
time, which mypy cannot see. This stub gives the type checker the same
public names the runtime resolves, by re-exporting the real module's
public surface. Kept beside the shim; regenerate if the target moves.
"""

from src.legacy.multi_solver import *  # noqa: F401,F403
