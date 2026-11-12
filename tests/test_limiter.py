from __future__ import annotations

import asyncio

from relay.limiter import AdaptiveLimiter, parse_duration


def test_groq_headers_map_only_documented_buckets_and_keep_unknowns_unknown() -> None:
    limiter = AdaptiveLimiter(additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60)

    async def run():
        await limiter.observe(
            "model-a",
            {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "40",
                "x-ratelimit-reset-requests": "2m",
                "x-ratelimit-limit-tokens": "12000",
                "x-ratelimit-remaining-tokens": "9000",
                "x-ratelimit-reset-tokens": "5s",
            },
            status_code=200,
            now_monotonic=10,
        )
        return await limiter.snapshots("model-a"), await limiter.admission(
            "model-a", request_tokens=2000, now_monotonic=11
        )

    snapshots, admission = asyncio.run(run())
    assert snapshots["rpd"].limit == 100
    assert snapshots["rpd"].remaining == 40
    assert snapshots["rpd"].reset_at == 130
    assert snapshots["tpm"].remaining == 9000
    assert "rpm" not in snapshots
    assert admission.allowed is True
    assert set(admission.unknown_buckets) == {"rpm", "tpd"}


def test_limit_exhaustion_and_reset_wait_are_reported() -> None:
    limiter = AdaptiveLimiter(additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60)

    async def run():
        await limiter.set_observed_limit(
            "m", "tpm", limit=100, remaining=5, reset_after_s=8, now_monotonic=2
        )
        return await limiter.admission("m", request_tokens=10, now_monotonic=3)

    result = asyncio.run(run())
    assert result.allowed is False
    assert result.blocked_by == ("tpm",)
    assert result.wait_seconds == 7


def test_429_honors_retry_after_and_reduces_concurrency() -> None:
    limiter = AdaptiveLimiter(additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60)

    async def run():
        initial = await limiter.effective_concurrency("m", 10)
        await limiter.observe(
            "m", {"retry-after": "3"}, status_code=429, now_monotonic=5, now_epoch=100
        )
        after_429 = await limiter.effective_concurrency("m", 10)
        admission = await limiter.admission("m", request_tokens=1, now_monotonic=6)
        await limiter.observe("m", {}, status_code=200, now_monotonic=9)
        after_success = await limiter.effective_concurrency("m", 10)
        return initial, after_429, admission, after_success

    initial, after_429, admission, after_success = asyncio.run(run())
    assert initial == 10
    assert after_429 == 5
    assert admission.allowed is False
    assert admission.wait_seconds == 2
    assert after_success == 6


def test_duration_parser_supports_provider_reset_format() -> None:
    assert parse_duration("2m59.56s") == 179.56
    assert parse_duration("7.5") == 7.5
    assert parse_duration("nonsense") is None


def test_concurrent_admission_reserves_observed_capacity_atomically() -> None:
    limiter = AdaptiveLimiter(additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60)

    async def run():
        await limiter.set_observed_limit(
            "m", "rpd", limit=1, remaining=1, reset_after_s=30, now_monotonic=1
        )
        first = await limiter.reserve("m", request_tokens=0, now_monotonic=2)
        second = await limiter.reserve("m", request_tokens=0, now_monotonic=2)
        await limiter.reconcile(
            first.reservation_id,
            {"x-ratelimit-remaining-requests": "0"},
            status_code=200,
            now_monotonic=3,
            now_epoch=100,
        )
        third = await limiter.reserve("m", request_tokens=0, now_monotonic=3)
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first.allowed is True
    assert second.allowed is False
    assert second.blocked_by == ("rpd",)
    assert third.allowed is False


def test_missing_bucket_header_marks_snapshot_unknown() -> None:
    limiter = AdaptiveLimiter(additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60)

    async def run():
        await limiter.observe(
            "m",
            {
                "x-ratelimit-limit-requests": "5",
                "x-ratelimit-remaining-requests": "1",
                "x-ratelimit-reset-requests": "20s",
            },
            status_code=200,
            now_monotonic=1,
        )
        await limiter.observe("m", {"x-trace": "new-response"}, status_code=200, now_monotonic=2)
        snapshot = (await limiter.snapshots("m"))["rpd"]
        admission = await limiter.reserve("m", request_tokens=0, now_monotonic=3)
        return snapshot, admission

    snapshot, admission = asyncio.run(run())
    assert snapshot.remaining is None
    assert snapshot.source == "missing-or-invalid-header"
    assert admission.allowed is True
    assert "rpd" in admission.unknown_buckets
