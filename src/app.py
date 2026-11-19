"""FastAPI application: typed health, readiness and generation routes.

- ``/health``: liveness only (process answers HTTP). Never touches config or
  the provider, so Render's health check stays cheap.
- ``/ready``: 503 when local configuration is unusable (unknown provider,
  missing API key, unwritable trace path). It does NOT call the provider.
- ``/generate``: 422 for invalid input (before any provider use), 503 when not
  ready or overloaded, 504 for upstream timeout, 502 for upstream failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.config import Settings
from src.models import (
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    ReadyResponse,
)
from src.provider import Provider, ProviderTimeout, build_provider
from src.service import GenerationFailed, GenerationService, Overloaded

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _check_trace_path(settings: Settings) -> str | None:
    if settings.trace_sink != "jsonl":
        return None
    try:
        settings.trace_path.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(settings.trace_path.parent, os.W_OK):
            return "Trace directory is not writable"
    except OSError as exc:
        return f"Trace directory unavailable: {type(exc).__name__}"
    return None


def create_app(
    settings: Settings | None = None, provider: Provider | None = None
) -> FastAPI:
    problems: list[str] = []
    service: GenerationService | None = None
    try:
        settings = settings or Settings.from_env()
    except ValueError as exc:
        problems.append(f"Invalid configuration: {exc}")
        settings = Settings(trace_sink="none")
    if not problems:
        try:
            provider = provider or build_provider(settings)
            service = GenerationService(provider, settings)
        except Exception as exc:  # noqa: BLE001 - any build failure = not ready
            problems.append(f"Provider not configured: {exc}")
        if problem := _check_trace_path(settings):
            problems.append(problem)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if problems:
            logger.warning("Service not ready: %s", "; ".join(problems))
        yield
        flush = getattr(service.trace_sink if service else None, "flush", None)
        if callable(flush):
            await asyncio.to_thread(flush)

    app = FastAPI(
        title="Northstar Support Assistant", version="0.1.0", lifespan=lifespan
    )
    app.state.settings = settings
    app.state.service = service
    app.state.problems = problems

    def error(
        status: int, kind: str, detail: str, trace_id: str | None = None
    ) -> JSONResponse:
        body = ErrorResponse(error=kind, detail=detail, trace_id=trace_id)
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ReadyResponse}},
    )
    async def ready():
        body = ReadyResponse(
            status="not_ready" if problems else "ready",
            provider=settings.provider,
            model=settings.model,
            release=settings.release,
            environment=settings.environment,
            trace_sink=settings.trace_sink,
            cache="process-local",
            problems=problems,
        )
        return JSONResponse(
            status_code=503 if problems else 200, content=body.model_dump()
        )

    @app.post(
        "/generate",
        response_model=GenerateResponse,
        responses={
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            504: {"model": ErrorResponse},
        },
    )
    async def generate(request: GenerateRequest):
        if service is None:
            return error(503, "not_ready", "Service is not configured; see /ready")
        try:
            return await service.generate(request)
        except GenerationFailed as failure:
            cause, trace_id = failure.cause, failure.trace_id
            if isinstance(cause, Overloaded):
                return error(
                    503, "overloaded", "Too many concurrent requests", trace_id
                )
            if isinstance(cause, ProviderTimeout):
                return error(504, "upstream_timeout", "Provider timed out", trace_id)
            logger.warning(
                "Upstream failure %s: %s (trace %s)",
                type(cause).__name__,
                cause,
                trace_id,
            )
            return error(502, "upstream_error", "Provider unavailable", trace_id)

    return app


app = create_app()
