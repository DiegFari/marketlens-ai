# syntax=docker/dockerfile:1.7
# MarketLens AI — FastAPI service. Build from the repo root:
#   docker build -f docker/api.Dockerfile -t marketlens-api .

ARG PYTHON_VERSION=3.12

# ---- builder: install the package into an isolated virtualenv ----------------
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api]"

# ---- runtime: only the virtualenv, running as a non-root user ----------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    LOG_FORMAT=json
RUN groupadd --system app && useradd --system --gid app --no-create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health/live' % os.environ['PORT'], timeout=3)"

# PORT is honoured so App Service (WEBSITES_PORT) and local runs share one image.
CMD ["sh", "-c", "exec uvicorn marketlens.api.main:create_app --factory --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*'"]
