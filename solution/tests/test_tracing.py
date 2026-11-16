"""Cloud exports preserve privacy and distinguish cache hits from generation."""

from contextlib import contextmanager, nullcontext

import pytest

from solution.deployment_project.models import TraceRecord, Usage
from solution.deployment_project.tracing import LangfuseTraceSink


@pytest.mark.parametrize(
    "cache_hit,status,quality",
    [(False, "ok", 0.9), (True, "ok", 0.9), (False, "ok", 0.0), (False, "error", 0.0)],
)
def test_cloud_export_contract(monkeypatch, cache_hit, status, quality):
    calls = []

    class Observation:
        active = True

        def update(self, **values):
            assert self.active
            calls.append(values)

        def score(self, **values):
            assert self.active
            calls.append(values)

    class Client:
        @contextmanager
        def start_as_current_observation(self, **values):
            calls.append(values)
            observation = Observation()
            yield observation
            observation.active = False

    monkeypatch.setattr(
        "solution.deployment_project.tracing.get_client", lambda: Client()
    )
    monkeypatch.setattr(
        "solution.deployment_project.tracing.propagate_attributes",
        lambda **kwargs: nullcontext(),
    )
    trace = TraceRecord(
        trace_id="test",
        model="test",
        prompt="private customer question",
        output="private customer answer" if status == "ok" else "",
        latency_ms=123,
        cache_hit=cache_hit,
        usage=Usage(input_tokens=0, output_tokens=0),
        quality_score=quality,
        status=status,
    )
    LangfuseTraceSink().record(trace)
    assert trace.prompt not in repr(calls)
    if trace.output:
        assert trace.output not in repr(calls)
    generations = [call for call in calls if call.get("as_type") == "generation"]
    assert len(generations) == (0 if cache_hit else 1)
    assert calls[0]["metadata"]["request_latency_ms"] == 123
    assert any(call.get("level") == "ERROR" for call in calls) == (status == "error")
    scores = [call for call in calls if call.get("name") == "quality"]
    assert scores == ([{"name": "quality", "value": quality}] if status == "ok" else [])
