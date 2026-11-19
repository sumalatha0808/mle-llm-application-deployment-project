"""Run every shared case through the service and write JSON + Markdown results.

Usage (from the repository root):
    uv run python -m src.evaluation            # dry provider, offline
    uv run python -m src.evaluation --live     # provider from .env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
from pathlib import Path

from pydantic import BaseModel

from src.config import ROOT, Settings
from src.models import GenerateRequest, GenerateResponse
from src.provider import build_provider
from src.service import GenerationFailed, GenerationService
from src.tracing import JsonlTraceSink


class EvaluationCase(BaseModel):
    case_id: str
    prompt: str
    expected_terms: list[str]
    repeat: int = 1


class CaseResult(BaseModel):
    case_id: str
    attempts: int
    errors: int
    terms_passed: bool
    cache_evidence: str
    provider_calls: int
    quality_mean: float | None
    latency_ms: list[float]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    trace_ids: list[str]
    answer: str


def load_cases(
    path: Path = ROOT / "data" / "evaluation" / "cases.json",
) -> list[EvaluationCase]:
    return [
        EvaluationCase.model_validate(c) for c in json.loads(path.read_text("utf-8"))
    ]


def has_term(text: str, term: str) -> bool:
    return (
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None
    )


async def evaluate_case(case: EvaluationCase, service: GenerationService) -> CaseResult:
    responses: list[GenerateResponse] = []
    trace_ids: list[str] = []
    errors = 0
    calls_before = service.provider_calls
    for _ in range(case.repeat):
        try:
            response = await service.generate(GenerateRequest(prompt=case.prompt))
            responses.append(response)
            trace_ids.append(response.trace_id)
        except GenerationFailed as failure:
            errors += 1
            trace_ids.append(failure.trace_id)
    calls = service.provider_calls - calls_before
    if case.repeat == 1:
        evidence = "N/A (single request)"
    elif errors:
        evidence = "not verified (error)"
    else:
        flags = [r.cache_hit for r in responses]
        ok = flags[0] is False and all(flags[1:]) and calls == 1
        evidence = (
            f"{'verified' if ok else 'FAILED'}: hits={flags}, provider_calls={calls}"
        )
    fresh = [r for r in responses if not r.cache_hit]
    return CaseResult(
        case_id=case.case_id,
        attempts=case.repeat,
        errors=errors,
        terms_passed=bool(responses)
        and not errors
        and all(has_term(r.text, t) for r in responses for t in case.expected_terms),
        cache_evidence=evidence,
        provider_calls=calls,
        quality_mean=statistics.mean(r.quality_score for r in responses)
        if responses
        else None,
        latency_ms=[round(r.latency_ms, 2) for r in responses],
        input_tokens=sum(r.usage.input_tokens for r in fresh),
        output_tokens=sum(r.usage.output_tokens for r in fresh),
        cost_usd=sum(r.cost_usd for r in fresh),
        trace_ids=trace_ids,
        answer=responses[-1].text if responses else "",
    )


async def run(settings: Settings) -> list[CaseResult]:
    service = GenerationService(build_provider(settings), settings)
    return [await evaluate_case(case, service) for case in load_cases()]


def render_markdown(results: list[CaseResult], settings: Settings) -> str:
    rows = "\n".join(
        f"| {r.case_id} | {r.attempts - r.errors}/{r.attempts} | "
        f"{'pass' if r.terms_passed else 'FAIL'} | {r.cache_evidence} | "
        f"{'' if r.quality_mean is None else f'{r.quality_mean:.2f}'} | "
        f"{', '.join(f'{x:.1f}' for x in r.latency_ms)} | "
        f"{r.input_tokens}/{r.output_tokens} | {r.cost_usd:.6f} |"
        for r in results
    )
    scores = [r.quality_mean for r in results if r.quality_mean is not None]
    return (
        f"# Functional Evaluation ({settings.provider}, model `{settings.model}`)\n\n"
        "| Case | Success | Expected terms | Cache evidence | Quality | Latency ms | "
        "Tokens in/out (new) | Cost USD |\n|---|---|---|---|---|---|---|---|\n"
        f"{rows}\n\n"
        f"Cases passing terms: {sum(r.terms_passed for r in results)}/{len(results)}; "
        f"mean quality {statistics.mean(scores) if scores else float('nan'):.3f} "
        f"over {len(scores)} cases; total new cost "
        f"{sum(r.cost_usd for r in results):.6f} USD.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="use provider from env/.env"
    )
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "generated")
    args = parser.parse_args()
    env = Settings.from_env()
    label = env.provider if args.live else "dry"
    trace_path = args.out / f"evaluation_traces_{label}.jsonl"
    settings = (
        Settings(**{**env.__dict__, "trace_sink": "jsonl", "trace_path": trace_path})
        if args.live
        else Settings(
            trace_sink="jsonl", trace_path=trace_path, environment="evaluation"
        )
    )
    trace_path.unlink(missing_ok=True)
    results = asyncio.run(run(settings))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"evaluation_{label}.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2), "utf-8"
    )
    markdown = render_markdown(results, settings)
    (args.out / f"evaluation_{label}.md").write_text(markdown, "utf-8")
    print(markdown)
    print(
        f"traces: {len(JsonlTraceSink(trace_path).read_all())} records in {trace_path}"
    )


if __name__ == "__main__":
    main()
