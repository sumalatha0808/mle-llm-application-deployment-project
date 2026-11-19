"""Process-local TTL + LRU response cache with single-flight miss handling.

Scope: one cache per process. With N instances (or N uvicorn workers) each has
its own cache, so the hit rate drops and duplicate provider calls across
instances are possible. Within one process, concurrent identical misses share
one provider call (single-flight) instead of each calling the provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from src.models import GenerateRequest


def cache_key(
    request: GenerateRequest, *, provider: str, model: str, policy_version: str
) -> str:
    """Hash every output-affecting input: model, policy and all request params."""
    payload = {
        "provider": provider,
        "model": model,
        "policy_version": policy_version,
        **request.model_dump(),  # prompt, max_tokens, temperature, top_p
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class TTLCache[T]:
    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[T]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self.clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        if self.ttl_seconds <= 0:
            return
        self._entries[key] = (self.clock() + self.ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    async def get_or_compute(
        self, key: str, compute: Callable[[], Awaitable[T]]
    ) -> tuple[T, bool, bool]:
        """Return ``(value, cache_hit, shared_inflight)``.

        Failures are never cached; waiters on a failed in-flight call see the
        same exception.
        """
        cached = self.get(key)
        if cached is not None:
            return cached, True, False
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending), False, True
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await compute()
        except BaseException as exc:
            if not future.done():
                if isinstance(exc, asyncio.CancelledError):
                    exc = RuntimeError("Shared in-flight request was cancelled")
                future.set_exception(exc)
                future.exception()  # mark retrieved: avoid "never retrieved" logs
            raise
        else:
            self.set(key, value)
            future.set_result(value)
            return value, False, False
        finally:
            self._inflight.pop(key, None)
