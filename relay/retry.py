"""Deadline-aware retry decisions and a process-wide retry budget."""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_s: float = 0.0
    reason: str = ""


class GlobalRetryBudget:
    """Caps retries across all requests so failure storms cannot multiply traffic."""

    def __init__(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("retry budget limit cannot be negative")
        self.limit = limit
        self.used = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> bool:
        async with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


class RetryPolicy:
    def __init__(
        self,
        *,
        max_attempts: int,
        base_delay_s: float,
        max_delay_s: float,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must include at least the first attempt")
        if (
            not math.isfinite(base_delay_s)
            or not math.isfinite(max_delay_s)
            or base_delay_s < 0
            or max_delay_s < base_delay_s
        ):
            raise ValueError("retry delay bounds are inconsistent")
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self._random = random_value or random.random

    async def decide(
        self,
        *,
        attempt: int,
        status: str,
        deadline_monotonic: float,
        budget: GlobalRetryBudget,
        now_monotonic: float | None = None,
        retry_after_s: float | None = None,
    ) -> RetryDecision:
        """Decide whether to retry after a completed attempt (1-based)."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        now = now_monotonic if now_monotonic is not None else asyncio.get_running_loop().time()
        if not math.isfinite(now) or not math.isfinite(deadline_monotonic):
            return RetryDecision(False, reason="deadline or current time is not finite")
        if status not in {"timeout", "rate_limited", "server_error"}:
            return RetryDecision(False, reason="failure class is not retryable")
        if attempt >= self.max_attempts:
            return RetryDecision(False, reason="per-request attempt limit reached")
        if retry_after_s is not None:
            if not math.isfinite(retry_after_s) or retry_after_s < 0:
                return RetryDecision(False, reason="invalid retry-after value")
            delay = retry_after_s
        else:
            window = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
            random_value = self._random()
            if not math.isfinite(random_value):
                return RetryDecision(False, reason="random source returned a non-finite value")
            delay = min(1.0, max(0.0, random_value)) * window
        if now + delay >= deadline_monotonic:
            return RetryDecision(False, reason="retry would miss the request deadline")
        if not await budget.reserve():
            return RetryDecision(False, reason="global retry budget exhausted")
        return RetryDecision(True, delay_s=delay, reason="retryable failure within budget")
