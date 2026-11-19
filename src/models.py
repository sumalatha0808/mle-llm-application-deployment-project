"""Typed API, provider and trace contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    """Client request. Every field here affects the output and the cache key."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    max_tokens: int = Field(default=300, ge=1, le=2048)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)


class Usage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ProviderResult(BaseModel):
    text: str
    model: str
    usage: Usage


class GenerateResponse(BaseModel):
    text: str
    model: str
    usage: Usage = Field(description="Tokens of the answer (original call if cached).")
    cost_usd: float = Field(ge=0, description="Cost of the original provider call.")
    cache_hit: bool
    latency_ms: float = Field(ge=0)
    quality_score: float = Field(ge=0, le=1)
    policy_topic: str
    trace_id: str


class ErrorResponse(BaseModel):
    error: Literal["upstream_timeout", "upstream_error", "overloaded", "not_ready"]
    detail: str
    trace_id: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    provider: str
    model: str
    release: str
    environment: str
    trace_sink: str
    cache: Literal["process-local"]
    problems: list[str] = Field(default_factory=list)


class Stage(BaseModel):
    """One measured stage of a request (nested under the root observation)."""

    name: Literal["cache-lookup", "prompt-preparation", "llm-generation"]
    start_offset_ms: float
    duration_ms: float
    status: Literal["ok", "error", "skipped"] = "ok"
    detail: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TraceRecord(BaseModel):
    trace_id: str
    name: str = "northstar-support-request"
    status: Literal["ok", "error"]
    error_type: str | None = None
    error_detail: str | None = None  # service-generated message, never content
    environment: str
    release: str
    provider: str
    model: str
    policy_version: str
    policy_topic: str
    cache_hit: bool
    latency_ms: float
    stages: list[Stage]
    # Usage/cost of NEW provider work done by this request (0 on cache hit).
    usage: Usage
    cost_usd: float
    quality_score: float | None  # None for failures: not an answer-quality sample
    prompt_chars: int
    output_chars: int
    # Masked text, present only when content sampling selected this trace.
    prompt_masked: str | None = None
    output_masked: str | None = None
    params: dict[str, float | int]
