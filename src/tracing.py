"""Trace masking, sampling and export (JSONL or Langfuse).

Choices:
- Raw prompts/outputs are NOT exported by default. Every trace keeps lengths,
  topic, usage, cost, latency, status and score. ``TRACE_CONTENT_SAMPLE_RATE``
  (default 0) selects a fraction of traces whose text is kept, and that text
  is always masked first (emails, phone/card/account numbers, IBANs).
- ``TRACE_SAMPLE_RATE`` (default 1.0) samples whole traces; errors are always
  exported regardless of sampling.
- The service measures stage timings while handling the request; the exporter
  records them after the request finishes. In Langfuse, observation wall-clock
  durations therefore measure export work: use the ``duration_ms`` metadata.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from pathlib import Path
from typing import Protocol

from src.config import Settings
from src.models import TraceRecord

logger = logging.getLogger(__name__)

_MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (
        re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"),
        "<iban>",
    ),
    (re.compile(r"\b(?:\d[ -]?){12,19}\b"), "<card>"),
    (re.compile(r"\+?\d[\d ()/-]{7,}\d"), "<phone>"),
    (re.compile(r"\b\d{6,}\b"), "<number>"),
]


def mask_text(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


class TraceSink(Protocol):
    def record(self, trace: TraceRecord) -> None: ...


class NullTraceSink:
    def record(self, trace: TraceRecord) -> None:
        return None


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()  # exports run in worker threads

    def record(self, trace: TraceRecord) -> None:
        line = trace.model_dump_json() + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line)

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text("utf-8").splitlines()
            if line
        ]


class LangfuseTraceSink:
    """Export one root span with nested stages, a generation and a score.

    Pattern follows the observability lesson's recorder: root observation,
    nested spans, generation with usage and cost, then a score. The API's
    ``trace_id`` is used as the Langfuse trace id so a client can look it up.
    """

    def __init__(self, settings: Settings) -> None:
        from langfuse import Langfuse

        self.client = Langfuse(
            environment=settings.environment,
            release=settings.release,
            mask=lambda *, data, **_: _mask_any(data),
        )

    def record(self, trace: TraceRecord) -> None:
        from langfuse import propagate_attributes

        level = "ERROR" if trace.status == "error" else "DEFAULT"
        root_input: dict = {"prompt_chars": trace.prompt_chars, "params": trace.params}
        if trace.prompt_masked is not None:
            root_input["prompt_masked"] = trace.prompt_masked
        root_output: dict = {"status": trace.status, "output_chars": trace.output_chars}
        if trace.output_masked is not None:
            root_output["output_masked"] = trace.output_masked
        with (
            propagate_attributes(
                trace_name=trace.name,
                version=trace.release,
                environment=trace.environment,
                tags=["northstar", trace.environment, f"topic:{trace.policy_topic}"],
                metadata={
                    "cache_hit": str(trace.cache_hit).lower(),
                    "policy_version": trace.policy_version,
                    "status": trace.status,
                },
            ),
            self.client.start_as_current_observation(
                trace_context={"trace_id": trace.trace_id},
                as_type="span",
                name=trace.name,
                input=root_input,
                level=level,
                status_message=trace.error_type,
                metadata={
                    "release": trace.release,
                    "environment": trace.environment,
                    "provider": trace.provider,
                    "model": trace.model,
                    "cache_hit": trace.cache_hit,
                    "request_latency_ms": round(trace.latency_ms, 2),
                    "cost_usd": trace.cost_usd,
                    "policy_topic": trace.policy_topic,
                },
            ) as root,
        ):
            for stage in trace.stages:
                metadata = {
                    "duration_ms": round(stage.duration_ms, 2),
                    "start_offset_ms": round(stage.start_offset_ms, 2),
                    **stage.detail,
                }
                stage_level = "ERROR" if stage.status == "error" else "DEFAULT"
                if stage.name == "llm-generation":
                    with self.client.start_as_current_observation(
                        as_type="generation",
                        name=stage.name,
                        model=trace.model,
                        model_parameters=trace.params,
                        input={"prompt_chars": trace.prompt_chars},
                        level=stage_level,
                        metadata=metadata,
                    ) as generation:
                        generation.update(
                            output={"output_chars": trace.output_chars},
                            usage_details={
                                "input": trace.usage.input_tokens,
                                "output": trace.usage.output_tokens,
                            },
                            cost_details={"total": trace.cost_usd},
                        )
                else:
                    with self.client.start_as_current_observation(
                        as_type="span",
                        name=stage.name,
                        level=stage_level,
                        metadata=metadata,
                    ):
                        pass
            if trace.quality_score is not None:  # failures are not quality samples
                root.score_trace(name="answer_quality", value=trace.quality_score)
            root.update(output=root_output)

    def flush(self) -> None:
        self.client.flush()


def _mask_any(data):
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {k: _mask_any(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask_any(v) for v in data]
    return data


class SampledSink:
    """Drop a fraction of successful traces; always keep errors."""

    def __init__(
        self, inner: TraceSink, rate: float, rng: random.Random | None = None
    ) -> None:
        self.inner = inner
        self.rate = rate
        self.rng = rng or random.Random()

    def record(self, trace: TraceRecord) -> None:
        if trace.status == "error" or self.rng.random() < self.rate:
            self.inner.record(trace)

    def flush(self) -> None:
        flush = getattr(self.inner, "flush", None)
        if callable(flush):
            flush()


def build_trace_sink(settings: Settings) -> TraceSink:
    if settings.trace_sink == "none":
        return NullTraceSink()
    inner: TraceSink = (
        LangfuseTraceSink(settings)
        if settings.trace_sink == "langfuse"
        else JsonlTraceSink(settings.trace_path)
    )
    return (
        inner
        if settings.trace_sample_rate >= 1.0
        else SampledSink(inner, settings.trace_sample_rate)
    )
