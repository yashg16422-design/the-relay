from __future__ import annotations

import asyncio
import csv
import json
import time

import httpx

import load.loadgen as loadgen_module
from relay.cache import AnswerCache
from load.loadgen import (
    RESULT_FIELDS,
    Scenario,
    generate_arrivals,
    load_settings,
    run_scenario,
)
from relay.config import load_config
from relay.llm_client import LLMClient
from relay.limiter import AdaptiveLimiter
from relay.pipeline import CitedAnswerPipeline, PipelineResult
from relay.retrieval import PaperRetriever
from relay.scheduler import DequeueResult, EnqueueResult, RequestEnvelope
from standin.server import create_app, load_config as load_standin_config


PASSAGES = [
    {
        "chunk_id": "0123456789abcdef",
        "paper_id": "1706.03762",
        "paper_title": "Attention Is All You Need",
        "page_number": 3,
        "section": "3 Model Architecture",
        "text": "The Transformer allows for significantly more parallelization than recurrent models. It uses attention to connect encoder and decoder representations.",
    },
    {
        "chunk_id": "fedcba9876543210",
        "paper_id": "2301.00001",
        "paper_title": "A Different Paper",
        "page_number": 2,
        "section": "Introduction",
        "text": "This unrelated paper studies a different topic with other terminology.",
    },
]


def test_seeded_scenario_arrivals_repeat_exactly():
    scenario = Scenario(
        arrival_mode="poisson",
        max_requests=10,
        rate_per_minute=60,
        duration_seconds=30,
        patience_ms=1000,
        deadline_ms=2000,
        zipf_exponent=1.1,
        urgent_fraction=0.5,
    )

    first = generate_arrivals(scenario, ["one", "two", "three"], seed=8)
    second = generate_arrivals(scenario, ["one", "two", "three"], seed=8)

    assert first == second
    assert len(first) > 1
    assert [item.offset_seconds for item in first] == sorted(
        item.offset_seconds for item in first
    )
    assert {item.priority for item in first} == {0, 10}


def test_load_settings_scenarios_are_readable_and_named():
    settings = load_settings()

    assert {"steady", "burst", "sustained_overload"}.issubset(settings.scenarios)
    assert settings.seed == 20261109


def test_actual_pipeline_produces_contract_rows_and_persisted_trace(tmp_path):
    async def run():
        runtime_config = load_config()
        standin_app = create_app(load_standin_config())
        transport = httpx.ASGITransport(app=standin_app)
        client = LLMClient(
            runtime_config,
            transport=transport,
            ledger_path=tmp_path / "usage.csv",
            limiter=AdaptiveLimiter(
                additive_step=0.1,
                multiplicative_decrease=0.5,
                freshness_seconds=60,
            ),
        )
        pipeline = CitedAnswerPipeline(
            client,
            PaperRetriever(PASSAGES, setting="R1", top_k=2),
            answer_cache=AnswerCache(ttl_seconds=60, max_entries=16),
            results_path=tmp_path / "results.csv",
            trace_dir=tmp_path / "traces",
        )
        admin = httpx.AsyncClient(transport=transport, base_url="http://standin.local")
        scenario = Scenario(
            arrival_mode="burst",
            max_requests=2,
            burst_spread_ms=0,
            patience_ms=5000,
            deadline_ms=8000,
        )
        try:
            summary = await run_scenario(
                pipeline,
                "pipeline_smoke",
                scenario,
                ["How does the Transformer allow parallelization?"],
                seed=17,
                run_id="load-test",
                results_path=tmp_path / "results.csv",
                trace_dir=tmp_path / "traces",
                admin_client=admin,
            )
        finally:
            await client.aclose()
            await admin.aclose()
        return summary

    summary = asyncio.run(run())
    results_path = tmp_path / "results.csv"
    with results_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert tuple(reader.fieldnames or ()) == RESULT_FIELDS

    assert summary.status_counts == {"ok": 2}
    assert len(rows) == 2
    assert all(row["source"] == "stand-in" for row in rows)
    assert all(row["retrieval_setting"] == "R1" for row in rows)
    assert all(row["answer_grade"] == "not_graded" for row in rows)
    assert any(row["tokens_in"] and row["tokens_out"] for row in rows)

    trace_files = list((tmp_path / "traces").glob("*.json"))
    assert len(trace_files) == 2
    traces = [json.loads(path.read_text(encoding="utf-8")) for path in trace_files]
    assert {trace["request_id"] for trace in traces} == {row["request_id"] for row in rows}
    assert all(trace["scenario"] == "pipeline_smoke" for trace in traces)
    assert any(trace["cache_hit"] or trace["coalesced"] for trace in traces)
    assert any(
        span["name"] == "sentence_gate" and span["status"] == "ok"
        for trace in traces
        for span in trace["spans"]
    )


def test_timeouts_are_recorded_as_gave_up_and_timeout_rows(tmp_path):
    class SlowPipeline:
        class Client:
            class Config:
                provider = "standin"

            config = Config()

            async def aclose(self):
                return None

        client = Client()
        retriever = type("Retriever", (), {"setting": "R1"})()

        async def run(self, *args, **kwargs):
            await asyncio.sleep(0.2)

    scenario = Scenario(
        arrival_mode="burst",
        max_requests=1,
        patience_ms=20,
        deadline_ms=100,
    )
    summary = asyncio.run(
        run_scenario(
            SlowPipeline(),
            "patience_timeout",
            scenario,
            ["question"],
            seed=1,
            run_id="timeout-test",
            results_path=tmp_path / "results.csv",
            trace_dir=tmp_path / "traces",
        )
    )

    assert summary.status_counts == {"timeout": 1}
    assert summary.gave_up_count == 1
    trace = json.loads(next((tmp_path / "traces").glob("*.json")).read_text())
    assert trace["gave_up"] is True
    assert trace["question"] == "question"
    assert trace["question_id"] is None
    assert trace["answer"] == ""
    assert trace["retrieved_chunk_ids"] == []
    with (tmp_path / "results.csv").open(newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["status"] == "timeout"


def test_loadgen_routes_tight_deadline_to_citation_checked_extractive_path(tmp_path):
    async def run():
        runtime_config = load_config()
        standin_app = create_app(load_standin_config())
        transport = httpx.ASGITransport(app=standin_app)
        client = LLMClient(
            runtime_config,
            transport=transport,
            ledger_path=tmp_path / "usage.csv",
            limiter=AdaptiveLimiter(
                additive_step=0.1,
                multiplicative_decrease=0.5,
                freshness_seconds=60,
            ),
        )
        pipeline = CitedAnswerPipeline(
            client,
            PaperRetriever(PASSAGES, setting="R1", top_k=2),
            results_path=tmp_path / "results.csv",
            trace_dir=tmp_path / "traces",
        )
        scenario = Scenario(
            arrival_mode="burst",
            max_requests=1,
            burst_spread_ms=0,
            patience_ms=5000,
            deadline_ms=2000,
        )
        try:
            return await run_scenario(
                pipeline,
                "extractive_tight_deadline",
                scenario,
                ["How does the Transformer allow parallelization?"],
                seed=17,
                run_id="extractive-test",
                results_path=tmp_path / "results.csv",
                trace_dir=tmp_path / "traces",
            )
        finally:
            await client.aclose()

    summary = asyncio.run(run())
    assert summary.status_counts == {"ok": 1}
    trace = json.loads(next((tmp_path / "traces").glob("*.json")).read_text())
    assert trace["level"] == "extractive"
    assert trace["spans"][-1]["name"] == "sentence_gate"


def test_already_expired_enqueue_is_persisted_once(tmp_path, monkeypatch):
    class PipelineThatMustNotRun:
        class Client:
            class Config:
                provider = "standin"

            config = Config()

        client = Client()
        retriever = type("Retriever", (), {"setting": "R1"})()

        async def run(self, *args, **kwargs):
            raise AssertionError("an expired request must not enter the pipeline")

    async def reject_as_already_expired(_queue, request, *, now=None):
        return EnqueueResult(
            False,
            "deadline already expired",
            expired_request_ids=(request.request_id,),
        )

    monkeypatch.setattr(loadgen_module.DeadlinePriorityQueue, "enqueue", reject_as_already_expired)
    summary = asyncio.run(
        run_scenario(
            PipelineThatMustNotRun(),
            "expired_at_admission",
            Scenario(
                arrival_mode="burst",
                max_requests=1,
                burst_spread_ms=0,
                patience_ms=1000,
                deadline_ms=0.001,
            ),
            ["question"],
            seed=1,
            run_id="expired-test",
            results_path=tmp_path / "results.csv",
            trace_dir=tmp_path / "traces",
        )
    )

    assert summary.status_counts == {"timeout": 1}
    assert summary.gave_up_count == 1
    with (tmp_path / "results.csv").open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 1
    traces = list((tmp_path / "traces").glob("*.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text())
    assert trace["question"] == "question"
    assert trace["answer"] == ""
    assert trace["retrieved_chunk_ids"] == []


def test_accepted_priority_shed_persists_the_displaced_request(tmp_path, monkeypatch):
    class SheddingQueue:
        def __init__(self, capacity):
            self.pending = []
            self.ready = asyncio.Event()
            self.condition = asyncio.Condition()
            self.unfinished = 0

        async def enqueue(self, request, *, now=None):
            if not self.pending:
                self.pending.append(request)
                self.unfinished += 1
                return EnqueueResult(True, "queued")
            displaced = self.pending.pop(0)
            assert request.priority < displaced.priority
            self.pending.append(request)
            self.ready.set()
            return EnqueueResult(True, "queued", shed_request_id=displaced.request_id)

        async def dequeue(self):
            await self.ready.wait()
            async with self.condition:
                if self.pending:
                    return DequeueResult(self.pending.pop(0))
                await self.condition.wait()
                return DequeueResult(None)

        async def task_done(self):
            async with self.condition:
                self.unfinished -= 1
                self.condition.notify_all()

        async def join(self):
            async with self.condition:
                while self.unfinished:
                    await self.condition.wait()

    class Pipeline:
        class Client:
            class Config:
                provider = "standin"

            config = Config()

        client = Client()
        retriever = type("Retriever", (), {"setting": "R1"})()
        results_path = None

        async def run(self, question, **kwargs):
            return PipelineResult(
                request_id=kwargs["request_id"],
                answer="answer",
                status="ok",
                answerable=True,
                retrieval_setting="R1",
                cache_hit=False,
                coalesced=False,
                repairs=0,
                first_token_ms=1,
                total_ms=1,
                spans=(),
                retrieved_chunk_ids=(),
                gate_reason="test",
                arrival=kwargs["scheduled_arrival"].isoformat(),
                deadline="2026-11-09T10:00:08.000Z",
                source="stand-in",
                level="full",
            )

    monkeypatch.setattr(loadgen_module, "DeadlinePriorityQueue", SheddingQueue)
    summary = asyncio.run(
        run_scenario(
            Pipeline(),
            "priority_shed",
            Scenario(
                arrival_mode="burst",
                max_requests=2,
                burst_spread_ms=0,
                patience_ms=5000,
                deadline_ms=8000,
                urgent_fraction=0.5,
            ),
            ["question"],
            seed=1,
            run_id="priority-shed-test",
            results_path=tmp_path / "results.csv",
            trace_dir=tmp_path / "traces",
        )
    )

    assert summary.status_counts == {"timeout": 1, "ok": 1}
    assert summary.gave_up_count == 1
    with (tmp_path / "results.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["status"] for row in rows} == {"timeout", "ok"}
    traces = list((tmp_path / "traces").glob("*.json"))
    assert len(traces) == 2
    for trace_path in traces:
        trace = json.loads(trace_path.read_text())
        assert trace["question"] == "question"
        assert trace["question_id"] is None
        if trace["gave_up"]:
            assert trace["answer"] == ""
            assert trace["retrieved_chunk_ids"] == []
        else:
            assert trace["answer"] == "answer"
            assert trace["retrieved_chunk_ids"] == []
