"""JSONL and Langfuse Python SDK v5 trace exporters."""

from pathlib import Path
from typing import Protocol

from langfuse import get_client, propagate_attributes

from solution.deployment_project.config import Settings
from solution.deployment_project.models import TraceRecord


class TraceSink(Protocol):
    def record(self, trace: TraceRecord) -> None: ...


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, trace: TraceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(trace.model_dump_json() + "\n")


class LangfuseTraceSink:
    def __init__(self) -> None:
        self.client = get_client()

    def record(self, trace: TraceRecord) -> None:
        with (
            propagate_attributes(
                trace_name=trace.name,
                version=trace.release,
                environment=trace.environment,
                tags=["deployment-project", trace.release],
                metadata={
                    "trace_id": trace.trace_id,
                    "environment": trace.environment,
                    "cache_hit": str(trace.cache_hit).lower(),
                },
            ),
            self.client.start_as_current_observation(
                as_type="span",
                name=trace.name,
                input={"prompt_chars": len(trace.prompt)},
                level="ERROR" if trace.status == "error" else "DEFAULT",
                metadata={
                    "trace_id": trace.trace_id,
                    "release": trace.release,
                    "environment": trace.environment,
                    "cache_hit": str(trace.cache_hit).lower(),
                    "request_latency_ms": trace.latency_ms,
                },
            ) as root,
        ):
            if not trace.cache_hit:
                with self.client.start_as_current_observation(
                    as_type="generation",
                    name="llm-generation",
                    model=trace.model,
                    input={"prompt_chars": len(trace.prompt)},
                    level="ERROR" if trace.status == "error" else "DEFAULT",
                ) as generation:
                    generation.update(
                        output={"output_chars": len(trace.output)},
                        usage_details={
                            "input": trace.usage.input_tokens,
                            "output": trace.usage.output_tokens,
                        },
                    )
            if trace.status == "ok":
                root.score(name="quality", value=trace.quality_score)
            root.update(
                output={"status": trace.status, "output_chars": len(trace.output)}
            )

    def flush(self) -> None:
        """Finish pending exports at process shutdown, not on every request."""
        self.client.flush()


def build_trace_sink(settings: Settings) -> TraceSink:
    if settings.trace_sink == "jsonl":
        return JsonlTraceSink(settings.trace_path)
    if settings.trace_sink == "langfuse":
        return LangfuseTraceSink()
    raise ValueError(f"Unsupported TRACE_SINK: {settings.trace_sink}")
