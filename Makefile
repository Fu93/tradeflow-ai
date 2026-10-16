PY ?= python

.PHONY: install test test-legacy figures cover pitch video clean engine help

help:
	@echo "TradeFlow AI — offline make targets"
	@echo "  make install   install dependencies"
	@echo "  make test      run the FULL pytest suite (same command as CI)"
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

# [修正] 舊版 `make test` 只直接執行 4 個測試檔（test_engine/test_evolus/
# test_benchmark/test_api），而 CI 跑的是 `python -m pytest tests -q`。
# 兩者不一致，正是 .github/workflows/ci.yml 註解所抱怨的情況：本機「測試通過」
# 不代表 CI 會通過。現改為與 CI 完全相同的指令。
test:
	$(PY) -m pytest tests -q

# 保留舊的逐檔執行方式，供只改動單一模組時快速回饋。
test-legacy:
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
