"""Deadline-aware priority queue and evidence-driven degradation ladder."""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class RequestEnvelope:
    request_id: str
    priority: int
    deadline_monotonic: float
    arrival_monotonic: float
    payload: Any


@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    reason: str
    shed_request_id: str | None = None
    expired_request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DequeueResult:
    request: RequestEnvelope | None
    expired_request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DegradationDecision:
    level: Literal["full", "lite", "extractive", "wait", "drop"]
    estimated_latency_s: float | None
    reason: str


class DeadlinePriorityQueue:
    """Lower numeric priority is more urgent; expired work is discarded."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("queue capacity must be positive")
        self.capacity = capacity
        self._heap: list[tuple[int, int, RequestEnvelope]] = []
        self._sequence = 0
        self._condition = asyncio.Condition()
        self._unfinished = 0

    async def enqueue(self, request: RequestEnvelope, *, now: float | None = None) -> EnqueueResult:
        current = now if now is not None else asyncio.get_running_loop().time()
        if request.deadline_monotonic <= current:
            return EnqueueResult(False, "deadline already expired", expired_request_ids=(request.request_id,))
        async with self._condition:
            expired_items = [
                item for item in self._heap if item[2].deadline_monotonic <= current
            ]
            if expired_items:
                expired_sequences = {item[1] for item in expired_items}
                self._heap = [item for item in self._heap if item[1] not in expired_sequences]
                heapq.heapify(self._heap)
                self._unfinished -= len(expired_items)
                self._condition.notify_all()
            expired_ids = tuple(item[2].request_id for item in expired_items)
            shed_id: str | None = None
            if len(self._heap) >= self.capacity:
                worst_index = max(
                    range(len(self._heap)),
                    key=lambda index: (self._heap[index][0], -self._heap[index][1]),
                )
                worst_priority, _, worst_request = self._heap[worst_index]
                if request.priority >= worst_priority:
                    return EnqueueResult(
                        False,
                        "queue full; incoming request shed",
                        expired_request_ids=expired_ids,
                    )
                self._heap.pop(worst_index)
                heapq.heapify(self._heap)
                self._unfinished -= 1
                shed_id = worst_request.request_id

            self._sequence += 1
            heapq.heappush(self._heap, (request.priority, self._sequence, request))
            self._unfinished += 1
            self._condition.notify()
            return EnqueueResult(True, "queued", shed_id, expired_ids)

    async def dequeue(self, *, now: float | None = None) -> DequeueResult:
        expired: list[str] = []
        while True:
            async with self._condition:
                while not self._heap:
                    if expired:
                        return DequeueResult(None, tuple(expired))
                    await self._condition.wait()
                current = now if now is not None else asyncio.get_running_loop().time()
                _, _, request = heapq.heappop(self._heap)
                if request.deadline_monotonic <= current:
                    expired.append(request.request_id)
                    self._unfinished -= 1
                    continue
                return DequeueResult(request, tuple(expired))

    async def task_done(self) -> None:
        async with self._condition:
            if self._unfinished <= 0:
                raise ValueError("task_done called too many times")
            self._unfinished -= 1
            self._condition.notify_all()

    async def join(self) -> None:
        async with self._condition:
            while self._unfinished:
                await self._condition.wait()

    async def size(self) -> int:
        async with self._condition:
            return len(self._heap)


def choose_degradation(
    *,
    remaining_s: float,
    estimates_s: Mapping[str, float | None],
    allow_wait: bool,
) -> DegradationDecision:
    """Choose the richest measured path that can meet the remaining deadline."""
    if remaining_s <= 0:
        return DegradationDecision("drop", None, "deadline has expired")
    for level in ("full", "lite", "extractive"):
        estimate = estimates_s.get(level)
        if estimate is not None and estimate <= remaining_s:
            return DegradationDecision(level, estimate, "measured estimate fits deadline")
    if allow_wait:
        return DegradationDecision("wait", None, "no measured answer path fits yet")
    return DegradationDecision("drop", None, "no measured answer path fits deadline")
