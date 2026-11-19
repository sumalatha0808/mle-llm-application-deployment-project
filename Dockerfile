# Northstar support assistant: same image locally and on Render (Free web service).
# Build from the repository root:  docker build -t northstar .
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.13.17-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHON_DOTENV_DISABLED=1

COPY --from=uv /uv /uvx /bin/

# Locked, dev-free dependency layer (cached unless pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY src ./src
COPY data ./data

# Non-root user; trace directory is the only writable app path.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p generated && chown appuser:appuser generated
USER appuser

# Secrets (API keys, Langfuse keys) are never baked in: pass them at runtime
# (docker run -e / --env-file, or the Render dashboard).
ENV PATH="/app/.venv/bin:$PATH" \
    MODEL_PROVIDER=dry \
    TRACE_SINK=jsonl \
    TRACE_PATH=/app/generated/traces.jsonl \
    PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"

# One worker: the cache and concurrency bound are per process (see report).
# Render injects PORT; locally it defaults to 8000.
CMD ["sh", "-c", "exec uvicorn src.app:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-graceful-shutdown 10"]
