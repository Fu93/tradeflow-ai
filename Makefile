PY ?= python

.PHONY: install test figures cover pitch video clean engine help

help:
	@echo "TradeFlow AI — offline make targets"
	@echo "  make install   install dependencies"
	@echo "  make test      run all 4 test suites"
	@echo "  make engine    run 5-round negotiation demo"
	@echo "  make figures   generate 5 demo figures + bench.json"
	@echo "  make cover     generate 1280x720 submission cover"
	@echo "  make pitch     build 10-slide pitch.pptx"
	@echo "  make video     render demo_video.mp4 (no GPU required)"
	@echo "  make server    start FastAPI on :8000"
	@echo "  make clean     remove generated results"

install:
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install fastapi httpx imageio imageio-ffmpeg

test:
	$(PY) tests/test_engine.py
	$(PY) tests/test_evolus.py
	$(PY) tests/test_benchmark.py
	$(PY) tests/test_api.py

engine:
	PYTHONUTF8=1 $(PY) -m src.cli

figures:
	PYTHONUTF8=1 $(PY) -m src.cli --figures

cover:
	PDF_CHARSET=utf-8 $(PY) scripts/make_cover.py

pitch:
	PDF_CHARSET=utf-8 $(PY) scripts/make_pitch.py

video:
	PDF_CHARSET=utf-8 $(PY) scripts/make_demo_video.py

server:
	uvicorn src.app.main:app --host 0.0.0.0 --port 8000

clean:
	rm -f results/*.png results/*.json results/*.mp4 results/*.gif results/*.pptx
	rm -rf results/_frames results/pitch_preview
