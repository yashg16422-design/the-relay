from __future__ import annotations

import asyncio

import pytest

from relay.cache import AnswerCache


def test_cache_hit_skips_factory() -> None:
    async def run():
        cache: AnswerCache[str] = AnswerCache(ttl_seconds=30, max_entries=2)
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return "answer"

        first = await cache.get_or_create("q", factory)
        second = await cache.get_or_create("q", factory)
        return calls, first, second

    calls, first, second = asyncio.run(run())
    assert calls == 1
    assert first.value == second.value == "answer"
    assert first.cache_hit is False
    assert second.cache_hit is True


def test_concurrent_identical_requests_are_coalesced() -> None:
    async def run():
        cache: AnswerCache[str] = AnswerCache(ttl_seconds=0, max_entries=2)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared"

        first_task = asyncio.create_task(cache.get_or_create("same", factory))
        await started.wait()
        second_task = asyncio.create_task(cache.get_or_create("same", factory))
        await asyncio.sleep(0)
        release.set()
        return calls, await first_task, await second_task

    calls, first, second = asyncio.run(run())
    assert calls == 1
    assert first.value == second.value == "shared"
    assert first.coalesced is False
    assert second.coalesced is True


def test_expired_entry_is_recomputed() -> None:
    async def run():
        cache: AnswerCache[int] = AnswerCache(ttl_seconds=0.01, max_entries=1)
        value = 0

        async def factory() -> int:
            nonlocal value
            value += 1
            return value

        first = await cache.get_or_create("key", factory)
        await asyncio.sleep(0.02)
        second = await cache.get_or_create("key", factory)
        return first, second

    first, second = asyncio.run(run())
    assert first.value == 1
    assert second.value == 2
    assert second.cache_hit is False


def test_failed_factory_is_not_cached() -> None:
    async def run():
        cache: AnswerCache[str] = AnswerCache(ttl_seconds=30, max_entries=2)

        async def fail() -> str:
            raise ValueError("model failed")

        with pytest.raises(ValueError, match="model failed"):
            await cache.get_or_create("q", fail)

        async def succeed() -> str:
            return "recovered"

        return await cache.get_or_create("q", succeed)

    result = asyncio.run(run())
    assert result.value == "recovered"
