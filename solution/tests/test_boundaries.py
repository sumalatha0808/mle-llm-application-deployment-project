"""Regression checks for the public service's failure boundaries."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from solution.deployment_project.config import Settings
from solution.deployment_project.models import GenerateRequest
from solution.deployment_project.provider import DryProvider
from solution.deployment_project.service import GenerationService


@pytest.mark.parametrize("prompt", ["", " ", "\n\t"])
def test_blank_prompt_is_rejected(prompt):
    with pytest.raises(ValidationError):
        GenerateRequest(prompt=prompt)


@pytest.mark.parametrize(
    "values",
    [
        {"max_concurrency": 0},
        {"cache_ttl_seconds": -1},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"request_timeout_seconds": float("inf")},
    ],
)
def test_invalid_configuration_fails_early(values):
    with pytest.raises(ValueError):
        Settings(**values)


def make_service(tmp_path, provider=None, **values):
    settings = Settings(trace_path=tmp_path / "traces.jsonl", **values)
    return GenerationService(provider or DryProvider(), settings)


@pytest.mark.parametrize("queued", [False, True])
def test_deadline_covers_provider_and_queue(tmp_path, queued):
    class SlowProvider:
        async def generate(self, request):
            await asyncio.sleep(1)
            raise AssertionError("The deadline should cancel this call")

    service = make_service(tmp_path, SlowProvider(), request_timeout_seconds=0.01)

    async def run():
        if queued:
            for _ in range(service.settings.max_concurrency):
                await service.semaphore.acquire()
        try:
            with pytest.raises(TimeoutError):
                await service.generate(GenerateRequest(prompt="timeout"))
        finally:
            if queued:
                for _ in range(service.settings.max_concurrency):
                    service.semaphore.release()

    asyncio.run(run())


def test_empty_provider_result_is_not_cached(tmp_path):
    class EmptyProvider(DryProvider):
        async def generate(self, request):
            result = await super().generate(request)
            return result.model_copy(update={"text": "  "})

    service = make_service(tmp_path, EmptyProvider())
    with pytest.raises(ValueError, match="no answer"):
        asyncio.run(service.generate(GenerateRequest(prompt="empty")))
    assert not service.cache


def test_cache_expiry_and_disabled_cache(tmp_path):
    service = make_service(tmp_path, cache_ttl_seconds=0)
    request = GenerateRequest(prompt="locked account")
    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))
    assert not first.cache_hit and not second.cache_hit
    assert not service.cache


def test_telemetry_failure_does_not_break_generation(tmp_path):
    class BrokenSink:
        def record(self, trace):
            raise OSError("storage unavailable")

    service = GenerationService(DryProvider(), Settings(), trace_sink=BrokenSink())
    result = asyncio.run(service.generate(GenerateRequest(prompt="locked account")))
    assert result.text


def test_failed_provider_is_traced_without_exception_contents(tmp_path):
    class BrokenProvider:
        async def generate(self, request):
            raise RuntimeError("secret upstream message")

    service = make_service(tmp_path, BrokenProvider())
    with pytest.raises(RuntimeError):
        asyncio.run(service.generate(GenerateRequest(prompt="failure")))
    trace = json.loads((tmp_path / "traces.jsonl").read_text())
    assert trace["status"] == "error"
    assert trace["output"] == ""
    assert trace["usage"] == {"input_tokens": 0, "output_tokens": 0}
