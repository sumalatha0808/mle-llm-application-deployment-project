"""Reference cache and trace tests."""

import asyncio
import json
from pathlib import Path

from solution.deployment_project.config import Settings
from solution.deployment_project.models import GenerateRequest
from solution.deployment_project.provider import DryProvider
from solution.deployment_project.service import GenerationService


def test_repeated_request_hits_cache_and_writes_complete_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "traces.jsonl"
    service = GenerationService(DryProvider(), Settings(trace_path=trace_path))
    request = GenerateRequest(prompt="locked account")

    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))
    traces = [json.loads(line) for line in trace_path.read_text().splitlines()]

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert traces[-1]["spans"] == ["cache-lookup"]
    assert traces[-1]["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert traces[-1]["quality_score"] >= 0.8
