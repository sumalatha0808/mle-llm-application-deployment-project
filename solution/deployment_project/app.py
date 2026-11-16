"""Reference FastAPI application."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from groq import APITimeoutError
from httpx import TimeoutException

from solution.deployment_project.config import settings
from solution.deployment_project.models import GenerateRequest, GenerateResponse
from solution.deployment_project.provider import build_provider
from solution.deployment_project.service import GenerationService

logger = logging.getLogger(__name__)

service = GenerationService(build_provider(settings), settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    flush = getattr(service.trace_sink, "flush", None)
    if callable(flush):
        await asyncio.to_thread(flush)


app = FastAPI(title="Northstar LLM Deployment Project", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {
        "status": "ready",
        "provider": settings.provider,
        "model": service.provider.model,
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    try:
        return await service.generate(request)
    except (TimeoutError, APITimeoutError, TimeoutException) as exc:
        raise HTTPException(
            status_code=504, detail="Generation request timed out"
        ) from exc
    except Exception as exc:
        logger.exception("Generation provider failed")
        raise HTTPException(
            status_code=502,
            detail="Generation provider unavailable",
        ) from exc
