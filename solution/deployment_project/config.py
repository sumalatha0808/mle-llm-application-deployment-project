"""Reference application configuration."""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Environment-backed deployment settings."""

    provider: str = os.getenv("MODEL_PROVIDER", "groq")
    api_key: str = os.getenv(f"{provider.upper()}_API_KEY", "")
    model: str = os.getenv(
        f"{provider.upper()}_MODEL" if provider != "dry" else "GROQ_MODEL",
        {
            "google": "gemma-4-26b-a4b-it",
            "openai": "gpt-6-luna",
            "mistral": "ministral-14b-latest",
            "openrouter": "google/gemma-4-26b-a4b-it:free",
        }.get(provider, "openai/gpt-oss-20b"),
    )
    openrouter_fallback_model: str = os.getenv(
        "OPENROUTER_FALLBACK_MODEL", "openrouter/free"
    )
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    max_concurrency: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "4"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    trace_sink: str = os.getenv("TRACE_SINK", "jsonl")
    trace_path: Path = Path(os.getenv("TRACE_PATH", "solution/generated/traces.jsonl"))
    environment: str = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development")
    release: str = os.getenv("LANGFUSE_RELEASE", "local")

    def __post_init__(self) -> None:
        if self.max_concurrency < 1 or self.cache_ttl_seconds < 0:
            raise ValueError("Concurrency must be positive and cache TTL nonnegative")
        if (
            not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("Request timeout must be finite and positive")


settings = Settings()
