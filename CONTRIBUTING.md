# Contributing / Run locally (no GPU, no external keys)

This project runs end-to-end offline. The only external dependency is **Evolus**
(which is used as a language layer); without credentials it falls back to an
offline stub in `src/app/orchestrator.py`.

## Setup
```bash
pip install -r requirements.txt
```

## Commands
```bash
python -m src.cli                   # 5-round demo (Bayesian belief update)
python -m src.cli --robustness      # robustness report
python -m src.cli --figures         # results/*.png + bench.json
python -m src.app.orchestrator      # offline webhook self-check
python -m src.app.main              # FastAPI at http://localhost:8000
```

## Tests
```bash
python tests/test_engine.py
python tests/test_evolus.py          # mocked requests
python tests/test_benchmark.py
```

## GPU (AMD MI300X)
`src/engine/solver.solve(use_gpu=True)` tries `cupy` then falls back to numpy.
Benchmark numbers in `results/bench.json` report the MI300X figure as an
analytical **data-movement lower bound** (`mi300x_transfer_lower_bound_seconds` =
measured working set ÷ 5.3 TB/s HBM3). It is not a run-time prediction and must
not be divided by `cpu_seconds` to claim a speedup. Replace it with measured
values (under `mi300x_measured_seconds`) once a real ROCm device is available.
