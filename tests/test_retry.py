from __future__ import annotations

import asyncio
import math

import pytest

from relay.retry import GlobalRetryBudget, RetryPolicy


def policy(**overrides) -> RetryPolicy:
    values = {"max_attempts": 3, "base_delay_s": 0.5, "max_delay_s": 2.0}
    values.update(overrides)
    return RetryPolicy(**values)


def test_retry_after_is_respected_and_budget_reserved() -> None:
    budget = GlobalRetryBudget(limit=1)

    async def decide():
        return await policy().decide(
            attempt=1,
            status="rate_limited",
            retry_after_s=1.25,
            deadline_monotonic=10,
            now_monotonic=2,
            budget=budget,
        )

    result = asyncio.run(decide())
    assert result.retry is True
    assert result.delay_s == 1.25
    assert budget.used == 1


def test_full_jitter_uses_attempt_window() -> None:
    async def decide():
        return await policy(random_value=lambda: 0.5).decide(
            attempt=2,
            status="server_error",
            deadline_monotonic=10,
            now_monotonic=1,
            budget=GlobalRetryBudget(limit=1),
        )

    result = asyncio.run(decide())
    assert result.retry is True
    assert result.delay_s == 0.5


@pytest.mark.parametrize("status", ["error", "ok", "invalid_request"])
def test_non_retryable_failures_do_not_consume_global_budget(status: str) -> None:
    budget = GlobalRetryBudget(limit=2)

    async def decide():
        return await policy().decide(
            attempt=1,
            status=status,
            deadline_monotonic=10,
            now_monotonic=1,
            budget=budget,
        )

    result = asyncio.run(decide())
    assert result.retry is False
    assert budget.used == 0


def test_retry_is_skipped_when_it_would_miss_deadline() -> None:
    budget = GlobalRetryBudget(limit=2)

    async def decide():
        return await policy().decide(
            attempt=1,
            status="rate_limited",
            retry_after_s=2,
            deadline_monotonic=3,
            now_monotonic=1.5,
            budget=budget,
        )

    result = asyncio.run(decide())
    assert result.retry is False
    assert result.reason == "retry would miss the request deadline"
    assert budget.used == 0


def test_global_retry_budget_stops_retry_storms() -> None:
    budget = GlobalRetryBudget(limit=1)

    async def decide_twice():
        p = policy(random_value=lambda: 0)
        first = await p.decide(
            attempt=1,
            status="timeout",
            deadline_monotonic=10,
            now_monotonic=1,
            budget=budget,
        )
        second = await p.decide(
            attempt=1,
            status="timeout",
            deadline_monotonic=10,
            now_monotonic=1,
            budget=budget,
        )
        return first, second

    first, second = asyncio.run(decide_twice())
    assert first.retry is True
    assert second.retry is False
    assert second.reason == "global retry budget exhausted"


@pytest.mark.parametrize("retry_after", [math.nan, math.inf, -math.inf])
def test_non_finite_retry_after_is_rejected_without_using_budget(retry_after: float) -> None:
    budget = GlobalRetryBudget(limit=1)

    async def decide():
        return await policy().decide(
            attempt=1,
            status="rate_limited",
            retry_after_s=retry_after,
            deadline_monotonic=10,
            now_monotonic=1,
            budget=budget,
        )

    result = asyncio.run(decide())
    assert result.retry is False
    assert result.reason == "invalid retry-after value"
    assert budget.used == 0


def test_non_finite_retry_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="delay bounds"):
        RetryPolicy(max_attempts=2, base_delay_s=math.nan, max_delay_s=2)
