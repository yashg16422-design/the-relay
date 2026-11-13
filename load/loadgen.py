#!/usr/bin/env python3
"""Seeded open-loop load runner for the local The Relay stand-in pipeline."""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
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
from relay.decide import pipeline_kwargs_from_config
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
    cache: bool = True  # false = every request runs the pipeline (no answer cache)
    # Per-request patience: lognormal with this median, capped. When set, each
    # request's deadline IS its own patience draw and patience_ms/deadline_ms
    # are only fallbacks.
    patience_median_ms: float | None = Field(default=None, gt=0)
    patience_sigma: float = Field(default=0.5, ge=0)
    patience_cap_ms: float = Field(default=20000, gt=0)
    # naive = retry at once, ignore retry-after, no shared retry budget (the retry-storm row).
    retry_mode: Literal["policy", "naive"] = "policy"
    ladder: bool = True  # false = always try the full pipeline (used by the retry comparison)

    def patience_s(self, seed: int, index: int) -> float:
        if self.patience_median_ms is None:
            return self.patience_ms / 1000
        rng = random.Random(f"patience-{seed}-{index}")
        draw = rng.lognormvariate(math.log(self.patience_median_ms), self.patience_sigma)
        return min(draw, self.patience_cap_ms) / 1000

    def deadline_s(self, seed: int, index: int) -> float:
        if self.patience_median_ms is None:
            return self.deadline_ms / 1000
        return self.patience_s(seed, index)


class LoadSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    retrieval_setting: str = Field(min_length=1)
    results_path: Path
    trace_dir: Path
    questions: list[str] = Field(default_factory=list)
    # eval-style YAML files; every entry's "question" joins the pool.
    question_files: list[Path] = Field(default_factory=list)
    scenarios: dict[str, Scenario]

    def question_pool(self) -> list[str]:
        pool = list(self.questions)
        for path in self.question_files:
            full = path if path.is_absolute() else PROJECT_ROOT / path
            if not full.exists():
                continue  # Aloo's file may not exist yet; the pool says how many it has
            raw = yaml.safe_load(full.read_text(encoding="utf-8")) or {}
            pool.extend(str(item["question"]).strip() for item in raw.get("questions", []))
        pool = list(dict.fromkeys(question for question in pool if question))
        if not pool:
            raise ValueError("the question pool is empty")
        return pool


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
    raw = {key: value for key, value in raw.items() if not str(key).startswith("x-")}
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


def _human_wait(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, round(seconds))} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{seconds / 3600:.1f} hours"


def limit_notice_for(bucket: str | None, wait_s: float) -> str:
    """The first paragraph a waiting person reads when a limit, not the papers, blocks an answer."""
    if bucket in ("tpd", "rpd"):
        return (
            "Today's model budget for this assistant is used up, so I cannot write you a "
            "checked answer right now. The budget refills gradually rather than at midnight: "
            f"the next full answer can be served in about {_human_wait(wait_s)}."
        )
    return (
        "The assistant is at its per-minute model limit. A checked answer would need about "
        f"{_human_wait(wait_s)} of waiting, which is longer than this request allows, "
        "so I am not making you wait for it."
    )


async def choose_level(
    *, scenario: "Scenario", limiter: Any, model: str, slack_s: float,
    committed_tokens: int = 0,
) -> tuple[str, str | None, dict[str, Any]]:
    """The degradation ladder: the richest answer this request can still get in time.

    full       = understand, retrieve, draft, gate, one repair
    lite       = the same without the repair
    extractive = no model call: the top passages verbatim, with a notice
    drop       = nothing fits; the person is told at once instead of waiting
    A level fits when (predicted wait for its tokens) + (its measured p95) is
    within the request's remaining patience. `committed_tokens` is what requests
    already in flight have been promised and not yet spent: they come first, so
    a new arrival is not started on tokens that would strand them half-way.
    """
    config = _load_yaml_mapping(PROJECT_ROOT / "config" / "scheduler.yaml")
    latency = config.get("degradation", {})
    tokens = config.get("tokens_per_level", {})
    info: dict[str, Any] = {"slack_s": round(slack_s, 3), "committed_tokens": committed_tokens}
    if not scenario.ladder:
        return "full", None, {**info, "ladder": "off"}
    worst_wait, worst_bucket = 0.0, None
    for level in ("full", "lite"):
        wait, bucket = (0.0, None)
        if limiter is not None:
            wait, bucket = await limiter.predicted_wait(
                model, request_tokens=int(tokens.get(level, 0)) + committed_tokens
            )
        info[f"{level}_wait_s"] = round(wait, 3)
        if wait > worst_wait:
            worst_wait, worst_bucket = wait, bucket
        if wait + float(latency.get(f"{level}_latency_s", 0)) <= slack_s:
            expected = config.get("tokens_expected", {})
            return level, None, {**info, "chosen": level, "blocked_by": bucket,
                                 "promised_tokens": int(expected.get(level, tokens.get(level, 0)))}
    info["blocked_by"] = worst_bucket
    if float(latency.get("extractive_latency_s", 0)) <= slack_s:
        if worst_wait > 0:
            notice = limit_notice_for(worst_bucket, worst_wait)
        else:
            # No limit is in the way; the request simply allows less time than a
            # checked answer takes. It still gets passages, never an unchecked sentence.
            notice = (
                "A checked answer takes about "
                f"{_human_wait(float(latency.get('lite_latency_s', 0)))}, which is longer than this "
                "request allows, so I am not sending an unchecked one."
            )
        return "extractive", notice, {**info, "chosen": "extractive",
                                      "reason": "limit" if worst_wait > 0 else "patience"}
    return "drop", None, {**info, "chosen": "drop"}


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
    allow_real: bool = False,
    warmup_experiment: str | None = None,
    experiment: str | None = None,
) -> LoadRunSummary:
    """Issue scheduled requests through an already-constructed real pipeline."""
    client = getattr(pipeline, "client", None)
    config = getattr(client, "config", None)
    if not allow_real and config is not None and getattr(config, "provider", "") != "standin":
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
    committed = {"tokens": 0}  # tokens promised to requests that are still running
    if scenario.ladder and client is not None and getattr(client, "limiter", None) is not None:
        # A service that is already running has recent limit headers. One tiny call
        # before the clock starts gives the limiter the same knowledge; without it
        # the first arrivals of a burst are admitted blind. It is not a request row.
        try:
            await client.complete(
                [{"role": "user", "content": "warm-up"}],
                experiment=warmup_experiment or f"load-{name}-warmup",
                model=getattr(pipeline, "model_by_step", {}).get("understand"),
                approval=getattr(pipeline, "approval", None),
                max_completion_tokens=16,
            )
        except Exception:  # the scenario itself will show any real problem
            pass
    run_start_monotonic = loop.time()
    run_start_wall = datetime.now(timezone.utc)

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
        deadline_seconds = scenario.deadline_s(seed, arrival.index)
        deadline_monotonic = scheduled_monotonic + deadline_seconds
        deadline_wall = _iso_utc(scheduled_wall + timedelta(seconds=deadline_seconds))
        patience_deadline = scheduled_monotonic + scenario.patience_s(seed, arrival.index)
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
            limiter = getattr(client, "limiter", None)
            model = getattr(pipeline, "model_by_step", {}).get("understand", "standin")
            level, limit_notice, ladder_info = await choose_level(
                scenario=scenario,
                limiter=limiter,
                model=model,
                slack_s=max(0.0, deadline_monotonic - loop.time()),
                committed_tokens=committed["tokens"],
            )
            promised = 0
            if level == "drop":
                raise TimeoutError("no answer path fits this request's remaining patience")
            promised = int(ladder_info.get("promised_tokens", 0))
            committed["tokens"] += promised
            # "Delayed" class: this request fits only after the bucket refills a
            # little. Hold it here, where waiting costs nothing, instead of letting
            # it start, spend tokens on its first step and stall at its second.
            hold_s = float(ladder_info.get(f"{level}_wait_s", 0.0)) if level in ("full", "lite") else 0.0
            if hold_s > 0:
                await asyncio.sleep(min(hold_s, max(0.0, patience_deadline - loop.time())))
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
                        limit_notice=limit_notice,
                        fallback_status=(
                            "rate_limited" if ladder_info.get("reason") == "limit" else "timeout"
                        ),
                        ladder=ladder_info,
                    )
                    if level == "extractive"
                    else pipeline.run(
                        arrival.question,
                        request_id=request_id,
                        experiment=experiment or f"load-{name}",
                        deadline_monotonic=deadline_monotonic,
                        scenario=name,
                        use_semantic=use_semantic,
                        rerank=rerank,
                        scheduled_arrival=scheduled_wall,
                        scheduled_arrival_monotonic=scheduled_monotonic,
                        degradation_level=level,
                        ladder=ladder_info,
                    )
                )
                pipeline_result = await asyncio.wait_for(operation, timeout=remaining_patience)
            finally:
                committed["tokens"] -= promised
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
            deadline = scheduled_monotonic + scenario.deadline_s(seed, arrival.index)
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
    ledger_path: str | Path | None = None,
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
        continuous_refill=bool(limiter_config.get("continuous_refill", False)),
    )
    # Stand-in calls are logged beside the run's results, never in budget_ledger.csv,
    # which is the record of real spending.
    if ledger_path is None and results_path is not None:
        ledger_path = Path(results_path).with_name(Path(results_path).stem + "_standin_ledger.csv")
    client = LLMClient(
        runtime_config, transport=transport, limiter=limiter, ledger_path=ledger_path,
        reserve_bytes_per_token=float(limiter_config.get("reservation_bytes_per_token", 1)),
    )
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
        **pipeline_kwargs_from_config(pipeline_config),
    )
    admin_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=local_app), base_url="http://standin.local"
    )
    return pipeline, admin_client, local_app


class NaiveRetryPolicy:
    """Retry immediately on any retryable failure, ignoring retry-after."""

    def __init__(self, *, max_attempts: int) -> None:
        self.max_attempts = max_attempts

    async def decide(self, *, attempt: int, status: str, deadline_monotonic: float,
                     budget: Any, now_monotonic: float | None = None,
                     retry_after_s: float | None = None) -> Any:
        from relay.retry import RetryDecision

        if status not in {"timeout", "rate_limited", "server_error"} or attempt >= self.max_attempts:
            return RetryDecision(False, reason="naive policy: attempts used up")
        return RetryDecision(True, delay_s=0.0, reason="naive policy: retry at once")


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
        scenario = settings.scenarios[name]
        if not scenario.cache:
            pipeline.answer_cache = None
        if scenario.retry_mode == "naive":
            # The policy this project argues against, kept runnable so its cost can be
            # measured: retry at once, ignore retry-after, no shared retry budget, and
            # no local admission control holding requests back.
            pipeline.retry_policy = NaiveRetryPolicy(max_attempts=3)
            pipeline.retry_budget = GlobalRetryBudget(10**9)
            pipeline.client.limiter = None
        try:
            summaries.append(
                await run_scenario(
                    pipeline,
                    name,
                    scenario,
                    settings.question_pool(),
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
