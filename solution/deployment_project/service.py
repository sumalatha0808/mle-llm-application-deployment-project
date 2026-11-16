"""Generation service with cache, traces, quality, and concurrency controls."""

import asyncio
import hashlib
import logging
import time
from pathlib import Path

from solution.deployment_project.config import Settings
from solution.deployment_project.models import (
    GenerateRequest,
    GenerateResponse,
    ProviderResult,
    TraceRecord,
    Usage,
)
from solution.deployment_project.provider import Provider
from solution.deployment_project.tracing import TraceSink, build_trace_sink

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(
        self,
        provider: Provider,
        settings: Settings,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.cache: dict[str, tuple[float, ProviderResult]] = {}
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self.trace_sink = trace_sink or build_trace_sink(settings)

    def cache_key(self, request: GenerateRequest) -> str:
        raw = f"{self.settings.model}:{request.temperature}:{request.max_tokens}:{request.prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def quality_score(text: str) -> float:
        unsupported = "cannot confirm" in text.lower()
        operational = any(
            term in text.lower()
            for term in ("receipt", "identity", "privacy", "status page")
        )
        return 0.9 if unsupported or operational else 0.4

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        started = time.perf_counter()
        key = self.cache_key(request)
        now = time.monotonic()
        entry = self.cache.get(key)
        cache_hit = entry is not None and entry[0] > now
        trace_id = hashlib.sha256(f"{key}:{time.time_ns()}".encode()).hexdigest()[:24]
        try:
            if cache_hit and entry is not None:
                result = entry[1]
            else:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    async with self.semaphore:
                        result = await self.provider.generate(request)
                if not result.text.strip():
                    raise ValueError("Provider returned no answer")
                expired = [
                    key
                    for key, entry in self.cache.items()
                    if entry[0] <= time.monotonic()
                ]
                for stale in expired:
                    self.cache.pop(stale)
                if self.settings.cache_ttl_seconds > 0:
                    if key not in self.cache and len(self.cache) >= 1024:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = (
                        time.monotonic() + self.settings.cache_ttl_seconds,
                        result,
                    )
        except Exception:
            await self._record(
                TraceRecord(
                    trace_id=trace_id,
                    model=self.settings.model,
                    prompt=request.prompt,
                    output="",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    cache_hit=False,
                    usage=Usage(input_tokens=0, output_tokens=0),
                    quality_score=0,
                    status="error",
                    spans=["cache-lookup", "llm-generation"],
                    release=self.settings.release,
                    environment=self.settings.environment,
                )
            )
            raise
        response = GenerateResponse(
            text=result.text,
            model=result.model,
            usage=result.usage,
            cache_hit=cache_hit,
            latency_ms=(time.perf_counter() - started) * 1000,
            quality_score=self.quality_score(result.text),
            trace_id=trace_id,
        )
        await self._record(
            TraceRecord(
                trace_id=response.trace_id,
                model=response.model,
                prompt=request.prompt,
                output=response.text,
                latency_ms=response.latency_ms,
                cache_hit=response.cache_hit,
                usage=Usage(input_tokens=0, output_tokens=0)
                if cache_hit
                else response.usage,
                quality_score=response.quality_score,
                release=self.settings.release,
                environment=self.settings.environment,
                spans=["cache-lookup"]
                if cache_hit
                else ["cache-lookup", "llm-generation"],
            )
        )
        return response

    async def _record(self, trace: TraceRecord) -> None:
        # Telemetry must not block the event loop or turn a valid answer into 502.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.trace_sink.record, trace), timeout=1
            )
        except Exception:
            logger.exception("Trace export failed")


def clear_trace_file(path: Path) -> None:
    if path.exists():
        path.unlink()
