# Raptor Desk: one image for the web app and the background worker.
# Dependencies are installed at build time, so nothing is fetched at runtime
# and the portal runs with the network off.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    RD_DATA_DIR=/data

RUN pip install --no-cache-dir "uv==0.12.19"

WORKDIR /app

# Install locked dependencies first so this layer caches across code changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY data/ ./data/
COPY tools/ ./tools/
COPY scripts/ ./scripts/

# Collect static files at build time. The throwaway key keeps a real secret
# out of the image; the running container creates its own in /data.
RUN RD_SECRET_KEY=collectstatic-only RD_DATA_DIR=/tmp/build-data \
    python src/manage.py collectstatic --noinput \
    && chmod +x scripts/*.sh \
    && useradd --system --uid 10001 --home-dir /app raptor \
    && mkdir -p /data \
    && chown raptor /data

USER raptor

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=12 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
