"""Shared-case evaluation and Markdown report rendering."""

import asyncio
import json
import re
import statistics
from pathlib import Path

from solution.deployment_project.config import ROOT, Settings
from solution.deployment_project.models import (
    CaseResult,
    EvaluationCase,
    GenerateRequest,
)
from solution.deployment_project.provider import DryProvider
from solution.deployment_project.service import GenerationService, clear_trace_file


def load_cases() -> list[EvaluationCase]:
    path = ROOT / "data" / "evaluation" / "cases.json"
    return [
        EvaluationCase.model_validate(item) for item in json.loads(path.read_text())
    ]


async def evaluate_case(case: EvaluationCase, service: GenerationService) -> CaseResult:
    responses = [
        await service.generate(GenerateRequest(prompt=case.prompt))
        for _ in range(case.repeat)
    ]
    passed = all(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", response.text, re.IGNORECASE)
        is not None
        for response in responses
        for term in case.expected_terms
    )
    return CaseResult(
        case_id=case.case_id,
        passed=passed,
        cache_verified=(
            not responses[0].cache_hit and all(r.cache_hit for r in responses[1:])
        )
        if case.repeat > 1
        else None,
        quality_score=statistics.mean(r.quality_score for r in responses),
        latency_ms=statistics.mean(r.latency_ms for r in responses),
    )


def run_evaluation(trace_path: Path | None = None) -> list[CaseResult]:
    settings = Settings(
        provider="dry",
        trace_sink="jsonl",
        cache_ttl_seconds=300,
        trace_path=trace_path
        or ROOT / "solution" / "generated" / "evaluation_traces.jsonl",
    )
    clear_trace_file(settings.trace_path)
    service = GenerationService(DryProvider(), settings)

    async def run_all() -> list[CaseResult]:
        return [await evaluate_case(case, service) for case in load_cases()]

    return asyncio.run(run_all())


def render_report(results: list[CaseResult]) -> str:
    passed = sum(
        result.passed and result.cache_verified is not False for result in results
    )
    rows = "\n".join(
        f"| {result.case_id} | {result.passed} | "
        f"{result.cache_verified if result.cache_verified is not None else 'N/A'} | "
        f"{result.quality_score:.2f} | {result.latency_ms:.2f} |"
        for result in results
    )
    return f"""# Reference Deployment Evaluation

## Decision

The deterministic reference passes {passed}/{len(results)} cases: each must match
all expected terms in every response and verify caching when repeated.
It is suitable as a deployment scaffold, not as evidence that a live
provider meets production latency or answer-quality objectives.

## Case Results

| Case | Expected Terms Matched | Cache Verified | Mean Heuristic Score | Mean Latency ms |
|---|---:|---:|---:|---:|
{rows}

N/A means the case has no repeated request. Cache verification requires an
initial miss followed by hits; mean latency includes every repeat, not just the
last cached response. Neither these timings nor the keyword scores prove a live SLO.
Expected-term matching uses whole words or phrases; it does not detect negation
or establish that an answer is correct.

## Service Health And LLM Signals

- Health and readiness contracts are separate.
- Traces distinguish cache hits from generation and count no new provider tokens on hits.
- The quality score is a keyword heuristic, not a factuality or policy-compliance metric.
- Dry latency is a smoke metric and must not be treated as live capacity.

## Required Next Experiments

1. Run the same cases against the configured live provider.
2. Run the supplied load profile against the deployed Render URL.
3. Calibrate quality scoring with human-reviewed responses.
"""
