FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.13.17-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY solution ./solution
COPY data ./data
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p solution/generated reports/generated \
    && chown appuser:appuser solution/generated reports/generated
USER appuser

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "uvicorn", "solution.deployment_project.app:app", "--host", "0.0.0.0", "--port", "8000"]
