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
Benchmark numbers in `results/bench.json` use a 15× MI300X projection; replace
`mi300x_projected_seconds` with measured values once a droplet is available.
