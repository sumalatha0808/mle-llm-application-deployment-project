"""Trace content: stages, usage, cost, masking, sampling and Langfuse export."""

import asyncio
import random
from contextlib import contextmanager

from src.models import GenerateRequest
from src.provider import DryProvider
from src.service import GenerationFailed, GenerationService
from src.tracing import JsonlTraceSink, LangfuseTraceSink, SampledSink, mask_text
from tests.helpers import FailingProvider, make_settings

REFUND = "What evidence is required for a duplicate-payment refund?"


def stage_names(trace):
    return [s["name"] for s in trace["stages"]]


def test_miss_and_hit_traces(tmp_path):
    settings = make_settings(
        tmp_path,
        input_cost_per_mtok=1.0,
        output_cost_per_mtok=2.0,
        release="abc123",
        environment="test",
    )
    service = GenerationService(DryProvider(), settings)
    first = asyncio.run(service.generate(GenerateRequest(prompt=REFUND)))
    asyncio.run(service.generate(GenerateRequest(prompt=REFUND)))
    miss, hit = JsonlTraceSink(settings.trace_path).read_all()

    assert miss["trace_id"] == first.trace_id and len(miss["trace_id"]) == 32
    assert stage_names(miss) == ["prompt-preparation", "cache-lookup", "llm-generation"]
    assert miss["usage"]["input_tokens"] > 0 and miss["cost_usd"] > 0
    assert miss["release"] == "abc123" and miss["environment"] == "test"
    assert miss["quality_score"] >= 0.8 and miss["status"] == "ok"

    assert hit["cache_hit"] is True
    assert "llm-generation" not in stage_names(hit)
    assert (
        hit["usage"] == {"input_tokens": 0, "output_tokens": 0} and hit["cost_usd"] == 0
    )
    # Raw text is not stored by default (content sample rate 0).
    assert miss["prompt_masked"] is None and miss["output_masked"] is None
    assert miss["prompt_chars"] == len(REFUND)


def test_failure_trace_has_no_quality_score(tmp_path):
    settings = make_settings(tmp_path)
    service = GenerationService(FailingProvider(), settings)
    try:
        asyncio.run(service.generate(GenerateRequest(prompt=REFUND)))
    except GenerationFailed as failure:
        trace_id = failure.trace_id
    (trace,) = JsonlTraceSink(settings.trace_path).read_all()
    assert trace["trace_id"] == trace_id and trace["status"] == "error"
    assert trace["error_type"] == "ProviderError" and trace["quality_score"] is None
    assert trace["stages"][-1] == {
        **trace["stages"][-1],
        "name": "llm-generation",
        "status": "error",
    }


def test_sampled_content_is_masked(tmp_path):
    settings = make_settings(tmp_path, trace_content_sample_rate=1.0)
    service = GenerationService(DryProvider(), settings)
    prompt = "Locked account for jane.doe@example.com, phone +49 170 1234567, card 4111 1111 1111 1111"
    asyncio.run(service.generate(GenerateRequest(prompt=prompt)))
    (trace,) = JsonlTraceSink(settings.trace_path).read_all()
    assert (
        "example.com" not in trace["prompt_masked"]
        and "<email>" in trace["prompt_masked"]
    )
    assert (
        "4111" not in trace["prompt_masked"] and "1234567" not in trace["prompt_masked"]
    )
    assert mask_text("acct 12345678") == "acct <number>"


def test_trace_sampling_keeps_errors(tmp_path):
    kept = []

    class Collect:
        def record(self, trace):
            kept.append(trace.status)

    sink = SampledSink(Collect(), rate=0.0, rng=random.Random(1))
    ok = GenerationService(DryProvider(), make_settings(tmp_path), trace_sink=sink)
    bad = GenerationService(FailingProvider(), make_settings(tmp_path), trace_sink=sink)
    asyncio.run(ok.generate(GenerateRequest(prompt=REFUND)))
    try:
        asyncio.run(bad.generate(GenerateRequest(prompt=REFUND)))
    except GenerationFailed:
        pass
    assert kept == ["error"]


def test_trace_export_failure_does_not_fail_request(tmp_path):
    class Broken:
        def record(self, trace):
            raise OSError("disk full")

    service = GenerationService(
        DryProvider(), make_settings(tmp_path), trace_sink=Broken()
    )
    assert asyncio.run(service.generate(GenerateRequest(prompt=REFUND))).text


class FakeObservation:
    def __init__(self, log, kind, name, kwargs):
        self.log, self.kind, self.name, self.kwargs = log, kind, name, kwargs

    def update(self, **kwargs):
        self.log.append(("update", self.name, kwargs))

    def score_trace(self, **kwargs):
        self.log.append(("score", self.name, kwargs))


class FakeLangfuse:
    def __init__(self):
        self.log = []

    @contextmanager
    def start_as_current_observation(self, *, name, as_type="span", **kwargs):
        self.log.append(("start", as_type, name, kwargs))
        yield FakeObservation(self.log, as_type, name, kwargs)


def _export(tmp_path, provider):
    sink = LangfuseTraceSink.__new__(LangfuseTraceSink)
    sink.client = FakeLangfuse()
    service = GenerationService(
        provider, make_settings(tmp_path, input_cost_per_mtok=1.0), trace_sink=sink
    )
    for _ in range(2):
        try:
            asyncio.run(service.generate(GenerateRequest(prompt=REFUND)))
        except GenerationFailed:
            pass
    return sink.client.log


def test_langfuse_export_structure(tmp_path):
    log = _export(tmp_path, DryProvider())
    starts = [(e[1], e[2]) for e in log if e[0] == "start"]
    assert starts == [
        ("span", "northstar-support-request"),
        ("span", "prompt-preparation"),
        ("span", "cache-lookup"),
        ("generation", "llm-generation"),
        ("span", "northstar-support-request"),
        ("span", "prompt-preparation"),
        ("span", "cache-lookup"),  # hit: no generation observation
    ]
    root = next(e for e in log if e[0] == "start")
    assert len(root[3]["trace_context"]["trace_id"]) == 32
    gen_update = next(e for e in log if e[0] == "update" and e[1] == "llm-generation")
    assert (
        gen_update[2]["usage_details"]["input"] > 0
        and gen_update[2]["cost_details"]["total"] > 0
    )
    assert sum(e[0] == "score" for e in log) == 2


def test_langfuse_failure_has_no_score(tmp_path):
    log = _export(tmp_path, FailingProvider())
    assert not any(e[0] == "score" for e in log)
    root = next(e for e in log if e[0] == "start")
    assert root[3]["level"] == "ERROR"


def test_failed_call_records_billed_usage(tmp_path):
    from src.models import Usage
    from src.provider import ProviderError

    class Truncating(DryProvider):
        async def generate(self, request):
            self.calls += 1
            raise ProviderError(
                "Incomplete answer (finish_reason=length)",
                Usage(input_tokens=400, output_tokens=300),
            )

    settings = make_settings(
        tmp_path, input_cost_per_mtok=1.0, output_cost_per_mtok=2.0
    )
    service = GenerationService(Truncating(), settings)
    try:
        asyncio.run(service.generate(GenerateRequest(prompt=REFUND)))
    except GenerationFailed:
        pass
    (trace,) = JsonlTraceSink(settings.trace_path).read_all()
    assert trace["usage"] == {"input_tokens": 400, "output_tokens": 300}
    assert trace["cost_usd"] == 0.001 and trace["quality_score"] is None
    assert "finish_reason=length" in trace["error_detail"]


def test_jsonl_sink_is_safe_under_concurrent_writes(tmp_path):
    import threading

    settings = make_settings(tmp_path)
    service = GenerationService(DryProvider(), settings)
    trace = service._trace(
        "0" * 32,
        GenerateRequest(prompt=REFUND * 20),
        "refund",
        __import__("src.service", fromlist=["_Timer"])._Timer(),
        status="ok",
    )
    sink = JsonlTraceSink(settings.trace_path)
    threads = [
        threading.Thread(target=lambda: [sink.record(trace) for _ in range(50)])
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sink.read_all()) == 400
