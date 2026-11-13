#!/usr/bin/env python3
"""Run a small, guarded real-provider steady-arrival pipeline experiment (E6)."""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from eval.recall import load_questions
from eval.run_quality import _model_estimate, _read_yaml, _select_questions, _setting
from library.ingest import CHUNKS_DIR
from relay.config import PROJECT_ROOT, load_config
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


APPROVED_MODEL = "openai/gpt-oss-20b"
APPROVED_SETTING = "R1"
APPROVED_QUESTION_IDS = ("Q02", "Q04", "Q10", "Q13", "Q16")
ARRIVAL_INTERVAL_SECONDS = 30.0
RESULTS_PATH = PROJECT_ROOT / "results" / "results.csv"
LEDGER_PATH = PROJECT_ROOT / "budget_ledger.csv"


def _today_usage(model: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    if not LEDGER_PATH.exists():
        return 0
    with LEDGER_PATH.open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        return sum(
            int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)
            for row in rows
            if row.get("model") == model
            and (row.get("timestamp") or "").startswith(today)
        )


def _existing_request_ids() -> set[str]:
    if not RESULTS_PATH.exists():
        return set()
    with RESULTS_PATH.open(newline="", encoding="utf-8-sig") as stream:
        return {row["request_id"] for row in csv.DictReader(stream)}


async def _run(args: argparse.Namespace) -> None:
    if args.model != APPROVED_MODEL or args.setting != APPROVED_SETTING:
        raise ValueError("this reviewed E6 run is fixed to gpt-oss-20b with R1 retrieval")
    setting = _setting(APPROVED_SETTING)
    ids = APPROVED_QUESTION_IDS
    questions_by_id = {q.question_id: q for q in load_questions(args.questions)}
    selected = _select_questions(tuple(questions_by_id.values()), ids)

    retrieval_config = _read_yaml(PROJECT_ROOT / "config" / "retrieval.yaml")
    pipeline_config = _read_yaml(PROJECT_ROOT / "config" / "pipeline.yaml")
    limiter_config = _read_yaml(PROJECT_ROOT / "config" / "limiter.yaml")
    output_limits = {
        "understand": int(pipeline_config.get("understand_max_completion_tokens", 100)),
        "judge": int(pipeline_config.get("judge_max_completion_tokens", 120)),
        "draft": int(pipeline_config.get("draft_max_completion_tokens", 400)),
        "repair": int(pipeline_config.get("repair_max_completion_tokens", 400)),
    }
    max_repairs = int(pipeline_config.get("max_repairs", 1))
    tpd_reference = int(limiter_config["public_free_plan_tpd_reference"])
    required_buffer = 0.10
    reranker_name = retrieval_config.get("reranker_model") if setting.rerank else None
    retriever = PaperRetriever.from_jsonl(
        setting.name,
        top_k=setting.top_k,
        chunk_dir=args.chunks,
        reranker_model_name=str(reranker_name) if reranker_name else None,
    )

    estimates = [
        _model_estimate(
            model=args.model,
            questions=[question],
            retriever=retriever,
            max_repairs=max_repairs,
            output_limits=output_limits,
        )
        for question in selected
    ]
    estimate = ModelEstimate(
        requests=sum(item.requests for item in estimates),
        input_tokens=sum(item.input_tokens for item in estimates),
        output_tokens=sum(item.output_tokens for item in estimates),
    )
    actual_today = _today_usage(args.model)
    estimated_total = estimate.input_tokens + estimate.output_tokens
    print(
        f"E6 conservative budget: {actual_today} measured tokens today + "
        f"{estimated_total} reserved tokens = {actual_today + estimated_total} "
        f"versus {tpd_reference} public TPD reference for {args.model}; "
        f"ledger-only headroom={tpd_reference - actual_today - estimated_total}."
    )
    if actual_today + estimated_total > tpd_reference * (1 - required_buffer):
        raise ValueError("E6 measured usage plus estimate would leave under 10% of public TPD reference")
    experiment_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    request_ids = [f"E6-{experiment_stamp}-{index:05d}" for index in range(len(ids))]
    collision = set(request_ids) & _existing_request_ids()
    if collision:
        raise ValueError(f"request ID collision: {sorted(collision)}")
    if args.dry_run:
        print(
            f"Dry run only: {len(selected)} requests at "
            f"{60 / ARRIVAL_INTERVAL_SECONDS:.1f} arrivals/minute; "
            f"minimum spacing={ARRIVAL_INTERVAL_SECONDS}s; no provider calls."
        )
        return

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    runtime_config = load_config().model_copy(update={"provider": "groq"})
    _ = runtime_config.api_key
    snapshots = last_known_daily_requests_remaining(LEDGER_PATH, [args.model])
    token_snapshots = last_known_daily_tokens_remaining(LEDGER_PATH, [args.model])
    print(f"Last RPD snapshot: {snapshots[args.model]}; TPD snapshot: {token_snapshots[args.model]}")
    approval = await approve_real_experiment(
        f"E6-real-steady-{experiment_stamp}",
        {args.model: estimate},
        ledger_path=LEDGER_PATH,
        public_tpd_reference=tpd_reference,
    )

    client = LLMClient(runtime_config, ledger_path=LEDGER_PATH)
    pipeline = CitedAnswerPipeline(
        client,
        retriever,
        model_by_step={name: args.model for name in ("understand", "judge", "draft", "repair")},
        approval=approval,
        max_repairs=max_repairs,
        **pipeline_kwargs_from_config(pipeline_config),
        understand_max_tokens=output_limits["understand"],
        judge_max_tokens=output_limits["judge"],
        draft_max_tokens=output_limits["draft"],
        repair_max_tokens=output_limits["repair"],
        retry_policy=None,
        retry_budget=None,
        results_path=RESULTS_PATH,
        trace_dir=PROJECT_ROOT / "results" / "traces",
    )
    try:
        loop = asyncio.get_running_loop()
        last_arrival: float | None = None
        for index, (question, request_id) in enumerate(zip(selected, request_ids)):
            if last_arrival is not None:
                await asyncio.sleep(
                    max(0.0, last_arrival + ARRIVAL_INTERVAL_SECONDS - loop.time())
                )
            last_arrival = loop.time()
            result = await pipeline.run(
                question.question,
                request_id=request_id,
                question_id=question.question_id,
                experiment=f"E6-real-steady-{experiment_stamp}",
                deadline_monotonic=loop.time() + args.deadline_seconds,
                scenario="steady",
                use_semantic=False,
                rerank=setting.rerank,
            )
            print(
                f"{index + 1}/{len(selected)} {question.question_id}: "
                f"{result.status}; {result.total_ms:.0f} ms; repairs={result.repairs}"
            )
            if result.status != "ok":
                raise RuntimeError(
                    f"stopping E6 after {result.status} on {question.question_id}; "
                    "inspect its trace and ledger before any further provider call"
                )
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=(APPROVED_MODEL,), default=APPROVED_MODEL)
    parser.add_argument("--setting", choices=(APPROVED_SETTING,), default=APPROVED_SETTING)
    parser.add_argument("--deadline-seconds", type=float, default=120.0)
    parser.add_argument("--questions", type=Path, default=PROJECT_ROOT / "eval" / "questions.yaml")
    parser.add_argument("--chunks", type=Path, default=CHUNKS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.deadline_seconds <= 0:
        parser.error("deadline must be positive")
    try:
        asyncio.run(_run(args))
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
