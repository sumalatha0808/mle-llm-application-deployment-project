"""Generation service: preparation, cache, bounded provider calls, tracing."""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field

from src.cache import TTLCache, cache_key
from src.config import Settings
from src.models import (
    GenerateRequest,
    GenerateResponse,
    ProviderResult,
    Stage,
    TraceRecord,
    Usage,
)
from src.policy import POLICY_VERSION, classify_topic, quality_score
from src.provider import Provider, ProviderError, ProviderTimeout
from src.tracing import TraceSink, build_trace_sink, mask_text

logger = logging.getLogger(__name__)


def shared_wait(exc: Exception, timer: _Timer) -> bool:
    """True when this request only waited on another request's provider call."""
    return not any(stage.name == "llm-generation" for stage in timer.stages)


class Overloaded(Exception):
    """No provider slot became free within the queue timeout."""


class GenerationFailed(Exception):
    """Wraps a failure with the trace id so the API can return it."""

    def __init__(self, cause: Exception, trace_id: str) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.trace_id = trace_id


@dataclass
class CachedAnswer:
    result: ProviderResult
    cost_usd: float


@dataclass
class _Timer:
    started: float = field(default_factory=time.perf_counter)
    stages: list[Stage] = field(default_factory=list)

    def ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000

    def add(self, name, start_ms: float, status="ok", **detail) -> None:
        self.stages.append(
            Stage(
                name=name,
                start_offset_ms=start_ms,
                duration_ms=self.ms() - start_ms,
                status=status,
                detail=detail,
            )
        )


class GenerationService:
    def __init__(
        self,
        provider: Provider,
        settings: Settings,
        trace_sink: TraceSink | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.cache: TTLCache[CachedAnswer] = TTLCache(
            settings.cache_ttl_seconds, settings.cache_max_entries
        )
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self.trace_sink = (
            trace_sink if trace_sink is not None else build_trace_sink(settings)
        )
        self.rng = rng or random.Random()
        self.provider_calls = 0
        self.in_flight = 0

    def cost(self, usage: Usage) -> float:
        s = self.settings
        return round(
            usage.input_tokens * s.input_cost_per_mtok / 1e6
            + usage.output_tokens * s.output_cost_per_mtok / 1e6,
            8,
        )

    async def _call_provider(
        self, request: GenerateRequest, timer: _Timer
    ) -> CachedAnswer:
        start = timer.ms()
        status, queue_ms = "error", 0.0
        try:
            try:
                await asyncio.wait_for(
                    self.semaphore.acquire(), self.settings.queue_timeout_seconds
                )
            except TimeoutError as exc:
                raise Overloaded("All provider slots busy") from exc
            queue_ms = timer.ms() - start
            self.in_flight += 1
            self.provider_calls += 1
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    result = await self.provider.generate(request)
            except TimeoutError as exc:
                raise ProviderTimeout("Provider deadline exceeded") from exc
            finally:
                self.in_flight -= 1
                self.semaphore.release()
            if not result.text.strip():
                raise ProviderError("Provider returned an empty answer")
            status = "ok"
            return CachedAnswer(result=result, cost_usd=self.cost(result.usage))
        finally:
            timer.add(
                "llm-generation", start, status=status, queue_wait_ms=round(queue_ms, 2)
            )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        timer = _Timer()
        trace_id = uuid.uuid4().hex  # 32 hex chars: valid Langfuse/OTel trace id

        prep_start = timer.ms()
        topic = classify_topic(request.prompt)
        key = cache_key(
            request,
            provider=self.settings.provider,
            model=self.settings.model,
            policy_version=POLICY_VERSION,
        )
        timer.add(
            "prompt-preparation", prep_start, policy_version=POLICY_VERSION, topic=topic
        )

        lookup_start = timer.ms()
        cached = self.cache.get(key)
        timer.add(
            "cache-lookup",
            lookup_start,
            hit=cached is not None,
            entries=len(self.cache),
            scope="process-local",
        )

        shared = False
        try:
            if cached is not None:
                answer, cache_hit = cached, True
            else:
                answer, cache_hit, shared = await self.cache.get_or_compute(
                    key, lambda: self._call_provider(request, timer)
                )
        except Exception as exc:
            # Billed-but-unusable tokens, only for the request that made the call.
            wasted = (
                getattr(exc, "usage", None) if not shared_wait(exc, timer) else None
            )
            await self._record(
                self._trace(
                    trace_id,
                    request,
                    topic,
                    timer,
                    status="error",
                    error_type=type(exc).__name__,
                    error_detail=str(exc)[:200],
                    usage=wasted,
                    cost=self.cost(wasted) if wasted else 0.0,
                )
            )
            raise GenerationFailed(exc, trace_id) from exc

        new_work = not cache_hit and not shared
        score = quality_score(topic, answer.result.text)
        response = GenerateResponse(
            text=answer.result.text,
            model=answer.result.model,
            usage=answer.result.usage,
            cost_usd=answer.cost_usd,
            cache_hit=cache_hit or shared,
            latency_ms=timer.ms(),
            quality_score=score,
            policy_topic=topic,
            trace_id=trace_id,
        )
        await self._record(
            self._trace(
                trace_id,
                request,
                topic,
                timer,
                status="ok",
                model=answer.result.model,
                cache_hit=response.cache_hit,
                usage=answer.result.usage if new_work else Usage(),
                cost=answer.cost_usd if new_work else 0.0,
                score=score,
                output=answer.result.text,
            )
        )
        return response

    def _trace(
        self,
        trace_id,
        request,
        topic,
        timer,
        *,
        status,
        error_type=None,
        error_detail=None,
        model=None,
        cache_hit=False,
        usage=None,
        cost=0.0,
        score=None,
        output="",
    ) -> TraceRecord:
        keep_text = self.rng.random() < self.settings.trace_content_sample_rate
        return TraceRecord(
            trace_id=trace_id,
            status=status,
            error_type=error_type,
            error_detail=error_detail,
            environment=self.settings.environment,
            release=self.settings.release,
            provider=self.settings.provider,
            model=model or self.settings.model,
            policy_version=POLICY_VERSION,
            policy_topic=topic,
            cache_hit=cache_hit,
            latency_ms=timer.ms(),
            stages=sorted(timer.stages, key=lambda s: s.start_offset_ms),
            usage=usage or Usage(),
            cost_usd=cost,
            quality_score=score,
            prompt_chars=len(request.prompt),
            output_chars=len(output),
            prompt_masked=mask_text(request.prompt) if keep_text else None,
            output_masked=mask_text(output) if keep_text and output else None,
            params={
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "top_p": request.top_p,
            },
        )

    async def _record(self, trace: TraceRecord) -> None:
        """Export off the event loop; telemetry failure never fails a request."""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.trace_sink.record, trace), timeout=2
            )
        except Exception:
            logger.exception("Trace export failed for %s", trace.trace_id)
