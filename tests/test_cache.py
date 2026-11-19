"""Cache key, TTL, bounds and single-flight behaviour."""

import asyncio

import pytest

from src.cache import TTLCache, cache_key
from src.models import GenerateRequest
from src.service import GenerationService
from tests.helpers import SlowProvider, make_settings

BASE = {"provider": "dry", "model": "m", "policy_version": "v1"}


def test_cache_key_covers_every_output_affecting_input():
    request = GenerateRequest(prompt="hello")
    key = cache_key(request, **BASE)
    variants = [
        cache_key(GenerateRequest(prompt="hello!"), **BASE),
        cache_key(GenerateRequest(prompt="hello", temperature=0.5), **BASE),
        cache_key(GenerateRequest(prompt="hello", max_tokens=10), **BASE),
        cache_key(GenerateRequest(prompt="hello", top_p=0.5), **BASE),
        cache_key(request, **{**BASE, "model": "other"}),
        cache_key(request, **{**BASE, "provider": "groq"}),
        cache_key(request, **{**BASE, "policy_version": "v2"}),
    ]
    assert key not in variants and len(set(variants)) == len(variants)
    # Whitespace stripping happens in validation, so equivalent prompts share a key.
    assert cache_key(GenerateRequest(prompt="  hello "), **BASE) == key


def test_ttl_expiry_and_lru_bound():
    now = [0.0]
    cache: TTLCache[str] = TTLCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cache.set("a", "A")
    now[0] = 9.9
    assert cache.get("a") == "A"
    now[0] = 10.0
    assert cache.get("a") is None
    cache.set("a", "A")
    cache.set("b", "B")
    cache.get("a")  # a becomes most recently used
    cache.set("c", "C")
    assert cache.get("b") is None and cache.get("a") == "A" and len(cache) == 2


def test_zero_ttl_disables_caching():
    cache: TTLCache[str] = TTLCache(ttl_seconds=0)
    cache.set("a", "A")
    assert cache.get("a") is None


def test_concurrent_identical_misses_share_one_provider_call(tmp_path):
    provider = SlowProvider(delay_s=0.1)
    service = GenerationService(provider, make_settings(tmp_path))

    async def burst():
        request = GenerateRequest(prompt="locked account")
        return await asyncio.gather(*(service.generate(request) for _ in range(5)))

    responses = asyncio.run(burst())
    assert provider.calls == 1
    assert sum(not r.cache_hit for r in responses) == 1


def test_failures_propagate_to_waiters_and_are_not_cached():
    cache: TTLCache[str] = TTLCache(ttl_seconds=60)

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream")

    async def scenario():
        return await asyncio.gather(
            cache.get_or_compute("k", boom),
            cache.get_or_compute("k", boom),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert all(isinstance(r, RuntimeError) for r in results)
    assert cache.get("k") is None


def test_concurrency_is_bounded(tmp_path):
    provider = SlowProvider(delay_s=0.05)
    service = GenerationService(provider, make_settings(tmp_path, max_concurrency=2))
    peak = 0

    async def watch():
        nonlocal peak
        while True:
            peak = max(peak, service.in_flight)
            await asyncio.sleep(0.005)

    async def scenario():
        watcher = asyncio.create_task(watch())
        await asyncio.gather(
            *(
                service.generate(GenerateRequest(prompt=f"question {i}"))
                for i in range(6)
            )
        )
        watcher.cancel()

    asyncio.run(scenario())
    assert provider.calls == 6 and peak == 2


@pytest.mark.parametrize("ttl", [-1])
def test_negative_ttl_rejected(tmp_path, ttl):
    with pytest.raises(ValueError):
        make_settings(tmp_path, cache_ttl_seconds=ttl)
