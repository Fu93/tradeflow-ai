"""Select the negotiation solver's array backend from configuration.

``TRADEFLOW_SOLVER_BACKEND``:

- ``numpy`` (default), ``cpu`` — NumPy. Every measured latency in this
  repository was produced on this path.
- ``cupy``, ``rocm``, ``gpu`` — ask for CuPy. If CuPy cannot be imported the
  solver still runs on NumPy and reports that fallback. This repository has
  not recorded a run on an AMD GPU.
- ``auto`` — CuPy when ``import cupy`` succeeds, otherwise NumPy.

An explicit ``use_gpu=True/False`` argument still wins, so existing callers
and tests keep their behaviour when they pass the flag.
"""
from __future__ import annotations

import os

_CPU = {"", "numpy", "cpu", "np"}
_GPU = {"cupy", "rocm", "gpu", "cupy-rocm"}
_AUTO = {"auto"}
_AMD_DEVICE_TOKENS = ("AMD", "INSTINCT", "RADEON", "MI300", "MI250", "MI210", "GFX")


def configured_backend_name() -> str:
    return os.getenv("TRADEFLOW_SOLVER_BACKEND", "numpy").strip().lower()


def configured_use_gpu() -> bool:
    """Whether the solver should *request* a GPU backend.

    Unknown values stay on NumPy. A typo must not silently move the decision
    core onto an unmeasured device.
    """
    name = configured_backend_name()
    if name in _CPU:
        return False
    if name in _GPU:
        return True
    if name in _AUTO:
        try:
            import cupy  # noqa: F401
        except Exception:
            return False
        return True
    return False


def resolve_use_gpu(requested: bool | None) -> bool:
    if requested is None:
        return configured_use_gpu()
    return bool(requested)


def _device_name_from_cupy() -> str | None:
    import cupy as cp

    count = int(cp.cuda.runtime.getDeviceCount())
    if count < 1:
        return None
    raw = cp.cuda.runtime.getDeviceProperties(0)["name"]
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def describe_solver_backend() -> dict:
    """Runtime description safe to put in /health and benchmark evidence.

    ``ran_on_amd_gpu`` is true only when CuPy imported and the visible device
    name looks like an AMD GPU. It is not a claim that a benchmark was run.
    """
    requested = configured_backend_name() or "numpy"
    use_gpu = configured_use_gpu()
    actual = "numpy"
    device_name = None
    cupy_importable = False
    note = "NumPy on CPU. This is the backend behind the measured numbers in results/bench.json."
    if use_gpu:
        try:
            import cupy  # noqa: F401

            cupy_importable = True
            actual = "cupy"
            device_name = _device_name_from_cupy()
            note = (
                "CuPy imported. Wall-clock numbers are a measurement only for the "
                "device that actually executed the solve. This repository does not "
                "ship an AMD GPU measurement."
            )
        except Exception as exc:
            actual = "numpy"
            note = (
                f"TRADEFLOW_SOLVER_BACKEND requested a GPU backend but CuPy is not "
                f"usable ({type(exc).__name__}). The solver is on NumPy."
            )
    amd = bool(device_name) and any(tok in device_name.upper() for tok in _AMD_DEVICE_TOKENS)
    return {
        "requested": requested,
        "use_gpu": use_gpu and actual == "cupy",
        "requested_gpu": use_gpu,
        "actual": actual,
        "cupy_importable": cupy_importable,
        "device_name": device_name,
        "ran_on_amd_gpu": bool(amd and actual == "cupy"),
        "note": note,
    }
