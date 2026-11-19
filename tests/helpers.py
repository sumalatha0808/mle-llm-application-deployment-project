"""Test doubles for the provider boundary."""

import asyncio
from pathlib import Path

from src.config import Settings
from src.models import GenerateRequest, ProviderResult
from src.provider import DryProvider, ProviderError


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "trace_sink": "jsonl",
        "trace_path": tmp_path / "traces.jsonl",
        **overrides,
    }
    return Settings(**values)


class SlowProvider(DryProvider):
    def __init__(self, delay_s: float) -> None:
        super().__init__()
        self.delay_s = delay_s

    async def generate(self, request: GenerateRequest) -> ProviderResult:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return self.answer(request)


class FailingProvider(DryProvider):
    async def generate(self, request: GenerateRequest) -> ProviderResult:
        self.calls += 1
        raise ProviderError("HTTP 500 from upstream")
