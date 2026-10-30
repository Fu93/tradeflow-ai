# Pinned 2026-10-18 to the library/python 3.12-slim index digest published
# that day. Bump the digest on purpose; do not float the tag.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
COPY requirements.lock .
# An optional corporate/session CA is mounted only during dependency download.
# Normal hosting builds omit it and use the image's standard CA bundle.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir --require-hashes -r requirements.lock; \
    else \
      pip install --no-cache-dir --require-hashes -r requirements.lock; \
    fi

# `src/engine/bilingual.py` imports `src.engine.onnx_classifier` at module level,
# and that class loads `models/intent_classifier_v5.onnx` + `models/vocab.json`.
# Those files were NOT copied before, so the container started fine but silently
# dropped the on-device ONNX stage (only a log line warned). `data/` is required
# by `src/benchmark/ood_500_dataset.py`, which the test suite imports.
COPY src ./src
COPY models ./models
COPY data ./data
COPY seed_data ./seed_data
COPY knowledge ./knowledge

# Fail the build if the runtime assets the app loads are missing, instead of
# discovering it from a warning at request time.
RUN python -c "\
import os, sys; \
missing = [p for p in ('models/intent_classifier_v5.onnx', 'models/vocab.json', 'data/benchmark_ood_500.json', 'seed_data/price_anchor.json') if not os.path.exists(p)]; \
print('missing runtime assets:', missing) if missing else print('runtime assets OK'); \
sys.exit(1 if missing else 0)"

# Unbuffered stdout so container logs appear immediately.
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
# [OPS 2026-10-25] Build identity for GET /version. Stamp at build time:
#   docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
#   compose: GIT_SHA=$(git rev-parse --short HEAD) docker compose up --build
# Unstamped builds report "unknown" — honest, not fabricated.
ARG GIT_SHA=unknown
ENV TRADEFLOW_GIT_SHA=${GIT_SHA}
# Render and other hosts inject PORT. Compose publishes 8000.
# This image starts one process (`python -m src.app.main`). Do not add a
# second server command. The process reads $PORT and binds 0.0.0.0:$PORT.
ENV PORT=8000
# One CPU thread pool on a 0.5 CPU instance. These must be set before Python
# starts, or BLAS/OpenMP can spawn workers during the first solve.
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV NUMEXPR_NUM_THREADS=1

# The dashboard writes data/sessions.db and may create .env on save.
# Those paths must be writable by the runtime user.
RUN useradd --create-home --uid 10001 tradeflow \
    && mkdir -p /app/data \
    && chown -R tradeflow:tradeflow /app
USER tradeflow

EXPOSE 8000

# python:3.12-slim has no curl. GET /health with the stdlib client.
# This does not bind a port. It only connects to the one server process.
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=8 \
  CMD python -c "import os,urllib.request; p=os.environ.get('PORT','8000'); urllib.request.urlopen('http://127.0.0.1:'+p+'/health', timeout=4).read()"

CMD ["python", "-m", "src.app.main"]
