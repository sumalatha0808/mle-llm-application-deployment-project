"""Reference dataset and evaluation tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from solution.deployment_project.evaluation import (
    evaluate_case,
    load_cases,
    render_report,
    run_evaluation,
)
from solution.deployment_project.models import EvaluationCase, GenerateResponse, Usage
from solution.deployment_project.service import GenerationService


def test_case_ids_are_unique() -> None:
    cases = load_cases()
    assert len(cases) == 5
    assert len({case.case_id for case in cases}) == len(cases)


def test_evaluation_covers_every_case(tmp_path) -> None:
    results = run_evaluation(trace_path=tmp_path / "evaluation.jsonl")

    assert len(results) == len(load_cases())
    assert all(result.passed for result in results)
    assert results[0].cache_verified is True
    assert all(result.cache_verified is None for result in results[1:])
    assert "## Decision" in render_report(results)
    assert "N/A" in render_report(results)


@pytest.mark.parametrize(
    "hits,verified",
    [([False, True], True), ([True, True], False), ([False, False], False)],
)
def test_evaluation_checks_every_response_and_a_real_cache_transition(hits, verified):
    responses = iter(
        [
            GenerateResponse(
                text=text,
                model="stub",
                usage=Usage(input_tokens=1, output_tokens=1),
                cache_hit=hit,
                latency_ms=latency,
                quality_score=0.5,
                trace_id="test",
            )
            for text, hit, latency in zip(["wrong", "expected"], hits, [100, 2])
        ]
    )

    service = MagicMock(spec=GenerationService)
    service.generate = AsyncMock(side_effect=responses)
    case = EvaluationCase(
        case_id="test", prompt="question", expected_terms=["expected"], repeat=2
    )
    result = asyncio.run(evaluate_case(case, service))
    assert result.passed is False
    assert result.cache_verified is verified
    assert result.latency_ms == 51


def test_evaluation_preserves_server_traces(tmp_path, monkeypatch):
    from solution.deployment_project import evaluation

    cases = load_cases()
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    monkeypatch.setattr(evaluation, "load_cases", lambda: cases)
    server_trace = tmp_path / "solution" / "generated" / "traces.jsonl"
    server_trace.parent.mkdir(parents=True)
    server_trace.write_text("existing server evidence\n")
    run_evaluation()
    assert server_trace.read_text() == "existing server evidence\n"
    assert server_trace.with_name("evaluation_traces.jsonl").exists()


@pytest.mark.parametrize(
    "text,passed", [("unconfirmed", False), ("Confirmed update.", True)]
)
def test_expected_terms_do_not_match_inside_other_words(text, passed):
    service = MagicMock(spec=GenerationService)
    service.generate = AsyncMock(
        return_value=GenerateResponse(
            text=text,
            model="stub",
            usage=Usage(input_tokens=1, output_tokens=1),
            cache_hit=False,
            latency_ms=1,
            quality_score=0.4,
            trace_id="test",
        )
    )
    case = EvaluationCase(
        case_id="test", prompt="question", expected_terms=["confirmed"], repeat=1
    )
    assert asyncio.run(evaluate_case(case, service)).passed is passed
