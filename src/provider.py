"""Provider boundary: the only module that talks to an LLM.

Tests replace the provider with any object satisfying ``Provider``. Providers
raise ``ProviderTimeout`` or ``ProviderError`` so the service and API can map
upstream failures to explicit responses without knowing SDK exception types.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from src.config import Settings
from src.models import GenerateRequest, ProviderResult, Usage
from src.policy import DECLINE_SENTENCE, build_messages, classify_topic


class ProviderError(Exception):
    """The upstream provider failed or returned an unusable answer.

    ``usage`` carries tokens the provider reported (and billed) for an
    unusable answer, so failed calls still show up in token/cost totals.
    """

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class ProviderTimeout(ProviderError):
    """The upstream provider did not answer within the deadline."""


class Provider(Protocol):
    model: str

    async def generate(self, request: GenerateRequest) -> ProviderResult: ...


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for the dry provider."""
    return max(1, len(text) // 4)


DRY_ANSWERS = {
    "refund": (
        "Collect the customer's receipt and the transaction ID of both charges. "
        "Verify the duplicate in the billing system, then submit the refund to "
        "finance for review without promising an amount or date."
    ),
    "account_access": (
        "Verify the customer's identity with the registered email and one "
        "additional security check, then trigger the standard password reset "
        "flow. Never reveal account details."
    ),
    "privacy": (
        "Do not delete the data yourself. Log the request and route it to the "
        "privacy team for human review, without promising a deadline."
    ),
    "incident": (
        "Share only confirmed information and point customers to the status "
        "page for updates. Do not speculate about causes or recovery times."
    ),
    "unsupported": f"{DECLINE_SENTENCE} Please escalate this question to a team lead.",
}


class DryProvider:
    """Deterministic, offline provider that follows the approved policy."""

    def __init__(
        self, model: str = "dry-northstar-v1", latency_ms: float = 0.0
    ) -> None:
        self.model = model
        self.latency_ms = latency_ms
        self.calls = 0

    async def generate(self, request: GenerateRequest) -> ProviderResult:
        self.calls += 1
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        return self.answer(request)

    def answer(self, request: GenerateRequest) -> ProviderResult:
        text = DRY_ANSWERS[classify_topic(request.prompt)]
        prompt_text = "".join(content for _, content in build_messages(request.prompt))
        return ProviderResult(
            text=text,
            model=self.model,
            usage=Usage(
                input_tokens=estimate_tokens(prompt_text),
                output_tokens=min(estimate_tokens(text), request.max_tokens),
            ),
        )


class ChatProvider:
    """Live provider through LangChain's ``init_chat_model``."""

    def __init__(self, settings: Settings) -> None:
        from langchain.chat_models import init_chat_model
        from pydantic import SecretStr

        if not settings.api_key:
            raise ValueError(f"{settings.provider.upper()}_API_KEY is not set")
        self.model = settings.model
        self.provider = settings.provider
        self.token_limit_key = (
            "max_output_tokens" if settings.provider == "google" else "max_tokens"
        )
        options: dict[str, Any] = {}
        if settings.provider == "openrouter":
            fallback = settings.openrouter_fallback_model.strip()
            options = {
                "model_kwargs": {
                    "models": [settings.model, fallback] if fallback else None
                },
                "openrouter_provider": {"require_parameters": True},
            }
        elif settings.provider == "google":
            options = {"vertexai": False}
        elif settings.provider == "openai":
            options = {"reasoning_effort": "none"}
        elif settings.provider == "groq" and "gpt-oss" in settings.model:
            # Reasoning tokens count against max_tokens. At the default effort
            # gpt-oss can spend the whole budget reasoning and return an empty
            # answer (finish_reason=length); "low" keeps the answer in budget.
            options = {"reasoning_effort": settings.reasoning_effort}
        self.client = init_chat_model(
            model=self.model,
            model_provider={"mistral": "mistralai", "google": "google_genai"}.get(
                settings.provider, settings.provider
            ),
            api_key=SecretStr(settings.api_key),
            timeout=int(settings.request_timeout_seconds * 1000)
            if settings.provider == "openrouter"
            else settings.request_timeout_seconds,
            max_retries=0,  # the service owns retries/deadlines, not the SDK
            **options,
        )

    async def generate(self, request: GenerateRequest) -> ProviderResult:
        params: dict[str, Any] = {
            "temperature": request.temperature,
            self.token_limit_key: request.max_tokens,
        }
        if request.top_p != 1.0:
            params["top_p"] = request.top_p
        try:
            response = await self.client.ainvoke(
                build_messages(request.prompt), **params
            )
        except TimeoutError as exc:
            raise ProviderTimeout("Provider request timed out") from exc
        except Exception as exc:  # SDK-specific errors stay behind the boundary
            if "timeout" in type(exc).__name__.lower():
                raise ProviderTimeout("Provider request timed out") from exc
            raise ProviderError(f"{type(exc).__name__} from provider") from exc
        raw_usage = response.usage_metadata or {}
        usage = Usage(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
        )
        finish = str(response.response_metadata.get("finish_reason", "stop")).lower()
        if finish not in {"stop", "end_turn", "complete"}:
            raise ProviderError(f"Incomplete answer (finish_reason={finish})", usage)
        text = response.text if isinstance(response.text, str) else response.text()
        if not text.strip():
            raise ProviderError("Provider returned an empty answer", usage)
        return ProviderResult(
            text=text.strip(),
            model=response.response_metadata.get("model_name") or self.model,
            usage=usage,
        )


def build_provider(settings: Settings) -> Provider:
    if settings.provider == "dry":
        return DryProvider(latency_ms=settings.dry_latency_ms)
    return ChatProvider(settings)
