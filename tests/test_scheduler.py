from __future__ import annotations

import asyncio

from relay.scheduler import DeadlinePriorityQueue, RequestEnvelope, choose_degradation


def request(name: str, priority: int, deadline: float = 100) -> RequestEnvelope:
    return RequestEnvelope(name, priority, deadline, 0, {"question": name})


def test_priority_queue_returns_urgent_request_first() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=3)
        await queue.enqueue(request("normal", 10), now=1)
        await queue.enqueue(request("urgent", 1), now=1)
        first = await queue.dequeue(now=2)
        second = await queue.dequeue(now=2)
        await queue.task_done()
        await queue.task_done()
        return first, second

    first, second = asyncio.run(run())
    assert first.request.request_id == "urgent"
    assert second.request.request_id == "normal"


def test_full_queue_sheds_lowest_priority_for_urgent_work() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=1)
        await queue.enqueue(request("normal", 10), now=1)
        return await queue.enqueue(request("urgent", 1), now=1), await queue.dequeue(now=2)

    enqueue, item = asyncio.run(run())
    assert enqueue.accepted is True
    assert enqueue.shed_request_id == "normal"
    assert item.request.request_id == "urgent"


def test_full_queue_rejects_equal_or_less_urgent_arrival() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=1)
        await queue.enqueue(request("first", 5), now=1)
        return await queue.enqueue(request("second", 5), now=1)

    result = asyncio.run(run())
    assert result.accepted is False
    assert result.reason == "queue full; incoming request shed"


def test_enqueue_prunes_expired_items_before_shedding() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=1)
        await queue.enqueue(request("expired", 1, deadline=3), now=1)
        result = await queue.enqueue(request("live", 10, deadline=20), now=4)
        item = await queue.dequeue(now=5)
        return result, item

    result, item = asyncio.run(run())
    assert result.accepted is True
    assert result.expired_request_ids == ("expired",)
    assert item.request.request_id == "live"


def test_dequeue_discards_expired_requests_before_serving() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=2)
        await queue.enqueue(request("expired", 1, deadline=3), now=1)
        await queue.enqueue(request("valid", 5, deadline=10), now=1)
        return await queue.dequeue(now=4)

    result = asyncio.run(run())
    assert result.expired_request_ids == ("expired",)
    assert result.request.request_id == "valid"


def test_dequeue_returns_when_every_queued_request_is_expired() -> None:
    async def run():
        queue = DeadlinePriorityQueue(capacity=1)
        await queue.enqueue(request("expired", 1, deadline=3), now=1)
        return await queue.dequeue(now=4)

    result = asyncio.run(run())
    assert result.request is None
    assert result.expired_request_ids == ("expired",)


def test_degradation_uses_only_supplied_measured_estimates() -> None:
    result = choose_degradation(
        remaining_s=1.2,
        estimates_s={"full": 3.0, "lite": 1.0, "extractive": 0.2},
        allow_wait=False,
    )
    assert result.level == "lite"
    assert result.estimated_latency_s == 1.0


def test_unknown_estimates_wait_or_drop_without_guessing() -> None:
    wait = choose_degradation(
        remaining_s=1,
        estimates_s={"full": None, "lite": None, "extractive": None},
        allow_wait=True,
    )
    drop = choose_degradation(
        remaining_s=1,
        estimates_s={"full": None, "lite": None, "extractive": None},
        allow_wait=False,
    )
    assert wait.level == "wait"
    assert drop.level == "drop"
