"""TTL answer cache with single-flight request coalescing."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar


T = TypeVar("T")
Factory = Callable[[], Awaitable[T]]


@dataclass(frozen=True)
class CacheResult(Generic[T]):
    value: T
    cache_hit: bool
    coalesced: bool


class AnswerCache(Generic[T]):
    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[T]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key_for(value: Any) -> str:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def get_or_create(
        self,
        key: str,
        factory: Factory[T],
        *,
        should_cache: Callable[[T], bool] | None = None,
    ) -> CacheResult[T]:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expiry, value = entry
                if expiry > now:
                    self._entries.move_to_end(key)
                    return CacheResult(value=value, cache_hit=True, coalesced=False)
                del self._entries[key]

            task = self._inflight.get(key)
            coalesced = task is not None
            if task is None:
                task = asyncio.create_task(
                    self._produce(key, factory, should_cache=should_cache)
                )
                self._inflight[key] = task

        value = await asyncio.shield(task)
        return CacheResult(value=value, cache_hit=False, coalesced=coalesced)

    async def _produce(
        self,
        key: str,
        factory: Factory[T],
        *,
        should_cache: Callable[[T], bool] | None,
    ) -> T:
        try:
            value = await factory()
            retain = should_cache is None or should_cache(value)
            if self.ttl_seconds > 0 and retain:
                async with self._lock:
                    self._entries[key] = (
                        asyncio.get_running_loop().time() + self.ttl_seconds,
                        value,
                    )
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.max_entries:
                        self._entries.popitem(last=False)
            return value
        finally:
            async with self._lock:
                self._inflight.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()

    async def size(self) -> int:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            expired = [key for key, (expiry, _) in self._entries.items() if expiry <= now]
            for key in expired:
                del self._entries[key]
            return len(self._entries)
