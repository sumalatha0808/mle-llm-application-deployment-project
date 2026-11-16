"""Reference API tests."""

import asyncio

import httpx2
from httpx import ASGITransport, AsyncClient

from solution.deployment_project.app import app, service
from solution.deployment_project.config import Settings
from solution.deployment_project.provider import ChatProvider


async def request(method: str, path: str, payload: dict[str, object] | None = None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, json=payload)


def test_health_and_generation_contract() -> None:
    health = asyncio.run(request("GET", "/health"))
    assert health.status_code == 200
    readiness = asyncio.run(request("GET", "/ready"))
    assert readiness.json()["model"] == "dry-northstar-v1"
    response = asyncio.run(request("POST", "/generate", {"prompt": "locked account"}))
    assert response.status_code == 200
    assert "identity" in response.json()["text"].lower()
    assert response.json()["model"] == readiness.json()["model"]


def test_invalid_prompt_is_rejected() -> None:
    assert asyncio.run(request("POST", "/generate", {"prompt": ""})).status_code == 422


def test_provider_errors_do_not_leak_details(monkeypatch) -> None:
    class FailingProvider:
        async def generate(self, request):
            raise RuntimeError("secret provider response")

    monkeypatch.setattr(service, "provider", FailingProvider())
    response = asyncio.run(request("POST", "/generate", {"prompt": "error-path-check"}))

    assert response.status_code == 502
    assert response.json()["detail"] == "Generation provider unavailable"


def test_openai_connect_timeout_returns_504(monkeypatch) -> None:
    async def connect_timeout(client, request, **kwargs):
        raise httpx2.ConnectTimeout("secret provider response", request=request)

    monkeypatch.setattr(httpx2.AsyncClient, "send", connect_timeout)
    monkeypatch.setattr(
        service,
        "provider",
        ChatProvider(Settings(provider="openai", api_key="test-only")),
    )
    response = asyncio.run(
        request("POST", "/generate", {"prompt": "openai-timeout-check"})
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "Generation request timed out"}
