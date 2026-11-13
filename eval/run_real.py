#!/usr/bin/env python3
"""Run the pipeline against real Groq: a quality run, a steady load, or a burst.

Every call goes through relay/llm_client.py and is written to budget_ledger.csv.
The script prints its request and token estimate and each model's use in the
last 24 hours, then asks for "go" on standard input before any call is made.

Machine: Aloo's Mac. Directory: the repo root. Examples:

  .venv/bin/python -m eval.run_real quality --id E5-A --model qwen/qwen3.8-27b \\
      --setting R1 --questions eval/questions_aloo.yaml
  .venv/bin/python -m eval.run_real load --id E6b --model openai/gpt-oss-20b \\
      --shape steady --n 10 --rate-per-min 1.5
  .venv/bin/python -m eval.run_real load --id E8 --model openai/gpt-oss-20b --shape burst --n 8
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.recall import load_questions  # noqa: E402
from library.ingest import SETTINGS  # noqa: E402
from load.loadgen import Scenario, run_scenario  # noqa: E402
from relay.config import load_config  # noqa: E402
from relay.decide import pipeline_kwargs_from_config  # noqa: E402
from relay.limiter import AdaptiveLimiter  # noqa: E402
from relay.llm_client import LLMClient, ModelEstimate, approve_real_experiment  # noqa: E402
from relay.pipeline import CitedAnswerPipeline  # noqa: E402
from relay.retrieval import PaperRetriever  # noqa: E402
from relay.retry import GlobalRetryBudget, RetryPolicy  # noqa: E402

LEDGER = ROOT / "budget_ledger.csv"
DAILY_GUARD = 170_000  # stop rule from the handover: no model above this in any 24 hours


def read_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8")) or {}


def tokens_last_24h(model: str) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    total = 0
    with LEDGER.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            when = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if row["model"] == model and when >= cutoff:
                total += int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0)
    return total


def estimate(setting, n: int, limits: dict[str, int], decide_mode: str, max_repairs: int
             ) -> tuple[ModelEstimate, int]:
    """(hard approval budget, realistic token estimate) for n answers.

    The approval budget is a hard ceiling: llm_client counts one token per byte
    of request, so the budget is sized in bytes. The realistic figure divides
    by 3.4 bytes per token (measured: a 982-token draft was about 3,300 bytes)
    and is what the 24-hour guard is checked against.
    """
    evidence_bytes = int(setting.top_k * setting.chunk_chars * 1.2) + 1500
    evidence_calls = 1 + max_repairs + (2 if decide_mode == "llm_judge" else 0)
    bytes_in = 900 + evidence_calls * (evidence_bytes + 4 * limits["draft"])
    out = limits["understand"] + (1 + max_repairs) * limits["draft"] + (
        2 * limits["judge"] if decide_mode == "llm_judge" else 0
    )
    hard = ModelEstimate(requests=n * (2 + 2 * evidence_calls) + 2,
                         input_tokens=n * bytes_in + 500, output_tokens=n * out + 16)
    # Realistic: one understand call, one draft, a repair on about 3 answers in 10.
    realistic = n * (int((900 + 1.3 * evidence_bytes) / 3.4) + 400)
    return hard, realistic


async def main_async(args: argparse.Namespace) -> None:
    setting = next(item for item in SETTINGS if item.name == args.setting)
    pipeline_config = read_yaml("pipeline.yaml")
    retrieval_config = read_yaml("retrieval.yaml")
    limiter_config = read_yaml("limiter.yaml")
    retry_config = read_yaml("retry.yaml")
    limits = {
        step: int(pipeline_config[f"{step}_max_completion_tokens"])
        for step in ("understand", "judge", "draft", "repair")
    }
    max_repairs = int(pipeline_config.get("max_repairs", 1))
    kwargs = pipeline_kwargs_from_config(pipeline_config)

    questions = load_questions(args.questions)
    if args.scored_only:
        raw = yaml.safe_load(args.questions.read_text(encoding="utf-8"))["questions"]
        scored = {str(item["id"]) for item in raw if item.get("scored")}
        questions = [q for q in questions if q.question_id in scored]
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q.question_id in wanted]
    if args.command == "load":
        rng = random.Random(args.seed)
        pool = [q.question for q in questions]
        n = args.n
    else:
        n = len(questions)
    if n == 0:
        raise SystemExit("no questions selected")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", args.model).strip("_")
    experiment = f"{args.id}-{args.command}-{setting.name}-{slug}-{stamp}"
    out_dir = ROOT / "results" / "raw" / "runs" / f"{args.id}-{stamp}"
    budget, realistic = estimate(setting, n, limits, kwargs["decide_mode"], max_repairs)
    used = tokens_last_24h(args.model)
    projected = used + realistic
    print(f"{experiment}\n  decide_mode={kwargs['decide_mode']}  setting={setting.name}  n={n}")
    print(f"  {args.model}: {used} tokens used in the last 24 h; estimate for this run {realistic} "
          f"(hard ceiling {budget.input_tokens + budget.output_tokens}); projected {projected} "
          f"of guard {DAILY_GUARD}")
    print("  stop rule: 3 consecutive 5xx/transport errors, any HTTP 400, or a ledger write failure")
    if projected > DAILY_GUARD:
        raise SystemExit("projected 24-hour use exceeds the guard; shrink the run")
    if args.dry_run:
        return

    load_dotenv(ROOT / ".env", override=False)
    runtime_config = load_config().model_copy(update={"provider": "groq"})
    _ = runtime_config.api_key  # fail early if the key is missing; never printed
    approval = await approve_real_experiment(experiment, {args.model: budget}, ledger_path=LEDGER)

    limiter = AdaptiveLimiter(
        additive_step=float(limiter_config.get("aimd_additive_step", 0.1)),
        multiplicative_decrease=float(limiter_config.get("aimd_multiplicative_decrease", 0.5)),
        freshness_seconds=float(limiter_config.get("snapshot_freshness_s", 60)),
        continuous_refill=bool(limiter_config.get("continuous_refill", False)),
    )
    client = LLMClient(
        runtime_config, ledger_path=LEDGER, limiter=limiter,
        reserve_bytes_per_token=float(limiter_config.get("reservation_bytes_per_token", 1)),
    )
    rerank = setting.rerank or args.rerank
    reranker = retrieval_config.get("reranker_model") if rerank else None
    retriever = PaperRetriever.from_jsonl(
        setting.name, top_k=setting.top_k, chunk_dir=ROOT / "library" / "chunks",
        reranker_model_name=str(reranker) if reranker else None,
    )
    if args.rerank and not setting.rerank:
        retriever.setting = f"{setting.name}b"  # e.g. R2b = R2's pieces, re-ranked (record 0011)
    label = retriever.setting
    out_dir.mkdir(parents=True, exist_ok=True)
    scenario_name = (
        f"E5-quality-{label}-{slug}" if args.command == "quality" else args.shape
    )
    results_path = out_dir / f"{scenario_name}.csv"
    (out_dir / "run.json").write_text(json.dumps(
        {"experiment": experiment, "model": args.model, "setting": setting.name,
         "pipeline_config": {k: v for k, v in pipeline_config.items()},
         "questions_file": args.questions.name, "n": n,
         "args": vars(args) | {"questions": args.questions.name}},
        indent=2, default=str) + "\n", encoding="utf-8")
    pipeline = CitedAnswerPipeline(
        client, retriever,
        model_by_step={step: args.model for step in ("understand", "judge", "draft", "repair")},
        approval=approval, max_repairs=max_repairs,
        understand_max_tokens=limits["understand"], judge_max_tokens=limits["judge"],
        draft_max_tokens=limits["draft"], repair_max_tokens=limits["repair"],
        retry_policy=RetryPolicy(
            max_attempts=int(retry_config.get("max_attempts", 3)),
            base_delay_s=float(retry_config.get("base_delay_s", 0.25)),
            max_delay_s=float(retry_config.get("max_delay_s", 4.0)),
        ),
        retry_budget=GlobalRetryBudget(int(retry_config.get("global_retry_limit", 20))),
        results_path=results_path, trace_dir=ROOT / "results" / "traces", **kwargs,
    )
    try:
        if args.command == "quality":
            loop = asyncio.get_running_loop()
            per_answer = realistic // max(1, n)
            failures = 0
            for question in questions:
                # Pace by tokens: start a question only when the per-minute bucket
                # is predicted to hold a typical answer.
                wait, _bucket = await limiter.predicted_wait(args.model, request_tokens=per_answer)
                if wait > 0:
                    await asyncio.sleep(min(wait, 60))
                result = await pipeline.run(
                    question.question,
                    request_id=f"{args.id}-{stamp}-{label}-{question.question_id}",
                    question_id=question.question_id, experiment=experiment,
                    deadline_monotonic=loop.time() + args.deadline_seconds,
                    scenario=scenario_name, rerank=rerank,
                )
                tokens = sum((s.tokens_in or 0) + (s.tokens_out or 0) for s in result.spans)
                print(f"{question.question_id}: {result.status}; {result.total_ms:.0f} ms; "
                      f"{tokens} tokens; outcome={dict(result.decide or {}).get('outcome')}")
                failures = failures + 1 if result.status == "error" else 0
                if failures >= 3:
                    print("stop rule: 3 consecutive errors; stopping without retrying the run")
                    break
        else:
            scenario = Scenario(
                arrival_mode="burst" if args.shape == "burst" else "poisson",
                max_requests=n,
                rate_per_minute=None if args.shape == "burst" else args.rate_per_min,
                duration_seconds=None if args.shape == "burst" else 3600,
                burst_spread_ms=0, patience_ms=8000, deadline_ms=8000,
                patience_median_ms=args.patience_median_ms, patience_sigma=0.5,
                patience_cap_ms=20000, zipf_exponent=1.1, cache=False,
            )
            summary = await run_scenario(
                pipeline, scenario_name, scenario, pool, seed=args.seed,
                run_id=f"{args.id}-{stamp}", results_path=results_path,
                trace_dir=ROOT / "results" / "traces", rerank=rerank,
                allow_real=True, warmup_experiment=experiment, experiment=experiment,
            )
            print(f"{summary.scenario}: {summary.request_count} requests, "
                  f"statuses={dict(summary.status_counts)}, gave_up={summary.gave_up_count}")
    finally:
        await client.aclose()
    print(f"results: {results_path.relative_to(ROOT)}; tokens now used in 24 h: {tokens_last_24h(args.model)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("quality", "load"))
    parser.add_argument("--id", required=True, help="experiment id, e.g. E5-A, E6b, E8")
    parser.add_argument("--model", required=True)
    parser.add_argument("--setting", default="R1", choices=[item.name for item in SETTINGS])
    parser.add_argument("--questions", type=Path, default=ROOT / "eval" / "questions.yaml")
    parser.add_argument("--ids", help="comma-separated question ids")
    parser.add_argument("--shape", choices=("steady", "burst"), default="steady")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--rate-per-min", type=float, default=1.5)
    parser.add_argument("--patience-median-ms", type=float, default=8000)
    parser.add_argument("--deadline-seconds", type=float, default=120)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--scored-only", action="store_true", help="only questions marked scored: true")
    parser.add_argument("--rerank", action="store_true", help="re-rank this setting's pieces locally")
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
