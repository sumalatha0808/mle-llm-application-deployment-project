"""Environment-backed settings.

Settings are read when ``Settings.from_env()`` is called, not at import time,
so tests and the evaluation CLI can build isolated configurations.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

LIVE_PROVIDERS = {"groq", "mistral", "openrouter", "google", "openai"}
SUPPORTED_PROVIDERS = LIVE_PROVIDERS | {"dry"}

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "mistral": "ministral-14b-latest",
    "openrouter": "google/gemma-4-26b-a4b-it:free",
    "google": "gemma-4-26b-a4b-it",
    "openai": "gpt-6-luna",
    "dry": "dry-northstar-v1",
}

# USD per million tokens (input, output). Verify against the provider's pricing
# page before relying on cost figures; override with INPUT/OUTPUT_COST_PER_MTOK.
DEFAULT_PRICES = {
    "groq": (0.075, 0.30),
    "dry": (0.0, 0.0),
}


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    provider: str = "dry"
    model: str = DEFAULT_MODELS["dry"]
    api_key: str = field(default="", repr=False)
    openrouter_fallback_model: str = ""
    reasoning_effort: str = "low"
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    cache_ttl_seconds: int = 300
    cache_max_entries: int = 1024
    max_concurrency: int = 4
    request_timeout_seconds: float = 30.0
    queue_timeout_seconds: float = 10.0
    dry_latency_ms: float = 0.0
    trace_sink: str = "jsonl"
    trace_path: Path = ROOT / "generated" / "traces.jsonl"
    trace_content_sample_rate: float = 0.0
    trace_sample_rate: float = 1.0
    environment: str = "development"
    release: str = "local"

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported MODEL_PROVIDER: {self.provider}")
        if self.max_concurrency < 1:
            raise ValueError("MAX_CONCURRENT_REQUESTS must be >= 1")
        if self.cache_ttl_seconds < 0 or self.cache_max_entries < 1:
            raise ValueError("Cache TTL must be >= 0 and max entries >= 1")
        for name in ("request_timeout_seconds", "queue_timeout_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("trace_content_sample_rate", "trace_sample_rate"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.trace_sink not in {"jsonl", "langfuse", "none"}:
            raise ValueError(f"Unsupported TRACE_SINK: {self.trace_sink}")

    @property
    def is_live(self) -> bool:
        return self.provider in LIVE_PROVIDERS

    @classmethod
    def from_env(cls) -> Settings:
        if os.getenv("PYTHON_DOTENV_DISABLED") != "1":
            load_dotenv(ROOT / ".env")
        provider = os.getenv("MODEL_PROVIDER", "dry").strip().lower()
        prefix = provider.upper()
        model = (
            DEFAULT_MODELS["dry"]
            if provider == "dry"
            else os.getenv(f"{prefix}_MODEL", DEFAULT_MODELS.get(provider, ""))
        )
        in_price, out_price = DEFAULT_PRICES.get(provider, (0.0, 0.0))
        return cls(
            provider=provider,
            model=model,
            api_key="" if provider == "dry" else os.getenv(f"{prefix}_API_KEY", ""),
            openrouter_fallback_model=os.getenv("OPENROUTER_FALLBACK_MODEL", ""),
            reasoning_effort=os.getenv("REASONING_EFFORT", "low"),
            input_cost_per_mtok=_float("INPUT_COST_PER_MTOK", in_price),
            output_cost_per_mtok=_float("OUTPUT_COST_PER_MTOK", out_price),
            cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 300),
            cache_max_entries=_int("CACHE_MAX_ENTRIES", 1024),
            max_concurrency=_int("MAX_CONCURRENT_REQUESTS", 4),
            request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", 30),
            queue_timeout_seconds=_float("QUEUE_TIMEOUT_SECONDS", 10),
            dry_latency_ms=_float("DRY_LATENCY_MS", 0),
            trace_sink=os.getenv("TRACE_SINK", "jsonl").strip().lower(),
            trace_path=Path(
                os.getenv("TRACE_PATH", str(ROOT / "generated" / "traces.jsonl"))
            ),
            trace_content_sample_rate=_float("TRACE_CONTENT_SAMPLE_RATE", 0.0),
            trace_sample_rate=_float("TRACE_SAMPLE_RATE", 1.0),
            environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development"),
            release=os.getenv(
                "LANGFUSE_RELEASE", os.getenv("RENDER_GIT_COMMIT", "local")[:12]
            ),
        )
