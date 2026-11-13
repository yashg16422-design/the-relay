from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime, timedelta, timezone

from relay.llm_client import LLMRequestError, LLMResponse, StreamCapture, TokenUsage
from relay.cache import AnswerCache
from relay.pipeline import CitedAnswerPipeline, LIMIT_REPLIES, SAFE_REFUSAL
from relay.retrieval import PaperRetriever
from relay.retry import GlobalRetryBudget, RetryPolicy


CHUNK_ID = "0123456789abcdef"
CHUNK = {
    "chunk_id": CHUNK_ID,
    "paper_id": "1706.03762v7",
    "paper_title": "Attention Is All You Need",
    "page_number": 4,
    "page_start": 4,
    "page_end": 4,
    "section": "6.1 Results",
    "kind": "text",
    "setting": "R1",
    "text": "The Transformer uses attention mechanisms.",
}


class StubClient:
    def __init__(self, *, answerable: bool = True, drafts: list[str] | None = None) -> None:
        self.answerable = answerable
        self.drafts = list(drafts or [f"The Transformer uses attention. [[cite:{CHUNK_ID}]]"])
        self.complete_calls: list[str] = []
        self.stream_calls = 0

    async def complete(self, messages, **kwargs):
        system = messages[0]["content"]
        self.complete_calls.append(system)
        if "Rewrite the user's question" in system:
            text = json.dumps({"query": "Transformer attention"})
        else:
            text = json.dumps({"answerable": self.answerable, "reason": "test judgment"})
        return LLMResponse(
            text=text,
            model="stub",
            status_code=200,
            headers={},
            usage=TokenUsage(input_tokens=10, output_tokens=4, cached_tokens=0, reasoning_tokens=0),
            time_to_first_chunk_ms=None,
            total_ms=1,
        )

    async def stream(self, messages, *, capture: StreamCapture, **kwargs):
        self.stream_calls += 1
        answer = self.drafts.pop(0)
        for part in (answer[:15], answer[15:]):
            yield part
        capture.response = LLMResponse(
            text=answer,
            model="stub",
            status_code=200,
            headers={},
            usage=TokenUsage(input_tokens=20, output_tokens=9, cached_tokens=0, reasoning_tokens=0),
            time_to_first_chunk_ms=0.2,
            total_ms=1,
        )


def retriever() -> PaperRetriever:
    return PaperRetriever([CHUNK], setting="R1", top_k=1)


def test_pipeline_runs_understand_retrieve_judge_draft_gate() -> None:
    client = StubClient()
    pipeline = CitedAnswerPipeline(client, retriever())

    result = asyncio.run(
        pipeline.run(
            "How does the Transformer work?",
            request_id="r1",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert result.answerable is True
    assert result.retrieval_setting == "R1"
    assert result.retrieved_chunk_ids == (CHUNK_ID,)
    assert "arXiv:1706.03762v7" in result.answer
    assert result.first_token_ms is not None
    assert result.spans[0].name == "understand"
    assert "retrieve_tool" in [span.name for span in result.spans]
    assert "decide_answerability" in [span.name for span in result.spans]
    assert "sentence_gate" in [span.name for span in result.spans]


def test_unanswerable_branch_refuses_without_drafting() -> None:
    client = StubClient(answerable=False)
    pipeline = CitedAnswerPipeline(client, retriever())

    result = asyncio.run(
        pipeline.run(
            "Question not covered",
            request_id="r2",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert result.answer == SAFE_REFUSAL
    assert result.answerable is False
    assert client.stream_calls == 0
    assert result.gate_reason == "test judgment"


def test_gate_failure_repairs_once_then_returns_cited_answer() -> None:
    client = StubClient(
        drafts=[
            f"The Transformer achieved 99 BLEU. [[cite:{CHUNK_ID}]]",
            f"The Transformer uses attention. [[cite:{CHUNK_ID}]]",
        ]
    )
    pipeline = CitedAnswerPipeline(client, retriever(), max_repairs=1)

    result = asyncio.run(
        pipeline.run(
            "How does the Transformer work?",
            request_id="r3",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert result.repairs == 1
    assert client.stream_calls == 2
    assert "arXiv:1706.03762v7" in result.answer
    assert [span.name for span in result.spans].count("sentence_gate") == 2


def test_failed_repair_drops_to_safe_refusal() -> None:
    client = StubClient(
        drafts=[
            f"The Transformer achieved 99 BLEU. [[cite:{CHUNK_ID}]]",
            f"The Transformer achieved 100 BLEU. [[cite:{CHUNK_ID}]]",
        ]
    )
    pipeline = CitedAnswerPipeline(client, retriever(), max_repairs=1)

    result = asyncio.run(
        pipeline.run(
            "Question",
            request_id="r4",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert result.answer == SAFE_REFUSAL
    assert result.repairs == 1


def test_provider_call_is_cancelled_at_request_deadline() -> None:
    class SlowClient(StubClient):
        async def complete(self, messages, **kwargs):
            await asyncio.sleep(1)
            return await super().complete(messages, **kwargs)

    client = SlowClient()
    pipeline = CitedAnswerPipeline(client, retriever())
    started = time.monotonic()

    result = asyncio.run(
        pipeline.run(
            "Question",
            request_id="r-deadline",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 0.02,
        )
    )

    assert result.status == "timeout"
    assert result.total_ms < 250
    assert time.monotonic() - started < 0.25


def test_pipeline_persists_contract_results_and_request_trace(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    trace_dir = tmp_path / "traces"
    client = StubClient()
    pipeline = CitedAnswerPipeline(
        client, retriever(), results_path=results_path, trace_dir=trace_dir
    )

    result = asyncio.run(
        pipeline.run(
            "How does the Transformer work?",
            request_id="persist-1",
            question_id="transformer-1",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
            scenario="steady",
        )
    )

    with results_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert tuple(reader.fieldnames) == (
            "request_id", "scenario", "source", "start_time", "first_token_ms",
            "total_ms", "status", "retries", "tokens_in", "tokens_out",
            "retrieval_setting", "answer_grade",
        )
    assert len(rows) == 1
    assert rows[0]["request_id"] == result.request_id
    assert rows[0]["scenario"] == "steady"
    assert rows[0]["source"] == "stand-in"
    assert rows[0]["status"] == "ok"
    assert rows[0]["answer_grade"] == "not_graded"
    assert rows[0]["tokens_in"] == "40"
    assert rows[0]["tokens_out"] == "17"
    trace = json.loads((trace_dir / "persist-1.json").read_text())
    assert trace["request_id"] == "persist-1"
    assert trace["scenario"] == "steady"
    assert trace["question_id"] == "transformer-1"
    assert trace["question"] == "How does the Transformer work?"
    assert trace["answer"] == result.answer
    assert trace["retrieved_chunk_ids"] == list(result.retrieved_chunk_ids)
    assert trace["spans"][0]["cached_tokens"] == 0
    assert trace["spans"][0]["reasoning_tokens"] == 0


def test_pipeline_keeps_token_totals_blank_when_provider_usage_is_unknown(tmp_path) -> None:
    client = StubClient()
    original_complete = client.complete

    async def no_usage(messages, **kwargs):
        response = await original_complete(messages, **kwargs)
        return LLMResponse(
            text=response.text,
            model=response.model,
            status_code=response.status_code,
            headers=response.headers,
            usage=TokenUsage(),
            time_to_first_chunk_ms=response.time_to_first_chunk_ms,
            total_ms=response.total_ms,
        )

    client.complete = no_usage
    results_path = tmp_path / "results.csv"
    pipeline = CitedAnswerPipeline(client, retriever(), results_path=results_path)
    asyncio.run(
        pipeline.run(
            "How does the Transformer work?",
            request_id="unknown-usage",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    with results_path.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert row["tokens_in"] == ""
    assert row["tokens_out"] == ""


def test_pipeline_retries_retryable_provider_failure_within_deadline() -> None:
    class FailOnceClient(StubClient):
        def __init__(self):
            super().__init__()
            self.failed = False

        async def complete(self, messages, **kwargs):
            if not self.failed:
                self.failed = True
                raise LLMRequestError(
                    "temporary provider failure",
                    status_code=503,
                    headers={},
                    body="{}",
                )
            return await super().complete(messages, **kwargs)

    client = FailOnceClient()
    pipeline = CitedAnswerPipeline(
        client,
        retriever(),
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_s=0,
            max_delay_s=0,
            random_value=lambda: 0,
        ),
        retry_budget=GlobalRetryBudget(limit=1),
    )

    result = asyncio.run(
        pipeline.run(
            "How does the Transformer work?",
            request_id="retry-1",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert any(span.kind == "retry" for span in result.spans)


def test_pipeline_cache_returns_cached_answer_without_extra_provider_calls(tmp_path) -> None:
    client = StubClient()
    pipeline = CitedAnswerPipeline(
        client,
        retriever(),
        answer_cache=AnswerCache[object](ttl_seconds=30, max_entries=10),
        results_path=tmp_path / "results.csv",
        trace_dir=tmp_path / "traces",
    )

    async def run_twice():
        first = await pipeline.run(
            "How does the Transformer work?",
            request_id="cache-first",
            question_id="Q-cache-first",
            experiment="standin-test",
            deadline_monotonic=asyncio.get_running_loop().time() + 10,
        )
        calls_after_first = len(client.complete_calls) + client.stream_calls
        second = await pipeline.run(
            "How does the Transformer work?",
            request_id="cache-second",
            question_id="Q-cache-second",
            experiment="standin-test",
            deadline_monotonic=asyncio.get_running_loop().time() + 10,
        )
        return first, second, calls_after_first

    first, second, calls_after_first = asyncio.run(run_twice())

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.request_id == "cache-second"
    assert second.answer == first.answer
    assert second.first_token_ms is not None
    assert second.spans == ()
    assert len(client.complete_calls) + client.stream_calls == calls_after_first
    for request_id, question_id in (
        ("cache-first", "Q-cache-first"),
        ("cache-second", "Q-cache-second"),
    ):
        trace = json.loads((tmp_path / "traces" / f"{request_id}.json").read_text())
        assert trace["request_id"] == request_id
        assert trace["question_id"] == question_id
        assert trace["question"] == "How does the Transformer work?"
        assert trace["answer"] == first.answer
        assert trace["retrieved_chunk_ids"] == list(first.retrieved_chunk_ids)


def test_pipeline_single_flight_marks_concurrent_duplicate_as_coalesced(tmp_path) -> None:
    class SlowClient(StubClient):
        async def complete(self, messages, **kwargs):
            await asyncio.sleep(0.02)
            return await super().complete(messages, **kwargs)

    client = SlowClient()
    pipeline = CitedAnswerPipeline(
        client,
        retriever(),
        answer_cache=AnswerCache[object](ttl_seconds=30, max_entries=10),
        results_path=tmp_path / "results.csv",
        trace_dir=tmp_path / "traces",
    )

    async def run_both():
        deadline = asyncio.get_running_loop().time() + 10
        return await asyncio.gather(
            pipeline.run(
                "How does the Transformer work?",
                request_id="coalesce-a",
                question_id="Q-coalesce-a",
                experiment="standin-test",
                deadline_monotonic=deadline,
            ),
            pipeline.run(
                "How does the Transformer work?",
                request_id="coalesce-b",
                question_id="Q-coalesce-b",
                experiment="standin-test",
                deadline_monotonic=deadline,
            ),
        )

    first, second = asyncio.run(run_both())

    assert sum(result.coalesced for result in (first, second)) == 1
    assert first.answer == second.answer
    assert first.request_id != second.request_id
    assert len(client.complete_calls) == 2
    assert client.stream_calls == 1
    for request_id, question_id in (
        ("coalesce-a", "Q-coalesce-a"),
        ("coalesce-b", "Q-coalesce-b"),
    ):
        trace = json.loads((tmp_path / "traces" / f"{request_id}.json").read_text())
        assert trace["request_id"] == request_id
        assert trace["question_id"] == question_id
        assert trace["question"] == "How does the Transformer work?"
        assert trace["answer"] == first.answer
        assert trace["retrieved_chunk_ids"] == list(first.retrieved_chunk_ids)


def test_scheduled_queue_wait_is_included_in_total_and_trace() -> None:
    client = StubClient()
    pipeline = CitedAnswerPipeline(client, retriever())

    async def execute():
        loop = asyncio.get_running_loop()
        scheduled_monotonic = loop.time() - 0.025
        return await pipeline.run(
            "How does the Transformer work?",
            request_id="queue-wait",
            experiment="standin-test",
            deadline_monotonic=loop.time() + 10,
            scheduled_arrival=datetime.now(timezone.utc) - timedelta(milliseconds=25),
            scheduled_arrival_monotonic=scheduled_monotonic,
        )

    result = asyncio.run(execute())

    assert result.total_ms >= 20
    assert result.spans[0].name == "scheduler_wait"
    assert result.spans[0].kind == "wait"


def test_extractive_fallback_refuses_zero_overlap_retrieval_hit() -> None:
    pipeline = CitedAnswerPipeline(StubClient(), retriever())

    result = asyncio.run(
        pipeline.run_extractive(
            "How does quantum teleportation work?",
            request_id="irrelevant-extractive",
            deadline_monotonic=time.monotonic() + 10,
        )
    )

    assert result.status == "ok"
    assert result.answerable is False
    assert result.answer == SAFE_REFUSAL
    assert "insufficient lexical overlap" in result.gate_reason
    assert "sentence_gate" not in [span.name for span in result.spans]


def test_expired_request_returns_timeout_refusal() -> None:
    client = StubClient()
    pipeline = CitedAnswerPipeline(client, retriever())

    result = asyncio.run(
        pipeline.run(
            "Question",
            request_id="r5",
            experiment="standin-test",
            deadline_monotonic=time.monotonic() - 1,
        )
    )

    assert result.status == "timeout"
    assert result.answer == LIMIT_REPLIES["timeout"]
    assert client.complete_calls == []
