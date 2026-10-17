# Contributing

The decision engine runs offline. No GPU and no API key are required.

```bash
pip install -r requirements.txt
python -m pytest tests -q
python -m src.app.main          # http://localhost:8000
```

`TRADEFLOW_SOLVER_BACKEND` selects NumPy (`numpy`, the default) or a CuPy/ROCm request (`cupy`, `rocm`, `auto`). CuPy falls back to NumPy when it is not installed. `scripts/benchmark_amd_gpu.py` records whether an AMD GPU was actually visible.

`VLLM_API_KEY` and the Evolus keys are optional. Values left as `__FILL_ME__` are ignored, so a copied `.env.example` does not call out.
