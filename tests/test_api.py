"""HTTP contract: health, readiness, generation, validation and failures."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.config import Settings
from src.provider import DryProvider
from tests.helpers import FailingProvider, SlowProvider, make_settings

REFUND = "What evidence is required for a duplicate-payment refund?"


@pytest.fixture
def dry(tmp_path):
    provider = DryProvider()
    return TestClient(create_app(make_settings(tmp_path), provider)), provider


def test_health_and_ready(dry):
    client, provider = dry
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["cache"] == "process-local"
    assert provider.calls == 0  # readiness never calls the provider


def test_repeated_request_misses_then_hits(dry):
    client, provider = dry
    first = client.post("/generate", json={"prompt": REFUND})
    second = client.post("/generate", json={"prompt": REFUND})
    assert first.status_code == second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert provider.calls == 1
    assert first.json()["trace_id"] != second.json()["trace_id"]
    assert "receipt" in first.json()["text"] and first.json()["quality_score"] >= 0.8


def test_changed_parameter_is_a_cache_miss(dry):
    client, provider = dry
    client.post("/generate", json={"prompt": REFUND})
    other = client.post("/generate", json={"prompt": REFUND, "temperature": 0.7})
    assert other.json()["cache_hit"] is False
    assert provider.calls == 2


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": ""},
        {"prompt": "   "},
        {},
        {"prompt": "x" * 4001},
        {"prompt": REFUND, "max_tokens": 0},
        {"prompt": REFUND, "temperature": 3},
        {"prompt": REFUND, "unexpected": True},
    ],
)
def test_invalid_input_rejected_before_provider(dry, body):
    client, provider = dry
    assert client.post("/generate", json=body).status_code == 422
    assert provider.calls == 0


def test_timeout_reaches_provider_and_returns_504(tmp_path):
    provider = SlowProvider(delay_s=1.0)
    client = TestClient(
        create_app(make_settings(tmp_path, request_timeout_seconds=0.05), provider)
    )
    response = client.post("/generate", json={"prompt": REFUND})
    assert response.status_code == 504
    assert response.json()["error"] == "upstream_timeout"
    assert response.json()["trace_id"]
    assert provider.calls == 1


def test_upstream_failure_returns_502_and_is_not_cached(tmp_path):
    provider = FailingProvider()
    client = TestClient(create_app(make_settings(tmp_path), provider))
    for _ in range(2):
        response = client.post("/generate", json={"prompt": REFUND})
        assert response.status_code == 502
        assert response.json()["error"] == "upstream_error"
    assert provider.calls == 2  # failures are not cached


def test_missing_api_key_fails_readiness_not_liveness(tmp_path):
    client = TestClient(
        create_app(make_settings(tmp_path, provider="groq", model="m", api_key=""))
    )
    assert client.get("/health").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert "API_KEY" in ready.json()["problems"][0]
    assert client.post("/generate", json={"prompt": REFUND}).status_code == 503


def test_saturation_returns_503_overloaded(tmp_path):
    from httpx import ASGITransport, AsyncClient

    provider = SlowProvider(delay_s=0.3)
    app = create_app(
        make_settings(tmp_path, max_concurrency=1, queue_timeout_seconds=0.05), provider
    )

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await asyncio.gather(
                client.post("/generate", json={"prompt": "locked account"}),
                client.post("/generate", json={"prompt": "active incident"}),
            )

    statuses = sorted(r.status_code for r in asyncio.run(scenario()))
    assert statuses == [200, 503]


def test_invalid_env_config_fails_readiness(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nope")
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    monkeypatch.setenv("MODEL_PROVIDER", "dry")
    assert Settings.from_env().provider == "dry"
