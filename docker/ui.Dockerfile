# syntax=docker/dockerfile:1.7
# MarketLens AI — Streamlit UI. Build from the repo root:
#   docker build -f docker/ui.Dockerfile -t marketlens-ui .

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[ui]"

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    UI_LOG_FORMAT=json
RUN groupadd --system app && useradd --system --gid app --create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /home/app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.environ['PORT'], timeout=3)"

CMD ["sh", "-c", "exec python -m marketlens.ui --server.port ${PORT} --server.address 0.0.0.0"]
