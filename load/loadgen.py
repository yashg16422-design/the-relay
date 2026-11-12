#!/usr/bin/env python3
"""Seeded open-loop load runner for the local The Relay stand-in pipeline."""

from __future__ import annotations

import argparse
import asyncio
import csv
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from library.ingest import SETTINGS as RETRIEVAL_SETTINGS
from relay.cache import AnswerCache
from relay.config import AppConfig, load_config as load_runtime_config
from relay.limiter import AdaptiveLimiter
from relay.llm_client import LLMClient
from relay.pipeline import CitedAnswerPipeline, PipelineResult
from relay.retrieval import PaperRetriever
from relay.retry import GlobalRetryBudget, RetryPolicy
from relay.scheduler import DeadlinePriorityQueue, RequestEnvelope, choose_degradation
from relay.trace import RequestTrace, Span, write_trace
from standin.server import create_app, load_config as load_standin_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = Path(__file__).with_name("scenarios.yaml")
RESULT_FIELDS = (
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
)
STATUS_VALUES = {"ok", "rate_limited", "timeout", "error"}


class LimitChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_seconds: float = Field(ge=0)
    limits: dict[str, int | None]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arrival_mode: Literal["poisson", "burst"]
    max_requests: int = Field(ge=1)
    rate_per_minute: float | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    burst_spread_ms: float = Field(default=0, ge=0)
    patience_ms: float = Field(gt=0)
    deadline_ms: float = Field(gt=0)
    zipf_exponent: float = Field(default=1.0, ge=0)
    urgent_fraction: float = Field(default=0.2, ge=0, le=1)
    urgent_priority: int = 0
    normal_priority: int = 10
    limit_changes: list[LimitChange] = Field(default_factory=list)


class LoadSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    retrieval_setting: str = Field(min_length=1)
    results_path: Path
    trace_dir: Path
    questions: list[str] = Field(min_length=1)
    scenarios: dict[str, Scenario]


@dataclass(frozen=True)
class PlannedArrival:
    index: int
    offset_seconds: float
    question: str
    priority: int


@dataclass(frozen=True)
class LoadRunSummary:
    scenario: str
    request_count: int
    status_counts: Mapping[str, int]
    gave_up_count: int
    results_path: Path
    trace_dir: Path


def load_settings(path: str | Path = DEFAULT_SCENARIOS) -> LoadSettings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("scenarios.yaml must contain a YAML mapping")
    return LoadSettings.model_validate(raw)


def _load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"expected a YAML mapping in {path}")
    return dict(raw)


def generate_arrivals(
    scenario: Scenario, questions: Sequence[str], *, seed: int
) -> list[PlannedArrival]:
    if not questions:
        raise ValueError("at least one workload question is required")
    rng = random.Random(seed)
    if scenario.arrival_mode == "burst":
        offsets = [i * scenario.burst_spread_ms / 1000 for i in range(scenario.max_requests)]
    else:
        if scenario.rate_per_minute is None or scenario.duration_seconds is None:
            raise ValueError("Poisson scenario needs rate_per_minute and duration_seconds")
        rate_per_second = scenario.rate_per_minute / 60
        offsets = [0.0]
        while len(offsets) < scenario.max_requests:
            next_offset = offsets[-1] + rng.expovariate(rate_per_second)
            if next_offset >= scenario.duration_seconds:
                break
            offsets.append(next_offset)

    arrivals = []
    for index, offset in enumerate(offsets):
        question = _choose_zipf_question(questions, scenario.zipf_exponent, rng)
        urgent_period = (
            max(1, round(1 / scenario.urgent_fraction))
            if scenario.urgent_fraction > 0
            else None
        )
        is_urgent = urgent_period is not None and (index + 1) % urgent_period == 0
        priority = scenario.urgent_priority if is_urgent else scenario.normal_priority
        arrivals.append(
            PlannedArrival(
                index=index,
                offset_seconds=offset,
                question=question,
                priority=priority,
            )
        )
    return arrivals


def _choose_zipf_question(questions: Sequence[str], exponent: float, rng: random.Random) -> str:
    weights = [1 / ((rank + 1) ** exponent) for rank in range(len(questions))]
    return rng.choices(list(questions), weights=weights, k=1)[0]


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _duration_ms(start: float, now: float) -> float:
    return max(0.0, (now - start) * 1000)


def _status_for(result_status: str) -> str:
    if result_status == "refused":
        return "ok"  # A safe refusal is still a completed response.
    if result_status in STATUS_VALUES:
        return result_status
    return "error"


def _tokens_from_spans(spans: Sequence[Span], field: str) -> int | None:
    model_spans = [span for span in spans if span.model is not None]
    if not model_spans or any(getattr(span, field) is None for span in model_spans):
        return None
    return sum(int(getattr(span, field)) for span in model_spans)


def _result_row(
    *,
    request_id: str,
    scenario: str,
    start_time: str,
    first_token_ms: float | None,
    total_ms: float,
    status: str,
    spans: Sequence[Span],
    retrieval_setting: str,
) -> dict[str, Any]:
    if status not in STATUS_VALUES:
        raise ValueError(f"results status is outside the CSV contract: {status}")
    return {
        "request_id": request_id,
        "scenario": scenario,
        "source": "stand-in",
        "start_time": start_time,
        "first_token_ms": "" if first_token_ms is None else round(first_token_ms, 3),
        "total_ms": round(total_ms, 3),
        "status": status,
        "retries": sum(span.kind == "retry" for span in spans),
        "tokens_in": "" if _tokens_from_spans(spans, "tokens_in") is None else _tokens_from_spans(spans, "tokens_in"),
        "tokens_out": "" if _tokens_from_spans(spans, "tokens_out") is None else _tokens_from_spans(spans, "tokens_out"),
        "retrieval_setting": retrieval_setting,
        # The stand-in answer is intentionally not a quality grade.
        "answer_grade": "not_graded",
    }


def _fallback_trace_span(total_ms: float, status: str) -> list[Span]:
    return [
        Span(
            name="load_runner",
            start_ms=0,
            end_ms=total_ms,
            kind="wait" if status == "timeout" else "work",
            status=status,
        )
    ]


class AppendOnlyResults:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def append(self, row: Mapping[str, Any]) -> None:
        async with self.lock:
            await asyncio.to_thread(self._append_sync, row)

    def _append_sync(self, row: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists() and self.path.stat().st_size > 0
        with self.path.open("a+", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, extrasaction="raise")
            if exists:
                stream.seek(0)
                first_line = stream.readline().strip("\r\n")
                if tuple(first_line.split(",")) != RESULT_FIELDS:
                    raise ValueError(f"results CSV header does not match contract: {self.path}")
                stream.seek(0, 2)
            else:
                writer.writeheader()
            writer.writerow(row)
            stream.flush()


def _request_id(run_id: str, scenario: str, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{run_id}-{scenario}")
    return f"{safe}-{index:05d}"


async def run_scenario(
    pipeline: CitedAnswerPipeline,
    name: str,
    scenario: Scenario,
    questions: Sequence[str],
    *,
    seed: int,
    run_id: str,
    results_path: str | Path,
    trace_dir: str | Path,
    admin_client: httpx.AsyncClient | None = None,
    use_semantic: bool = False,
    rerank: bool = False,
) -> LoadRunSummary:
    """Issue scheduled requests through an already-constructed real pipeline."""
    client = getattr(pipeline, "client", None)
    config = getattr(client, "config", None)
    if config is not None and getattr(config, "provider", "") != "standin":
        raise ValueError("loadgen only permits provider=standin; real Groq is disabled")
    arrivals = generate_arrivals(scenario, questions, seed=seed)
    loop = asyncio.get_running_loop()
    run_start_monotonic = loop.time()
    run_start_wall = datetime.now(timezone.utc)
    writer = AppendOnlyResults(results_path)
    statuses: dict[str, int] = {}
    gave_up_count = 0
    effective_active = 0
    effective_active_lock = asyncio.Lock()

    async def apply_limits() -> None:
        if not scenario.limit_changes:
            return
        if admin_client is None:
            raise ValueError("scenario requests limit_changes but no local admin_client was provided")
        for change in sorted(scenario.limit_changes, key=lambda item: item.at_seconds):
            target = run_start_monotonic + change.at_seconds
            await asyncio.sleep(max(0.0, target - loop.time()))
            response = await admin_client.post("/admin/limits", json=change.limits)
            response.raise_for_status()

    async def run_one(arrival: PlannedArrival) -> dict[str, Any]:
        nonlocal effective_active
        scheduled_monotonic = run_start_monotonic + arrival.offset_seconds
        scheduled_wall = run_start_wall + timedelta(seconds=arrival.offset_seconds)
        await asyncio.sleep(max(0.0, scheduled_monotonic - loop.time()))
        request_id = _request_id(run_id, name, arrival.index)
        scheduled_start = _iso_utc(scheduled_wall)
        deadline_seconds = scenario.deadline_ms / 1000
        deadline_monotonic = scheduled_monotonic + deadline_seconds
        deadline_wall = _iso_utc(scheduled_wall + timedelta(seconds=deadline_seconds))
        patience_deadline = scheduled_monotonic + scenario.patience_ms / 1000
        gave_up = False
        nonlocal gave_up_count
        pipeline_result: PipelineResult | None = None
        status = "error"
        spans: list[Span] = []
        first_token_ms: float | None = None
        retrieval_setting = getattr(getattr(pipeline, "retriever", None), "setting", "unknown")
        try:
            remaining_patience = max(0.0, patience_deadline - loop.time())
            if remaining_patience <= 0:
                raise TimeoutError
            scheduler_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "scheduler.yaml")
            estimates = scheduler_config.get("degradation", {})
            degradation = choose_degradation(
                remaining_s=max(0.0, deadline_monotonic - loop.time()),
                estimates_s={
                    "full": estimates.get("full_latency_s"),
                    "lite": estimates.get("lite_latency_s"),
                    "extractive": estimates.get("extractive_latency_s"),
                },
                allow_wait=False,
            )
            if degradation.level == "drop":
                raise TimeoutError("no configured answer path fits the request deadline")
            limiter = getattr(client, "limiter", None)
            model = getattr(pipeline, "model_by_step", {}).get("understand", "standin")
            if limiter is not None:
                while True:
                    allowed_concurrency = await limiter.effective_concurrency(model, worker_count)
                    async with effective_active_lock:
                        if effective_active < allowed_concurrency:
                            effective_active += 1
                            break
                    await asyncio.sleep(0.005)
            try:
                operation = (
                    pipeline.run_extractive(
                        arrival.question,
                        request_id=request_id,
                        deadline_monotonic=deadline_monotonic,
                        scenario=name,
                        scheduled_arrival=scheduled_wall,
                        scheduled_arrival_monotonic=scheduled_monotonic,
                    )
                    if degradation.level == "extractive"
                    else pipeline.run(
                        arrival.question,
                        request_id=request_id,
                        experiment=f"load-{name}",
                        deadline_monotonic=deadline_monotonic,
                        scenario=name,
                        use_semantic=use_semantic,
                        rerank=rerank,
                        scheduled_arrival=scheduled_wall,
                        scheduled_arrival_monotonic=scheduled_monotonic,
                        degradation_level=degradation.level,
                    )
                )
                pipeline_result = await asyncio.wait_for(operation, timeout=remaining_patience)
            finally:
                if limiter is not None:
                    async with effective_active_lock:
                        effective_active = max(0, effective_active - 1)
            total_ms = _duration_ms(scheduled_monotonic, loop.time())
            status = _status_for(pipeline_result.status)
            gave_up = pipeline_result.status == "timeout"
            spans = list(pipeline_result.spans)
            retrieval_setting = pipeline_result.retrieval_setting
            if pipeline_result.first_token_ms is not None:
                first_token_ms = pipeline_result.first_token_ms
        except TimeoutError:
            gave_up = True
            status = "timeout"
            total_ms = _duration_ms(scheduled_monotonic, loop.time())
            spans = _fallback_trace_span(total_ms, status)
            first_token_ms = None
        except Exception as exc:
            status = "error"
            total_ms = _duration_ms(scheduled_monotonic, loop.time())
            spans = [
                Span(
                    name="load_runner",
                    start_ms=0,
                    end_ms=total_ms,
                    kind="work",
                    status=f"error:{type(exc).__name__}",
                )
            ]

        # The pipeline owns append-only persistence for completed requests.
        # The harness writes only outcomes that never reached its result path
        # (for example, a patience timeout while waiting on the pipeline).
        pipeline_persists = getattr(pipeline, "results_path", None) is not None
        if pipeline_persists and pipeline_result is not None:
            row = {"request_id": request_id, "status": status}
        else:
            trace = RequestTrace(
                request_id=request_id,
                question_id=None,
                question=arrival.question,
                answer=pipeline_result.answer if pipeline_result else "",
                retrieved_chunk_ids=(
                    list(pipeline_result.retrieved_chunk_ids) if pipeline_result else []
                ),
                scenario=name,
                arrival=scheduled_start,
                deadline=deadline_wall,
                level="wait" if status == "timeout" else "full",
                cache_hit=pipeline_result.cache_hit if pipeline_result else False,
                coalesced=pipeline_result.coalesced if pipeline_result else False,
                spans=spans,
                gave_up=gave_up,
            )
            write_trace(trace, directory=trace_dir)
            row = _result_row(
                request_id=request_id,
                scenario=name,
                start_time=scheduled_start,
                first_token_ms=first_token_ms,
                total_ms=total_ms,
                status=status,
                spans=spans,
                retrieval_setting=retrieval_setting,
            )
            await writer.append(row)
        statuses[status] = statuses.get(status, 0) + 1
        if gave_up:
            gave_up_count += 1
        return row

    limit_task = asyncio.create_task(apply_limits()) if scenario.limit_changes else None
    if limit_task is not None:
        # Ensure a t=0 limit change is submitted before same-time requests.
        await asyncio.sleep(0)
    # Requests enter the same deadline-aware priority queue used by the local
    # service. This harness offers them open-loop, then caps active workers.
    scheduler_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "scheduler.yaml")
    queue = DeadlinePriorityQueue(int(scheduler_config.get("queue_capacity", 200)))
    worker_count = int(scheduler_config.get("unverified_concurrency_ceiling", 10))
    arrival_by_request_id: dict[str, PlannedArrival] = {}

    async def record_queue_drop(arrival: PlannedArrival, reason: str) -> None:
        nonlocal gave_up_count
        scheduled_monotonic = run_start_monotonic + arrival.offset_seconds
        scheduled_wall = run_start_wall + timedelta(seconds=arrival.offset_seconds)
        request_id = _request_id(run_id, name, arrival.index)
        scheduled_start = _iso_utc(scheduled_wall)
        total_ms = _duration_ms(scheduled_monotonic, loop.time())
        trace = RequestTrace(
            request_id=request_id,
            question_id=None,
            question=arrival.question,
            answer="",
            retrieved_chunk_ids=[],
            scenario=name,
            arrival=scheduled_start,
            deadline=_iso_utc(scheduled_wall + timedelta(milliseconds=scenario.deadline_ms)),
            level="wait",
            cache_hit=False,
            coalesced=False,
            spans=[Span(name="scheduler", start_ms=0, end_ms=total_ms, kind="wait", status=reason)],
            gave_up=True,
        )
        write_trace(trace, directory=trace_dir)
        row = _result_row(
            request_id=request_id,
            scenario=name,
            start_time=scheduled_start,
            first_token_ms=None,
            total_ms=total_ms,
            status="timeout",
            spans=trace.spans,
            retrieval_setting=getattr(getattr(pipeline, "retriever", None), "setting", "unknown"),
        )
        await writer.append(row)
        statuses["timeout"] = statuses.get("timeout", 0) + 1
        gave_up_count += 1

    async def process_expired(request_ids: Sequence[str]) -> None:
        for request_id in request_ids:
            expired_arrival = arrival_by_request_id.get(request_id)
            if expired_arrival is not None:
                await run_one(expired_arrival)

    async def offer_arrivals() -> None:
        for arrival in arrivals:
            scheduled_monotonic = run_start_monotonic + arrival.offset_seconds
            await asyncio.sleep(max(0.0, scheduled_monotonic - loop.time()))
            request_id = _request_id(run_id, name, arrival.index)
            arrival_by_request_id[request_id] = arrival
            deadline = scheduled_monotonic + scenario.deadline_ms / 1000
            admission = await queue.enqueue(
                RequestEnvelope(
                    request_id=request_id,
                    priority=arrival.priority,
                    deadline_monotonic=deadline,
                    arrival_monotonic=scheduled_monotonic,
                    payload=arrival,
                ),
                now=loop.time(),
            )
            await process_expired(admission.expired_request_ids)
            if admission.shed_request_id is not None:
                shed_arrival = arrival_by_request_id.get(admission.shed_request_id)
                if shed_arrival is None:
                    raise RuntimeError(
                        "scheduler shed an unknown request ID: "
                        f"{admission.shed_request_id}"
                    )
                await record_queue_drop(
                    shed_arrival,
                    f"shed for higher-priority request {request_id}",
                )
            if not admission.accepted:
                if request_id in admission.expired_request_ids:
                    # A deadline already elapsed before queue admission. The
                    # expired-ID path persisted this request above.
                    continue
                if "deadline" in admission.reason:
                    await run_one(arrival)
                else:
                    await record_queue_drop(arrival, admission.reason)

    async def worker() -> None:
        while True:
            item = await queue.dequeue()
            await process_expired(item.expired_request_ids)
            if item.request is None:
                continue
            try:
                await run_one(item.request.payload)
            finally:
                await queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    producer = asyncio.create_task(offer_arrivals())
    await producer
    await queue.join()
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    if limit_task is not None:
        await limit_task

    return LoadRunSummary(
        scenario=name,
        request_count=len(arrivals),
        status_counts=dict(statuses),
        gave_up_count=gave_up_count,
        results_path=Path(results_path),
        trace_dir=Path(trace_dir),
    )


def build_local_runtime(
    *,
    retrieval_setting: str,
    chunk_dir: str | Path,
    runtime_config_path: str | Path | None = None,
    standin_config_path: str | Path | None = None,
    seed: int = 20261109,
    results_path: str | Path | None = None,
    trace_dir: str | Path | None = None,
) -> tuple[CitedAnswerPipeline, httpx.AsyncClient, Any]:
    """Create the real pipeline wired only to an in-process stand-in ASGI app."""
    runtime_config: AppConfig = load_runtime_config(runtime_config_path)
    if runtime_config.provider != "standin":
        raise ValueError("loadgen refuses non-standin providers")
    simulator_config = load_standin_config(standin_config_path or Path(__file__).parents[1] / "standin" / "config.yaml")
    local_app = create_app(simulator_config)
    transport = httpx.ASGITransport(app=local_app)
    limiter_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "limiter.yaml")
    limiter = AdaptiveLimiter(
        additive_step=float(limiter_config.get("aimd_additive_step", 0.1)),
        multiplicative_decrease=float(limiter_config.get("aimd_multiplicative_decrease", 0.5)),
        freshness_seconds=float(limiter_config.get("snapshot_freshness_s", 60)),
    )
    client = LLMClient(runtime_config, transport=transport, limiter=limiter)
    setting_spec = next(
        (item for item in RETRIEVAL_SETTINGS if item.name == retrieval_setting), None
    )
    if setting_spec is None:
        raise ValueError(
            f"unknown retrieval setting {retrieval_setting!r}; "
            f"choose from {[item.name for item in RETRIEVAL_SETTINGS]}"
        )
    retrieval_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "retrieval.yaml")
    retriever = PaperRetriever.from_jsonl(
        retrieval_setting,
        top_k=setting_spec.top_k,
        chunk_dir=chunk_dir,
        reranker_model_name=(
            str(retrieval_config.get("reranker_model"))
            if setting_spec.rerank and retrieval_config.get("reranker_model")
            else None
        ),
    )
    retry_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "retry.yaml")
    pipeline_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "pipeline.yaml")
    cache_config = _load_yaml_mapping(PROJECT_ROOT / "config" / "cache.yaml")
    retry_rng = random.Random(seed)
    retry_policy = RetryPolicy(
        max_attempts=int(retry_config.get("max_attempts", 3)),
        base_delay_s=float(retry_config.get("base_delay_s", 0.25)),
        max_delay_s=float(retry_config.get("max_delay_s", 4.0)),
        random_value=retry_rng.random,
    )
    retry_budget = GlobalRetryBudget(int(retry_config.get("global_retry_limit", 20)))
    answer_cache = AnswerCache[PipelineResult](
        ttl_seconds=float(cache_config.get("ttl_seconds", 60)),
        max_entries=int(cache_config.get("max_entries", 1000)),
    )
    pipeline = CitedAnswerPipeline(
        client,
        retriever,
        max_repairs=int(pipeline_config.get("max_repairs", 1)),
        understand_max_tokens=int(pipeline_config.get("understand_max_completion_tokens", 100)),
        judge_max_tokens=int(pipeline_config.get("judge_max_completion_tokens", 120)),
        draft_max_tokens=int(pipeline_config.get("draft_max_completion_tokens", 400)),
        repair_max_tokens=int(pipeline_config.get("repair_max_completion_tokens", 400)),
        retry_policy=retry_policy,
        retry_budget=retry_budget,
        answer_cache=answer_cache,
        results_path=results_path,
        trace_dir=trace_dir,
    )
    admin_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=local_app), base_url="http://standin.local"
    )
    return pipeline, admin_client, local_app


async def run_from_settings(
    *,
    settings: LoadSettings,
    scenario_names: Sequence[str],
    seed: int | None = None,
    run_id: str | None = None,
    results_path: str | Path | None = None,
    trace_dir: str | Path | None = None,
    retrieval_setting: str | None = None,
    chunk_dir: str | Path | None = None,
    runtime_config_path: str | Path | None = None,
    standin_config_path: str | Path | None = None,
) -> list[LoadRunSummary]:
    active_setting = retrieval_setting or settings.retrieval_setting
    setting_spec = next(
        (item for item in RETRIEVAL_SETTINGS if item.name == active_setting), None
    )
    if setting_spec is None:
        raise ValueError(
            f"unknown retrieval setting {active_setting!r}; "
            f"choose from {[item.name for item in RETRIEVAL_SETTINGS]}"
        )
    active_results = Path(results_path or settings.results_path)
    active_traces = Path(trace_dir or settings.trace_dir)
    active_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    summaries = []
    for name in scenario_names:
        if name not in settings.scenarios:
            raise ValueError(f"unknown scenario {name!r}; choose from {sorted(settings.scenarios)}")
        # Every scenario gets a fresh stand-in instance and answer cache. This
        # prevents earlier limit changes or cache warm-up from contaminating a
        # later scenario's measurements.
        pipeline, admin_client, _local_app = build_local_runtime(
            retrieval_setting=active_setting,
            chunk_dir=chunk_dir or PROJECT_ROOT / "library" / "chunks",
            runtime_config_path=runtime_config_path,
            standin_config_path=standin_config_path,
            seed=settings.seed if seed is None else seed,
            results_path=active_results,
            trace_dir=active_traces,
        )
        try:
            summaries.append(
                await run_scenario(
                    pipeline,
                    name,
                    settings.scenarios[name],
                    settings.questions,
                    seed=settings.seed if seed is None else seed,
                    run_id=active_run_id,
                    results_path=active_results,
                    trace_dir=active_traces,
                    admin_client=admin_client,
                    rerank=setting_spec.rerank,
                )
            )
        finally:
            await pipeline.client.aclose()
            await admin_client.aclose()
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--scenario", action="append", help="scenario name; repeat or use --all")
    parser.add_argument("--all", action="store_true", help="run every scenario in scenarios.yaml")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--run-id", help="unique ID prefix; default is a UTC timestamp")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--setting", help="retrieval setting, default from scenarios.yaml")
    parser.add_argument("--chunks", type=Path, help="chunk directory; default library/chunks")
    parser.add_argument("--runtime-config", type=Path, help="default config/default.yaml")
    parser.add_argument("--standin-config", type=Path, help="default standin/config.yaml")
    args = parser.parse_args()

    settings = load_settings(args.config)
    if args.all:
        names = list(settings.scenarios)
    elif args.scenario:
        names = args.scenario
    else:
        parser.error("choose --scenario NAME or --all")
    summaries = asyncio.run(
        run_from_settings(
            settings=settings,
            scenario_names=names,
            seed=args.seed,
            run_id=args.run_id,
            results_path=args.results,
            trace_dir=args.traces,
            retrieval_setting=args.setting,
            chunk_dir=args.chunks,
            runtime_config_path=args.runtime_config,
            standin_config_path=args.standin_config,
        )
    )
    for summary in summaries:
        print(
            f"{summary.scenario}: {summary.request_count} requests, "
            f"statuses={dict(summary.status_counts)}, gave_up={summary.gave_up_count}; "
            f"results={summary.results_path}, traces={summary.trace_dir}"
        )


if __name__ == "__main__":
    main()
