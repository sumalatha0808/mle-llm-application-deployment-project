"""Keep unit tests independent of live-service settings before app imports."""

import os

import pytest

os.environ.update(
    {
        "PYTHON_DOTENV_DISABLED": "1",
        "MODEL_PROVIDER": "dry",
        "GROQ_API_KEY": "",
        "GROQ_MODEL": "openai/gpt-oss-20b",
        "CACHE_TTL_SECONDS": "300",
        "MAX_CONCURRENT_REQUESTS": "4",
        "REQUEST_TIMEOUT_SECONDS": "30",
        "TRACE_SINK": "jsonl",
        "LANGFUSE_TRACING_ENABLED": "false",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
    }
)


@pytest.fixture(autouse=True)
def isolated_api_traces(tmp_path, monkeypatch):
    from solution.deployment_project.app import service
    from solution.deployment_project.tracing import JsonlTraceSink

    monkeypatch.setattr(service, "trace_sink", JsonlTraceSink(tmp_path / "api.jsonl"))
    monkeypatch.setattr(service, "cache", {})
