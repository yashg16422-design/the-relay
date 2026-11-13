#!/usr/bin/env python3
"""Run a bounded human-graded answer-quality comparison against Groq."""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import yaml
from dotenv import load_dotenv

from eval.recall import EvalQuestion, load_questions
from library.ingest import CHUNKS_DIR, SETTINGS
from relay.config import PROJECT_ROOT, load_config
from relay.gate import GateResult
from relay.llm_client import (
    LLMClient,
    ModelEstimate,
    approve_real_experiment,
    last_known_daily_requests_remaining,
    last_known_daily_tokens_remaining,
)
from relay.decide import pipeline_kwargs_from_config
from relay.pipeline import CitedAnswerPipeline
from relay.retrieval import PaperRetriever


COHORTS = {
    "R1": {
        "day1": ("Q02", "Q04", "Q10", "Q13", "Q16", "Q19", "Q25", "Q34"),
        "day2": ("Q06", "Q08", "Q17", "Q24", "Q28", "Q31", "Q32"),
    },
    "R2": {
        "day1": ("Q02", "Q04", "Q10", "Q13", "Q16", "Q19", "Q25", "Q34"),
        "day2": ("Q06", "Q08", "Q17", "Q24", "Q28", "Q31", "Q32"),
    },
    "R3": {
        "day1": ("Q02", "Q13", "Q19", "Q24", "Q32"),
        "day2": ("Q04", "Q10", "Q16", "Q28", "Q31"),
        "day3": ("Q06", "Q08", "Q17", "Q25", "Q34"),
    },
}
DEFAULT_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
)
PIPELINE_SYSTEM = (
    "Answer only from the supplied passages. End every factual sentence with one or more "
    "markers in this exact form: [[cite:chunk_id]]. Do not invent citations, numbers, or names. "
    "Return only the answer."
)


def _read_yaml(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected a YAML mapping in {path}")
    return raw


def _select_questions(questions: Sequence[EvalQuestion], ids: Sequence[str]) -> list[EvalQuestion]:
    by_id = {question.question_id: question for question in questions}
    if len(ids) != len(set(ids)):
        raise ValueError("question IDs must be unique")
    missing = sorted(set(ids) - set(by_id))
    if missing:
        raise ValueError(f"unknown question IDs: {', '.join(missing)}")
    return [by_id[question_id] for question_id in ids]


def _setting(name: str):
    for setting in SETTINGS:
        if setting.name == name:
            return setting
    raise ValueError(f"unknown retrieval setting {name!r}; choose from {[s.name for s in SETTINGS]}")


def _model_estimate(
    *,
    model: str,
    questions: Sequence[EvalQuestion],
    retriever: PaperRetriever,
    max_repairs: int,
    output_limits: dict[str, int],
) -> ModelEstimate:
    """Bound one full pipeline path per question, including every allowed repair.

    Input estimates use the project's same UTF-8 byte-count bound as the real
    client. Retrieval is preflighted from the original question; if query
    rewriting later expands the request past this allowance, the client refuses
    to send it and the run stops safely.
    """
    system_understand = (
        "Rewrite the user's question as one concise search query. "
        'Return only JSON: {"query":"..."}.'
    )
    system_judge = (
        "Decide whether the evidence contains enough information to answer the question. "
        'Do not answer it. Return only JSON with boolean "answerable" and a short "reason".'
    )
    total_input = 0
    total_output = 0
    max_repairs = max(0, max_repairs)

    for question in questions:
        chunks = [
            dict(hit.chunk)
            for hit in retriever.search(
                question.question,
                top_k=retriever.top_k,
                rerank=bool(getattr(retriever, "_reranker_model_name", None)),
            ).hits
        ]
        understand_messages = [
            {"role": "system", "content": system_understand},
            {"role": "user", "content": question.question},
        ]
        judge_messages = [
            {"role": "system", "content": system_judge},
            {"role": "user", "content": CitedAnswerPipeline._evidence_prompt(question.question, chunks)},
        ]
        draft_messages = [
            {"role": "system", "content": PIPELINE_SYSTEM},
            {"role": "user", "content": CitedAnswerPipeline._draft_prompt(question.question, chunks)},
        ]
        total_input += LLMClient.input_token_upper_bound(
            understand_messages, model=model, max_completion_tokens=output_limits["understand"]
        )
        total_output += output_limits["understand"]
        total_input += LLMClient.input_token_upper_bound(
            judge_messages, model=model, max_completion_tokens=output_limits["judge"]
        )
        total_output += output_limits["judge"]
        total_input += LLMClient.input_token_upper_bound(
            draft_messages,
            model=model,
            stream=True,
            max_completion_tokens=output_limits["draft"],
        )
        total_output += output_limits["draft"]

        for _ in range(max_repairs):
            repair_prompt = CitedAnswerPipeline._repair_prompt(
                question.question,
                "x" * (output_limits["draft"] * 4),
                GateResult(False, "a sentence has no citation marker", None, ()),
                chunks,
            )
            repair_messages = [
                {"role": "system", "content": PIPELINE_SYSTEM},
                {"role": "user", "content": repair_prompt},
            ]
            total_input += LLMClient.input_token_upper_bound(
                repair_messages,
                model=model,
                stream=True,
                max_completion_tokens=output_limits["repair"],
            )
            total_output += output_limits["repair"]

    calls_per_question = 2 + 1 + max_repairs
    return ModelEstimate(
        requests=len(questions) * calls_per_question,
        input_tokens=total_input,
        output_tokens=total_output,
    )


async def _run(args: argparse.Namespace) -> None:
    question_path = args.questions
    setting = _setting(args.setting)
    cohort_map = COHORTS[setting.name]
    if args.question_id:
        selected_ids = args.question_id
        cohort_name = "custom"
    else:
        cohort_name = args.cohort or next(iter(cohort_map))
        if cohort_name not in cohort_map:
            raise ValueError(
                f"cohort {cohort_name!r} is unavailable for {setting.name}; "
                f"choose from {list(cohort_map)}"
            )
        selected_ids = cohort_map[cohort_name]
    questions = _select_questions(load_questions(question_path), selected_ids)
    unknown_models = sorted(set(args.model) - set(DEFAULT_MODELS))
    if unknown_models:
        raise ValueError(f"models are not in the verified candidate list: {', '.join(unknown_models)}")
    if len(args.model) != len(set(args.model)):
        raise ValueError("model IDs must be unique")
    if args.mode == "final" and len(args.model) != 1:
        raise ValueError("final mode requires exactly one selected --model")

    retrieval_config = _read_yaml(PROJECT_ROOT / "config" / "retrieval.yaml")
    pipeline_config = _read_yaml(PROJECT_ROOT / "config" / "pipeline.yaml")
    output_limits = {
        "understand": int(pipeline_config.get("understand_max_completion_tokens", 100)),
        "judge": int(pipeline_config.get("judge_max_completion_tokens", 120)),
        "draft": int(pipeline_config.get("draft_max_completion_tokens", 400)),
        "repair": int(pipeline_config.get("repair_max_completion_tokens", 400)),
    }
    max_repairs = int(pipeline_config.get("max_repairs", 1))
    limiter_config = _read_yaml(PROJECT_ROOT / "config" / "limiter.yaml")
    public_tpd_reference = limiter_config.get("public_free_plan_tpd_reference")
    public_tpd_reference = int(public_tpd_reference) if public_tpd_reference is not None else None
    reranker_name = retrieval_config.get("reranker_model") if setting.rerank else None
    retriever = PaperRetriever.from_jsonl(
        setting.name,
        top_k=setting.top_k,
        chunk_dir=args.chunks,
        reranker_model_name=str(reranker_name) if reranker_name else None,
    )
    estimates = {
        model: _model_estimate(
            model=model,
            questions=questions,
            retriever=retriever,
            max_repairs=max_repairs,
            output_limits=output_limits,
        )
        for model in args.model
    }
    over_reference = {}
    if public_tpd_reference is not None:
        over_reference = {
            model: estimate.input_tokens + estimate.output_tokens
            for model, estimate in estimates.items()
            if estimate.input_tokens + estimate.output_tokens > public_tpd_reference
        }

    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.mode == "selection":
        raw_run_dir = PROJECT_ROOT / "results" / "raw" / "model_selection" / run_stamp
        results_path = args.results or raw_run_dir / "results.csv"
        traces_path = args.traces or raw_run_dir / "traces"
    else:
        results_path = args.results or PROJECT_ROOT / "results" / "results.csv"
        traces_path = args.traces or PROJECT_ROOT / "results" / "traces"
    if args.dry_run:
        print(
            f"Experiment estimate: mode={args.mode}; cohort={cohort_name}; "
            f"{len(questions)} questions; setting={setting.name}; no provider calls"
        )
        snapshots = last_known_daily_requests_remaining(args.ledger, list(estimates))
        token_snapshots = last_known_daily_tokens_remaining(args.ledger, list(estimates))
        for model, estimate in estimates.items():
            remaining, timestamp = snapshots[model]
            tpd_remaining, tpd_timestamp = token_snapshots[model]
            total_tokens = estimate.input_tokens + estimate.output_tokens
            headroom = (
                f"{public_tpd_reference - total_tokens} vs published reference"
                if public_tpd_reference is not None
                else "unknown (no published reference configured)"
            )
            print(
                f"{model}: up to {estimate.requests} requests; "
                f"{estimate.input_tokens} input-token bound; "
                f"{estimate.output_tokens} output-token cap; estimated total={total_tokens}; "
                f"daily RPD remaining={remaining}"
                + (f" (last response {timestamp})" if timestamp else "")
                + f"; daily TPD remaining={tpd_remaining}"
                + (f" (last response {tpd_timestamp})" if tpd_timestamp else "")
                + f"; public-reference headroom={headroom}"
            )
        if over_reference:
            print("This cohort exceeds the public daily-token reference and will not be sent.")
        print(f"Output: {results_path}; traces: {traces_path}")
        return
    if over_reference:
        details = ", ".join(f"{model}={tokens}" for model, tokens in over_reference.items())
        raise ValueError(
            f"cohort exceeds documented public TPD reference ({public_tpd_reference}): {details}; split it"
        )
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    runtime_config = load_config().model_copy(update={"provider": "groq"})
    # Validate that the ignored .env contains the key without displaying it.
    _ = runtime_config.api_key

    experiment = f"E5-{args.mode}-{setting.name}-{cohort_name}-{run_stamp}"
    approval = await approve_real_experiment(
        experiment,
        estimates,
        ledger_path=args.ledger,
        public_tpd_reference=public_tpd_reference,
    )

    for model in args.model:
        client = LLMClient(runtime_config, ledger_path=args.ledger)
        pipeline = CitedAnswerPipeline(
            client,
            retriever,
            model_by_step={
                "understand": model,
                "judge": model,
                "draft": model,
                "repair": model,
            },
            approval=approval,
            max_repairs=max_repairs,
            **pipeline_kwargs_from_config(pipeline_config),
            understand_max_tokens=output_limits["understand"],
            judge_max_tokens=output_limits["judge"],
            draft_max_tokens=output_limits["draft"],
            repair_max_tokens=output_limits["repair"],
            # E5 isolates answer quality; retries are disabled so request/token
            # bounds stay fixed and a 429 is recorded without replay.
            retry_policy=None,
            retry_budget=None,
            results_path=results_path,
            trace_dir=traces_path,
        )
        try:
            for question in questions:
                safe_model = re.sub(r"[^A-Za-z0-9]+", "_", model)
                request_id = f"{run_stamp}-{setting.name}-{safe_model}-{question.question_id}"
                scenario = (
                    f"E5-model-selection-{setting.name}-{safe_model}"
                    if args.mode == "selection"
                    else f"E5-quality-{setting.name}"
                )
                loop = asyncio.get_running_loop()
                result = await pipeline.run(
                    question.question,
                    request_id=request_id,
                    question_id=question.question_id,
                    experiment=experiment,
                    deadline_monotonic=loop.time() + args.deadline_seconds,
                    scenario=scenario,
                    use_semantic=False,
                    rerank=setting.rerank,
                )
                print(
                    f"{question.question_id} {model}: {result.status}; "
                    f"{result.total_ms:.0f} ms; repairs={result.repairs}; "
                    f"answer_grade=human review required"
                )
                if result.status != "ok":
                    raise RuntimeError(
                        f"stopping the experiment after {result.status} on "
                        f"{question.question_id}; inspect the saved trace and ledger first"
                    )
        finally:
            await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("selection", "final"), default="selection")
    parser.add_argument("--setting", choices=[item.name for item in SETTINGS], default="R1")
    parser.add_argument("--cohort", choices=("day1", "day2", "day3"))
    parser.add_argument("--question-id", action="append", dest="question_id")
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--questions", type=Path, default=PROJECT_ROOT / "eval" / "questions.yaml")
    parser.add_argument("--chunks", type=Path, default=CHUNKS_DIR)
    parser.add_argument("--ledger", type=Path, default=PROJECT_ROOT / "budget_ledger.csv")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--deadline-seconds", type=float, default=120)
    parser.add_argument("--dry-run", action="store_true", help="show estimates without asking approval or calling Groq")
    args = parser.parse_args()
    if args.model is None:
        args.model = list(DEFAULT_MODELS) if args.mode == "selection" else []
    if args.deadline_seconds <= 0:
        parser.error("--deadline-seconds must be positive")
    try:
        asyncio.run(_run(args))
    except PermissionError as exc:
        parser.exit(2, f"{exc}\n")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
