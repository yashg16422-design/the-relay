"""Multi-step cited-answer pipeline with retrieval, branch, repair, and drop."""

from __future__ import annotations

import asyncio
import csv
import json
import time
import threading
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from relay.cache import AnswerCache
from relay.decide import (
    ABSTAIN_SENTINEL,
    is_abstention,
    papers_from_question,
    parse_judgment,
    parse_understanding,
    validate_options,
)
from relay.gate import CitationGate, GateResult
from relay.llm_client import (
    ExperimentApproval,
    LLMClient,
    LLMRequestError,
    RateLimitAdmissionError,
    StreamCapture,
)
from relay.retry import GlobalRetryBudget, RetryPolicy
from relay.retrieval import PaperRetriever, RetrievalResult, content_query, tokenize
from relay.trace import RequestTrace, Span, write_trace


SAFE_REFUSAL = (
    "I couldn't find support for this question in the passages I retrieved, "
    "so I can't answer it without guessing."
)
# What a person is told when the model service, not the papers, is the reason
# there is no answer. Saying "the papers lack support" here would be false.
LIMIT_REPLIES = {
    "rate_limited": (
        "The model service is at its rate limit, so I could not finish a checked answer "
        "for you in time. Nothing was guessed. Please ask again in about a minute."
    ),
    "timeout": (
        "I ran out of time before I could finish a checked answer, so I am not sending "
        "an unchecked one. Please ask again."
    ),
    "error": (
        "The model service failed while I was preparing your answer, so I am not sending "
        "an unchecked one. Please ask again."
    ),
}
PASSAGES_HEADER = "These are passages from the papers that best match your question. They are passages, not an answer."
RESULT_FIELDS = (
    "request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms",
    "status", "retries", "tokens_in", "tokens_out", "retrieval_setting", "answer_grade",
)
_RESULTS_LOCK = threading.Lock()
_EXTRACTIVE_STOPWORDS = {
    "about", "after", "and", "are", "does", "explain", "for", "from", "how",
    "into", "paper", "passage", "passages", "that", "the", "their", "this",
    "what", "which", "with",
}


@dataclass(frozen=True)
class PipelineResult:
    request_id: str
    answer: str
    status: str
    answerable: bool
    retrieval_setting: str
    cache_hit: bool
    coalesced: bool
    repairs: int
    first_token_ms: float | None
    total_ms: float
    spans: tuple[Span, ...]
    retrieved_chunk_ids: tuple[str, ...]
    gate_reason: str
    arrival: str
    deadline: str
    source: str
    level: Literal["full", "lite", "extractive", "wait"]
    decide: Mapping[str, Any] | None = None
    # The draft exactly as the model wrote it, citation markers included, so
    # the gate can be replayed offline at zero cost.
    raw_answer: str | None = None
    # True when passages were sent in place of a model answer.
    fallback_delivered: bool = False
    ladder: Mapping[str, Any] | None = None


UNDERSTAND_SYSTEM = (
    "Rewrite the user's question as one concise search query. Also say which "
    'paper(s) it is about: "transformer" (Attention Is All You Need), "moshi" '
    '(Moshi: a speech-text foundation model for real-time dialogue), "hnet" '
    "(Dynamic Chunking for End-to-End Hierarchical Sequence Modeling). Use an "
    'empty list if it names none. Return only JSON: {"query":"...","papers":[...]}.'
)
JUDGE_SYSTEM = (
    "Decide whether the evidence contains enough information to answer "
    "the question. Do not answer it. Return only JSON with boolean "
    '"answerable" and a short "reason".'
)
DRAFT_SYSTEM = (
    "Answer only from the supplied passages. End every factual "
    "sentence with one or more markers in this exact form: "
    "[[cite:chunk_id]]. Do not invent citations, numbers, or names. "
    "Return only the answer. If the passages do not contain the answer, "
    f"reply with exactly {ABSTAIN_SENTINEL} and nothing else."
)


class CitedAnswerPipeline:
    def __init__(
        self,
        client: LLMClient,
        retriever: PaperRetriever,
        *,
        model_by_step: Mapping[str, str] | None = None,
        approval: ExperimentApproval | None = None,
        max_repairs: int = 1,
        understand_max_tokens: int = 100,
        judge_max_tokens: int = 120,
        draft_max_tokens: int = 400,
        repair_max_tokens: int = 400,
        gate: CitationGate | None = None,
        results_path: str | Path | None = None,
        trace_dir: str | Path | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_budget: GlobalRetryBudget | None = None,
        answer_cache: AnswerCache[PipelineResult] | None = None,
        decide_mode: Literal["llm_judge", "precheck_abstain"] = "llm_judge",
        precheck_min_bm25: float = 0.0,
        scope_to_named_papers: bool = True,
        judge_retry_options: Mapping[str, Any] | None = None,
        options_by_step: Mapping[str, Mapping[str, Any]] | None = None,
        content_word_query: bool = False,
        options_by_model: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs cannot be negative")
        if decide_mode not in ("llm_judge", "precheck_abstain"):
            raise ValueError(f"unknown decide_mode: {decide_mode}")
        self.decide_mode = decide_mode
        self.content_word_query = content_word_query
        # Request options that only some models need, e.g. reasoning_effort.
        self.options_by_model = {
            model: validate_options(f"options_by_model[{model}]", dict(options))
            for model, options in (options_by_model or {}).items()
        }
        self.precheck_min_bm25 = float(precheck_min_bm25)
        self.scope_to_named_papers = scope_to_named_papers
        # Extra request options for the single judge retry, e.g. JSON mode once
        # experiment E0 shows which models accept it.
        self.judge_retry_options = validate_options(
            "judge_retry_options", dict(judge_retry_options or {})
        )
        # Per-step request options such as reasoning_effort or temperature.
        self.options_by_step = {
            step: validate_options(f"options_by_step[{step}]", dict(options))
            for step, options in (options_by_step or {}).items()
        }
        self.client = client
        self.retriever = retriever
        self.model_by_step = dict(model_by_step or {})
        self.approval = approval
        self.max_repairs = max_repairs
        self.output_limits = {
            "understand": understand_max_tokens,
            "judge": judge_max_tokens,
            "draft": draft_max_tokens,
            "repair": repair_max_tokens,
        }
        self.gate = gate or CitationGate()
        self.results_path = results_path
        self.trace_dir = trace_dir
        if retry_policy is not None and retry_budget is None:
            raise ValueError("retry_budget is required when retry_policy is enabled")
        self.retry_policy = retry_policy
        self.retry_budget = retry_budget
        self.answer_cache = answer_cache

    async def run(
        self,
        question: str,
        *,
        request_id: str,
        experiment: str,
        deadline_monotonic: float,
        scenario: str = "single",
        question_id: str | None = None,
        use_semantic: bool = False,
        rerank: bool = False,
        scheduled_arrival: datetime | None = None,
        scheduled_arrival_monotonic: float | None = None,
        degradation_level: Literal["full", "lite"] = "full",
        ladder: Mapping[str, Any] | None = None,
    ) -> PipelineResult:
        if not question.strip():
            raise ValueError("question cannot be empty")
        if question_id is not None and not question_id.strip():
            raise ValueError("question_id cannot be empty when provided")
        loop = asyncio.get_running_loop()
        now_monotonic = loop.time()
        dispatch_lag_s = max(
            0.0,
            now_monotonic - scheduled_arrival_monotonic
            if scheduled_arrival_monotonic is not None
            else 0.0,
        )
        started = time.perf_counter() - dispatch_lag_s
        arrival_dt = (scheduled_arrival or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if scheduled_arrival is not None and (
            scheduled_arrival.tzinfo is None or scheduled_arrival.utcoffset() is None
        ):
            raise ValueError("scheduled_arrival must be timezone-aware")
        arrival = arrival_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        deadline_delta_s = max(
            0.0,
            deadline_monotonic
            - (scheduled_arrival_monotonic if scheduled_arrival_monotonic is not None else now_monotonic),
        )
        deadline = (arrival_dt + timedelta(seconds=deadline_delta_s)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

        async def execute() -> PipelineResult:
            return await self._run_uncached(
                question,
                request_id=request_id,
                experiment=experiment,
                deadline_monotonic=deadline_monotonic,
                scenario=scenario,
                use_semantic=use_semantic,
                rerank=rerank,
                scheduled_arrival=arrival_dt,
                scheduled_started_perf=started,
                dispatch_lag_ms=dispatch_lag_s * 1000,
                degradation_level=degradation_level,
            )

        cache_hit = False
        coalesced = False
        if self.answer_cache is None:
            result = await execute()
        else:
            provider = getattr(getattr(self.client, "config", None), "provider", "standin")
            cache_key = AnswerCache.key_for(
                {
                    "question": question,
                    "provider": provider,
                    "models": self.model_by_step,
                    "retrieval_setting": self.retriever.setting,
                    "semantic": use_semantic,
                    "rerank": rerank,
                    "level": degradation_level,
                    "decide_mode": self.decide_mode,
                    "gate_strict": bool(getattr(self.gate, "strict", False)),
                    "scope": self.scope_to_named_papers,
                    "content_word_query": self.content_word_query,
                }
            )
            try:
                cache_result = await asyncio.wait_for(
                    self.answer_cache.get_or_create(
                        cache_key,
                        execute,
                        should_cache=lambda value: value.status == "ok" and value.answerable,
                    ),
                    timeout=self._remaining(deadline_monotonic),
                )
                result = cache_result.value
                cache_hit = cache_result.cache_hit
                coalesced = cache_result.coalesced
            except asyncio.TimeoutError:
                result = PipelineResult(
                    request_id=request_id,
                    answer=SAFE_REFUSAL,
                    status="timeout",
                    answerable=False,
                    retrieval_setting=self.retriever.setting,
                    cache_hit=False,
                    coalesced=False,
                    repairs=0,
                    first_token_ms=None,
                    total_ms=(time.perf_counter() - started) * 1000,
                    spans=(),
                    retrieved_chunk_ids=(),
                    gate_reason="deadline expired while waiting for shared request",
                    arrival=arrival,
                    deadline=deadline,
                    source=("real" if provider == "groq" else "stand-in"),
                    level="wait",
                )

        elapsed_ms = (time.perf_counter() - started) * 1000
        if cache_hit:
            result_spans: tuple[Span, ...] = ()
        elif coalesced:
            result_spans = (
                Span(
                    name="coalesced_wait",
                    start_ms=0,
                    end_ms=elapsed_ms,
                    kind="wait",
                    status="coalesced",
                ),
            )
        else:
            result_spans = result.spans
        result = replace(
            result,
            request_id=request_id,
            cache_hit=cache_hit,
            coalesced=coalesced,
            first_token_ms=elapsed_ms if cache_hit or coalesced else result.first_token_ms,
            total_ms=elapsed_ms if cache_hit or coalesced else result.total_ms,
            spans=result_spans,
            arrival=arrival,
            deadline=deadline,
            ladder=ladder,
        )
        self._persist(
            result,
            scenario=scenario,
            question=question,
            question_id=question_id,
        )
        return result

    async def _run_uncached(
        self,
        question: str,
        *,
        request_id: str,
        experiment: str,
        deadline_monotonic: float,
        scenario: str = "single",
        use_semantic: bool = False,
        rerank: bool = False,
        scheduled_arrival: datetime | None = None,
        scheduled_started_perf: float | None = None,
        dispatch_lag_ms: float = 0.0,
        degradation_level: Literal["full", "lite"] = "full",
    ) -> PipelineResult:
        if not question.strip():
            raise ValueError("question cannot be empty")
        started = scheduled_started_perf or time.perf_counter()
        loop = __import__("asyncio").get_running_loop()
        arrival_dt = scheduled_arrival or datetime.now(timezone.utc)
        if arrival_dt.tzinfo is None or arrival_dt.utcoffset() is None:
            raise ValueError("scheduled_arrival must be timezone-aware")
        arrival_dt = arrival_dt.astimezone(timezone.utc)
        remaining_s = max(0.0, deadline_monotonic - loop.time())
        deadline_dt = arrival_dt + timedelta(seconds=remaining_s)
        arrival = arrival_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        deadline = deadline_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        spans: list[Span] = []
        chunk_ids: tuple[str, ...] = ()
        retrieved: list[dict[str, Any]] = []
        if dispatch_lag_ms > 0:
            spans.append(
                Span(
                    name="scheduler_wait",
                    start_ms=0,
                    end_ms=dispatch_lag_ms,
                    kind="wait",
                    status="queued",
                )
            )

        def span(
            name: str,
            start: float,
            *,
            kind: str = "work",
            model: str | None = None,
            status: str = "ok",
            ttft_ms: float | None = None,
            tokens_in: int | None = None,
            tokens_out: int | None = None,
            cached_tokens: int | None = None,
            reasoning_tokens: int | None = None,
            attempt: int = 1,
            finish_reason: str | None = None,
            parse_state: str | None = None,
            raw_text: str | None = None,
        ) -> None:
            end = time.perf_counter()
            spans.append(
                Span(
                    name=name,
                    start_ms=max(0.0, (start - started) * 1000),
                    end_ms=max(0.0, (end - started) * 1000),
                    kind=kind,
                    attempt=attempt,
                    model=model,
                    status=status,
                    ttft_ms=ttft_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    finish_reason=finish_reason,
                    parse_state=parse_state,
                    raw_text=None if raw_text is None else raw_text[:500],
                )
            )

        decide_info: dict[str, Any] = {
            "mode": self.decide_mode,
            "gate": "strict" if getattr(self.gate, "strict", False) else "legacy",
        }

        last_draft: list[str] = []

        def done(*args: Any, **kwargs: Any) -> PipelineResult:
            return replace(
                self._result(*args, **kwargs),
                decide=dict(decide_info),
                raw_answer=last_draft[-1] if last_draft else None,
            )

        try:
            self._check_deadline(deadline_monotonic)
            understand, understand_call_start, understand_attempt = await self._complete_with_retry(
                [
                    {"role": "system", "content": UNDERSTAND_SYSTEM},
                    {"role": "user", "content": question},
                ],
                deadline_monotonic=deadline_monotonic,
                experiment=experiment,
                model=self.model_by_step.get("understand"),
                max_completion_tokens=self.output_limits["understand"],
                spans=spans,
                request_started=started,
                step="understand",
            )
            span(
                "understand",
                understand_call_start,
                model=understand.model,
                tokens_in=understand.usage.input_tokens,
                tokens_out=understand.usage.output_tokens,
                cached_tokens=understand.usage.cached_tokens,
                reasoning_tokens=understand.usage.reasoning_tokens,
                attempt=understand_attempt,
                finish_reason=getattr(understand, "finish_reason", None),
                parse_state=parse_understanding(understand.text, question)[3],
                raw_text=understand.text,
            )
            query, papers, paper_source, _ = parse_understanding(understand.text, question)
            allowed_papers = set(papers) if self.scope_to_named_papers else set()
            decide_info.update(papers=sorted(allowed_papers), paper_source=paper_source)

            self._check_deadline(deadline_monotonic)
            retrieval_start = time.perf_counter()
            if self.content_word_query:
                query = content_query(query)
            retrieval = self.retriever.search(
                query,
                use_semantic=use_semantic,
                rerank=rerank,
                paper_ids=allowed_papers or None,
            )
            self._check_deadline(deadline_monotonic)
            span("retrieve_tool", retrieval_start)
            retrieved = [dict(hit.chunk) for hit in retrieval.hits]
            chunk_ids = tuple(str(chunk["chunk_id"]) for chunk in retrieved)
            if not retrieved:
                decide_info.update(outcome="no_passages")
                return done(
                    request_id,
                    SAFE_REFUSAL,
                    "ok",
                    False,
                    spans,
                    chunk_ids,
                    "no passages retrieved",
                    started,
                    arrival,
                    deadline,
                    scenario=scenario,
                    level=degradation_level,
                    first_token_ms=(time.perf_counter() - started) * 1000,
                )

            bm25_scores = [hit.bm25_score for hit in retrieval.hits if hit.bm25_score is not None]
            top_bm25 = max(bm25_scores) if bm25_scores else None
            decide_info.update(
                top_bm25=None if top_bm25 is None else round(top_bm25, 3),
                min_bm25=self.precheck_min_bm25,
            )
            self._check_deadline(deadline_monotonic)
            if self.decide_mode == "precheck_abstain":
                # Deterministic branch, zero tokens: refuse when even the best
                # passage matches the question's words too weakly.
                precheck_start = time.perf_counter()
                passed = top_bm25 is None or top_bm25 >= self.precheck_min_bm25
                span(
                    "decide_precheck",
                    precheck_start,
                    status="ok" if passed else "refused",
                    parse_state="yes" if passed else "no",
                )
                decide_info.update(precheck="pass" if passed else "fail")
                if not passed:
                    decide_info.update(outcome="refused_by_precheck")
                    return done(
                        request_id, SAFE_REFUSAL, "ok", False, spans, chunk_ids,
                        "retrieval pre-check: best passage score below threshold",
                        started, arrival, deadline, scenario=scenario,
                        level=degradation_level,
                        first_token_ms=(time.perf_counter() - started) * 1000,
                    )
            else:
                judge_messages = [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": self._evidence_prompt(question, retrieved)},
                ]
                state, reason = "uncertain", ""
                # One retry only, and only when the first reply could not be read.
                for judge_try in (1, 2):
                    try:
                        judge, judge_call_start, judge_attempt = await self._complete_with_retry(
                            judge_messages,
                            deadline_monotonic=deadline_monotonic,
                            experiment=experiment,
                            model=self.model_by_step.get("judge"),
                            max_completion_tokens=self.output_limits["judge"],
                            spans=spans,
                            request_started=started,
                            step="judge",
                            extra_options=self.judge_retry_options if judge_try == 2 else None,
                        )
                    except (LLMRequestError, RuntimeError):
                        if judge_try == 1:
                            raise
                        # The retry is optional. If the provider rejects it (for
                        # example a 400 for an unsupported option, or a 429), keep
                        # "uncertain" and let the drafter and the gate decide.
                        reason = "judge retry failed; proceeding as uncertain"
                        break
                    finish_reason = getattr(judge, "finish_reason", None)
                    state, reason = parse_judgment(judge.text, finish_reason)
                    span(
                        "decide_answerability" if judge_try == 1 else "decide_answerability_retry",
                        judge_call_start,
                        model=judge.model,
                        tokens_in=judge.usage.input_tokens,
                        tokens_out=judge.usage.output_tokens,
                        cached_tokens=judge.usage.cached_tokens,
                        reasoning_tokens=judge.usage.reasoning_tokens,
                        attempt=judge_attempt,
                        finish_reason=finish_reason,
                        parse_state=state,
                        raw_text=judge.text,
                    )
                    if state != "uncertain":
                        break
                    self._check_deadline(deadline_monotonic)
                decide_info.update(judge_state=state)
                if state == "no":
                    decide_info.update(outcome="refused_by_judge")
                    return done(
                        request_id, SAFE_REFUSAL, "ok", False, spans, chunk_ids,
                        reason or "the evidence judge found no answer",
                        started, arrival, deadline, scenario=scenario,
                        level=degradation_level,
                        first_token_ms=(time.perf_counter() - started) * 1000,
                    )
                # "uncertain" falls through: the drafter may abstain and the
                # sentence gate still checks every sentence.

            answer = ""
            gate_result: GateResult | None = None
            repairs = 0
            allowed_repairs = self.max_repairs if degradation_level == "full" else 0
            for attempt in range(allowed_repairs + 1):
                self._check_deadline(deadline_monotonic)
                step = "draft" if attempt == 0 else "repair"
                prompt = self._draft_prompt(question, retrieved)
                if attempt:
                    prompt = self._repair_prompt(question, answer, gate_result, retrieved)
                stream_messages = [
                    {"role": "system", "content": DRAFT_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                answer, capture, provider_attempt, draft_call_start = await self._stream_with_retry(
                    stream_messages,
                    deadline_monotonic=deadline_monotonic,
                    experiment=experiment,
                    model=self.model_by_step.get(step),
                    max_completion_tokens=self.output_limits[step],
                    spans=spans,
                    request_started=started,
                    step=step,
                )
                response = capture.response
                span(
                    step,
                    draft_call_start,
                    model=response.model if response else self.model_by_step.get(step),
                    status="ok" if response else "error",
                    ttft_ms=response.time_to_first_chunk_ms if response else None,
                    tokens_in=response.usage.input_tokens if response else None,
                    tokens_out=response.usage.output_tokens if response else None,
                    cached_tokens=response.usage.cached_tokens if response else None,
                    reasoning_tokens=response.usage.reasoning_tokens if response else None,
                    attempt=provider_attempt,
                    finish_reason=getattr(response, "finish_reason", None),
                    raw_text=answer,  # first 500 characters of this draft, markers included
                )
                last_draft.append(answer)
                if is_abstention(answer):
                    # The drafter read the passages and found no answer. This is
                    # the "papers do not say" branch; a repair would only invite a guess.
                    decide_info.update(outcome="drafter_abstained")
                    return done(
                        request_id, SAFE_REFUSAL, "ok", False, spans, chunk_ids,
                        f"drafter replied {ABSTAIN_SENTINEL}",
                        started, arrival, deadline, scenario=scenario,
                        level=degradation_level, repairs=repairs,
                        first_token_ms=(time.perf_counter() - started) * 1000,
                    )
                gate_start = time.perf_counter()
                gate_result = self.gate.check(
                    answer,
                    retrieved_chunks=retrieved,
                    allowed_paper_ids=allowed_papers or None,
                    question=question,
                )
                span(
                    "sentence_gate",
                    gate_start,
                    status="ok" if gate_result.accepted else "rejected",
                    attempt=attempt + 1,
                    raw_text=gate_result.reason,  # which check failed, when one did
                )
                if gate_result.accepted:
                    decide_info.update(outcome="answered")
                    return done(
                        request_id,
                        gate_result.rendered_answer or answer,
                        "ok",
                        True,
                        spans,
                        chunk_ids,
                        gate_result.reason,
                        started,
                        arrival,
                        deadline,
                        scenario=scenario,
                        level=degradation_level,
                        repairs=repairs,
                        first_token_ms=(time.perf_counter() - started) * 1000,
                    )
                if attempt < allowed_repairs:
                    repairs += 1

            decide_info.update(outcome="refused_by_gate")
            return done(
                request_id,
                SAFE_REFUSAL,
                "ok",
                False,
                spans,
                chunk_ids,
                gate_result.reason if gate_result else "answer gate failed",
                started,
                arrival,
                deadline,
                scenario=scenario,
                level=degradation_level,
                repairs=repairs,
                first_token_ms=(time.perf_counter() - started) * 1000,
            )
        except (TimeoutError, LLMRequestError, RuntimeError) as exc:
            status = (
                "timeout"
                if isinstance(exc, TimeoutError)
                else "rate_limited"
                if isinstance(exc, RateLimitAdmissionError)
                or isinstance(exc, LLMRequestError) and exc.status_code == 429
                else "error"
            )
            decide_info.update(outcome=status)
            reply = LIMIT_REPLIES.get(status, SAFE_REFUSAL)
            # A limit hit after retrieval: the passages are already in hand and cost
            # nothing to send, so the person gets them rather than only an apology.
            shown = [
                chunk for chunk in retrieved[:2]
                if status == "rate_limited" and self._extractive_overlap(question, str(chunk["text"]))
            ]
            if shown:
                reply = "\n\n".join(
                    [reply, PASSAGES_HEADER]
                    + [
                        f"\"{' '.join(str(chunk['text']).split())}\" "
                        + self.gate._render_citation(str(chunk["chunk_id"]), {str(chunk["chunk_id"]): chunk})
                        for chunk in shown
                    ]
                )
            return replace(
                done(
                    request_id,
                    reply,
                    status,
                    False,
                    spans,
                    chunk_ids,
                    str(exc),
                    started,
                    arrival,
                    deadline,
                    scenario=scenario,
                    level="wait" if status == "timeout" else "extractive" if shown else degradation_level,
                    # No checked answer was delivered, so there is no first-token time.
                    first_token_ms=None,
                ),
                fallback_delivered=bool(shown),
            )

    async def run_extractive(
        self,
        question: str,
        *,
        request_id: str,
        deadline_monotonic: float,
        scenario: str = "single",
        question_id: str | None = None,
        scheduled_arrival: datetime | None = None,
        scheduled_arrival_monotonic: float | None = None,
        limit_notice: str | None = None,
        fallback_status: Literal["rate_limited", "timeout"] = "rate_limited",
        passages: int = 2,
        ladder: Mapping[str, Any] | None = None,
    ) -> PipelineResult:
        """Answer without a model call.

        With ``limit_notice``: send the notice and the top passages verbatim with
        citations, no first-token time, ``fallback_delivered`` in the trace. The
        status says why there is no model answer: ``rate_limited`` when a limit
        was in the way, ``timeout`` when the request allowed too little time. Without it: the
        older behaviour, one citation-checked sentence from the top passage.
        """
        if not question.strip():
            raise ValueError("question cannot be empty")
        if question_id is not None and not question_id.strip():
            raise ValueError("question_id cannot be empty when provided")
        loop = asyncio.get_running_loop()
        now_monotonic = loop.time()
        dispatch_lag = max(
            0.0,
            now_monotonic - scheduled_arrival_monotonic
            if scheduled_arrival_monotonic is not None
            else 0.0,
        )
        started = time.perf_counter() - dispatch_lag
        arrival_dt = scheduled_arrival or datetime.now(timezone.utc)
        if arrival_dt.tzinfo is None or arrival_dt.utcoffset() is None:
            raise ValueError("scheduled_arrival must be timezone-aware")
        arrival_dt = arrival_dt.astimezone(timezone.utc)
        arrival = arrival_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        base_monotonic = scheduled_arrival_monotonic or now_monotonic
        deadline = (
            arrival_dt + timedelta(seconds=max(0.0, deadline_monotonic - base_monotonic))
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        spans: list[Span] = []
        if dispatch_lag > 0:
            spans.append(
                Span(
                    name="scheduler_wait",
                    start_ms=0,
                    end_ms=dispatch_lag * 1000,
                    kind="wait",
                    status="queued",
                )
            )
        if deadline_monotonic <= loop.time():
            result = self._result(
                request_id, SAFE_REFUSAL, "timeout", False, spans, (),
                "deadline expired before extractive retrieval", started, arrival, deadline,
                scenario=scenario, level="wait", first_token_ms=None,
            )
            self._persist(
                result,
                scenario=scenario,
                question=question,
                question_id=question_id,
            )
            return result

        retrieval_start = time.perf_counter()
        allowed_papers = (
            set(papers_from_question(question)) if self.scope_to_named_papers else set()
        )
        retrieval = self.retriever.search(question, paper_ids=allowed_papers or None)
        spans.append(
            Span(
                name="retrieve_tool",
                start_ms=max(0.0, (retrieval_start - started) * 1000),
                end_ms=max(0.0, (time.perf_counter() - started) * 1000),
                kind="work",
            )
        )
        if loop.time() >= deadline_monotonic:
            result = self._result(
                request_id, SAFE_REFUSAL, "timeout", False, spans, (),
                "request deadline expired during extractive retrieval", started, arrival, deadline,
                scenario=scenario, level="wait", first_token_ms=None,
            )
            self._persist(
                result,
                scenario=scenario,
                question=question,
                question_id=question_id,
            )
            return result
        chunks = [dict(hit.chunk) for hit in retrieval.hits]
        if limit_notice is not None:
            # Passages are shown only when they clearly match the question's words.
            shown = [
                chunk for chunk in chunks[:passages]
                if self._extractive_overlap(question, str(chunk["text"]))
            ]
            parts = [limit_notice]
            if shown:
                parts.append(PASSAGES_HEADER)
                parts.extend(
                    f"\"{' '.join(str(chunk['text']).split())}\" "
                    + self.gate._render_citation(str(chunk["chunk_id"]), {str(chunk["chunk_id"]): chunk})
                    for chunk in shown
                )
            result = replace(
                self._result(
                    request_id, "\n\n".join(parts), fallback_status, False, spans,
                    [str(chunk["chunk_id"]) for chunk in chunks],
                    "limit reached; passages sent instead of a model answer"
                    if shown else "limit reached; no passage matched well enough to show",
                    started, arrival, deadline, scenario=scenario, level="extractive",
                    first_token_ms=None,
                ),
                fallback_delivered=bool(shown),
                ladder=ladder,
            )
            self._persist(result, scenario=scenario, question=question, question_id=question_id)
            return result
        answer = SAFE_REFUSAL
        answerable = False
        reason = "no passages retrieved"
        if chunks:
            chunk = chunks[0]
            text = str(chunk["text"]).strip()
            if not self._extractive_overlap(question, text):
                reason = "top passage has insufficient lexical overlap for extractive answering"
            else:
                sentence_end = next(
                    (index + 1 for index, char in enumerate(text) if char in ".!?"),
                    len(text),
                )
                claim = text[:sentence_end].strip()
                candidate = f"{claim} [[cite:{chunk['chunk_id']}]]" if claim else ""
                gate_start = time.perf_counter()
                gate_result = self.gate.check(
                    candidate,
                    retrieved_chunks=[chunk],
                    allowed_paper_ids=allowed_papers or None,
                )
                spans.append(
                    Span(
                        name="sentence_gate",
                        start_ms=max(0.0, (gate_start - started) * 1000),
                        end_ms=max(0.0, (time.perf_counter() - started) * 1000),
                        kind="work",
                        status="ok" if gate_result.accepted else "rejected",
                    )
                )
                if gate_result.accepted:
                    answer = gate_result.rendered_answer or candidate
                    answerable = True
                    reason = gate_result.reason
                else:
                    reason = gate_result.reason
        result = self._result(
            request_id,
            answer,
            "ok",
            answerable,
            spans,
            [str(chunk["chunk_id"]) for chunk in chunks],
            reason,
            started,
            arrival,
            deadline,
            scenario=scenario,
            level="extractive",
            first_token_ms=(time.perf_counter() - started) * 1000,
        )
        self._persist(
            result,
            scenario=scenario,
            question=question,
            question_id=question_id,
        )
        return result

    @staticmethod
    def _extractive_overlap(question: str, passage: str) -> bool:
        query_terms = {
            term for term in tokenize(question)
            if len(term) >= 3 and term not in _EXTRACTIVE_STOPWORDS
        }
        passage_terms = set(tokenize(passage))
        overlap = query_terms & passage_terms
        return len(overlap) >= 2 or any(len(term) >= 7 for term in overlap)

    def _check_deadline(self, deadline_monotonic: float) -> None:
        if self._remaining(deadline_monotonic) <= 0:
            raise TimeoutError("request deadline expired")

    @staticmethod
    def _remaining(deadline_monotonic: float) -> float:
        remaining = deadline_monotonic - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("request deadline expired")
        return remaining

    @classmethod
    async def _within_deadline(cls, awaitable: Any, deadline_monotonic: float) -> Any:
        """Cancel provider work at the request deadline and surface a timeout."""
        try:
            remaining = cls._remaining(deadline_monotonic)
        except TimeoutError:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("request deadline expired during provider call") from exc

    async def _complete_with_retry(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        deadline_monotonic: float,
        experiment: str,
        model: str | None,
        max_completion_tokens: int,
        spans: list[Span],
        request_started: float,
        step: str,
        extra_options: Mapping[str, Any] | None = None,
    ) -> tuple[Any, float, int]:
        attempt = 1
        while True:
            attempt_started = time.perf_counter()
            try:
                operation = self.client.complete(
                    messages,
                    experiment=experiment,
                    model=model,
                    approval=self.approval,
                    # An explicit max_completion_tokens in the options overrides the step cap.
                    **{
                        "max_completion_tokens": max_completion_tokens,
                        **self.options_by_model.get(model or "", {}),
                        **self.options_by_step.get(step, {}),
                        **(extra_options or {}),
                    },
                )
                response = await self._within_deadline(operation, deadline_monotonic)
                return response, attempt_started, attempt
            except (TimeoutError, LLMRequestError, RuntimeError) as exc:
                attempt_ended = time.perf_counter()
                failed_status = (
                    "timeout" if isinstance(exc, TimeoutError)
                    else f"http_{exc.status_code}" if isinstance(exc, LLMRequestError)
                    else "error"
                )
                spans.append(
                    Span(
                        name=f"{step}_attempt_{attempt}",
                        start_ms=max(0.0, (attempt_started - request_started) * 1000),
                        end_ms=max(0.0, (attempt_ended - request_started) * 1000),
                        kind="work",
                        attempt=attempt,
                        model=model,
                        status=failed_status,
                    )
                )
                if not await self._wait_to_retry(
                    exc, attempt, deadline_monotonic, spans, request_started, step, model
                ):
                    raise
                attempt += 1

    async def _stream_with_retry(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        deadline_monotonic: float,
        experiment: str,
        model: str | None,
        max_completion_tokens: int,
        spans: list[Span],
        request_started: float,
        step: str,
    ) -> tuple[str, StreamCapture, int, float]:
        attempt = 1
        while True:
            attempt_started = time.perf_counter()
            capture = StreamCapture()
            pieces: list[str] = []
            try:
                async with asyncio.timeout(self._remaining(deadline_monotonic)):
                    async for piece in self.client.stream(
                        messages,
                        experiment=experiment,
                        capture=capture,
                        model=model,
                        approval=self.approval,
                        **{
                            "max_completion_tokens": max_completion_tokens,
                            **self.options_by_model.get(model or "", {}),
                            **self.options_by_step.get(step, {}),
                        },
                    ):
                        pieces.append(piece)
                return "".join(pieces), capture, attempt, attempt_started
            except (TimeoutError, LLMRequestError, RuntimeError) as exc:
                # Replaying after partial output can duplicate user-visible tokens.
                attempt_ended = time.perf_counter()
                failed_status = (
                    "timeout" if isinstance(exc, TimeoutError)
                    else f"http_{exc.status_code}" if isinstance(exc, LLMRequestError)
                    else "error"
                )
                spans.append(
                    Span(
                        name=f"{step}_attempt_{attempt}",
                        start_ms=max(0.0, (attempt_started - request_started) * 1000),
                        end_ms=max(0.0, (attempt_ended - request_started) * 1000),
                        kind="work",
                        attempt=attempt,
                        model=model,
                        status=failed_status,
                    )
                )
                if pieces or not await self._wait_to_retry(
                    exc, attempt, deadline_monotonic, spans, request_started, step, model
                ):
                    raise
                attempt += 1

    async def _wait_to_retry(
        self,
        exc: Exception,
        attempt: int,
        deadline_monotonic: float,
        spans: list[Span],
        request_started: float,
        step: str,
        model: str | None,
    ) -> bool:
        if self.retry_policy is None or self.retry_budget is None:
            return False
        if isinstance(exc, TimeoutError):
            status = "timeout"
        elif isinstance(exc, LLMRequestError) and exc.status_code == 429:
            status = "rate_limited"
        elif isinstance(exc, LLMRequestError) and 500 <= exc.status_code <= 599:
            status = "server_error"
        elif isinstance(exc, RateLimitAdmissionError):
            status = "rate_limited"
        else:
            status = "error"
        retry_after = None
        if isinstance(exc, LLMRequestError):
            header = next(
                (value for key, value in exc.headers.items() if key.casefold() == "retry-after"),
                None,
            )
            retry_after = self._parse_retry_after(header)
        elif isinstance(exc, RateLimitAdmissionError):
            retry_after = exc.admission.wait_seconds
        decision = await self.retry_policy.decide(
            attempt=attempt,
            status=status,
            deadline_monotonic=deadline_monotonic,
            budget=self.retry_budget,
            retry_after_s=retry_after,
        )
        if not decision.retry:
            return False
        wait_start = time.perf_counter()
        try:
            await asyncio.sleep(decision.delay_s)
        except TimeoutError:
            return False
        wait_end = time.perf_counter()
        spans.append(
            Span(
                name=f"{step}_retry_backoff",
                start_ms=max(0.0, (wait_start - request_started) * 1000),
                end_ms=max(0.0, (wait_end - request_started) * 1000),
                kind="retry",
                attempt=attempt,
                model=model,
                status="retry",
            )
        )
        return True

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
            return seconds if seconds >= 0 else None
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _evidence_prompt(question: str, chunks: Sequence[Mapping[str, Any]]) -> str:
        evidence = [
            {
                "chunk_id": chunk["chunk_id"],
                "paper": chunk["paper_title"],
                "paper_id": chunk["paper_id"],
                "pdf_page": chunk["page_number"],
                "section": chunk.get("section", ""),
                "text": chunk["text"],
            }
            for chunk in chunks
        ]
        return json.dumps(
            {"question": question, "evidence": evidence}, ensure_ascii=False
        )

    @classmethod
    def _draft_prompt(cls, question: str, chunks: Sequence[Mapping[str, Any]]) -> str:
        return "Write a concise answer to this question using the evidence.\n" + cls._evidence_prompt(
            question, chunks
        )

    @classmethod
    def _repair_prompt(
        cls,
        question: str,
        prior_answer: str,
        gate_result: GateResult | None,
        chunks: Sequence[Mapping[str, Any]],
    ) -> str:
        issue = gate_result.reason if gate_result else "the answer failed validation"
        return (
            f"Rewrite the answer to fix this citation-check failure: {issue}.\n"
            f"Previous answer: {prior_answer}\n"
            + cls._evidence_prompt(question, chunks)
        )

    def _result(
        self,
        request_id: str,
        answer: str,
        status: str,
        answerable: bool,
        spans: Sequence[Span],
        chunk_ids: Sequence[str],
        gate_reason: str,
        started: float,
        arrival: str,
        deadline: str,
        *,
        scenario: str = "single",
        level: Literal["full", "lite", "extractive", "wait"] = "full",
        repairs: int = 0,
        first_token_ms: float | None = None,
    ) -> PipelineResult:
        total = (time.perf_counter() - started) * 1000
        provider = getattr(getattr(self.client, "config", None), "provider", "standin")
        source = "real" if provider == "groq" else "stand-in"
        result = PipelineResult(
            request_id=request_id,
            answer=answer,
            status=status,
            answerable=answerable,
            retrieval_setting=self.retriever.setting,
            cache_hit=False,
            coalesced=False,
            repairs=repairs,
            first_token_ms=first_token_ms,
            total_ms=total,
            spans=tuple(spans),
            retrieved_chunk_ids=tuple(chunk_ids),
            gate_reason=gate_reason,
            arrival=arrival,
            deadline=deadline,
            source=source,
            level=level,
        )
        return result

    def _persist(
        self,
        result: PipelineResult,
        *,
        scenario: str,
        question: str,
        question_id: str | None,
    ) -> None:
        if self.results_path is None:
            return
        results_path = Path(self.results_path)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        trace_dir = self.trace_dir or str(results_path.parent / "traces")
        trace = RequestTrace(
            request_id=result.request_id,
            question_id=question_id,
            question=question,
            answer=result.answer,
            retrieved_chunk_ids=list(result.retrieved_chunk_ids),
            scenario=scenario,
            arrival=result.arrival,
            deadline=result.deadline,
            level=result.level,
            cache_hit=result.cache_hit,
            coalesced=result.coalesced,
            spans=list(result.spans),
            # "Gave up" means the person left with nothing; passages count as something.
            gave_up=result.status == "timeout" and not result.fallback_delivered,
            decide=None if result.decide is None else dict(result.decide),
            raw_answer=result.raw_answer,
            fallback_delivered=result.fallback_delivered,
            ladder=None if result.ladder is None else dict(result.ladder),
        )
        def token_total(field: str) -> str:
            provider_spans = [
                span for span in result.spans if span.model is not None and span.kind == "work"
            ]
            # A refused or failed attempt reports no usage and bills nothing, so it is
            # left out. A completed call with unknown usage still blanks the total.
            values = [getattr(span, field) for span in provider_spans if span.status == "ok"]
            if not values or any(value is None for value in values):
                return ""
            return str(sum(values))

        row = {
            "request_id": result.request_id,
            "scenario": scenario,
            "source": result.source,
            "start_time": result.arrival,
            "first_token_ms": "" if result.first_token_ms is None else f"{result.first_token_ms:.3f}",
            "total_ms": f"{result.total_ms:.3f}",
            "status": result.status,
            "retries": str(sum(span.kind == "retry" for span in result.spans)),
            "tokens_in": token_total("tokens_in"),
            "tokens_out": token_total("tokens_out"),
            "retrieval_setting": result.retrieval_setting,
            "answer_grade": "not_graded" if result.source == "stand-in" else "",
        }
        with _RESULTS_LOCK:
            write_header = not results_path.exists() or results_path.stat().st_size == 0
            if not write_header:
                with results_path.open(newline="", encoding="utf-8-sig") as existing:
                    actual_header = next(csv.reader(existing), [])
                if tuple(actual_header) != RESULT_FIELDS:
                    raise ValueError(
                        f"Refusing to append to results file with unexpected header: {results_path}"
                    )
            write_trace(trace, directory=trace_dir)
            with results_path.open("a", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
