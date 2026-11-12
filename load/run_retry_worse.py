#!/usr/bin/env python3
"""Stand-in comparison: one retry versus no retry against a fixed 503.

The stand-in returns HTTP 503 on every call. The retrying request waits out
one backoff and then fails again. The twin has retries disabled and fails on
the first 503. Both rows are appended to results.csv. This does not call Groq.
"""

from __future__ import annotations

import asyncio
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from relay.config import AppConfig, GroqConfig, ProviderConfig
from relay.llm_client import LLMClient
from relay.pipeline import CitedAnswerPipeline
from relay.retry import GlobalRetryBudget, RetryPolicy
from standin.server import RateLimits, StandinConfig, create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT_ROOT / "results" / "results.csv"
TRACES = PROJECT_ROOT / "results" / "traces"
LEDGER = PROJECT_ROOT / "results" / "retry_worse_ledger.csv"
SEED = 20261111
BACKOFF_S = 0.35
QUESTION = "How does the Transformer handle sequence processing?"
RUNS = (
    ("Rtry20261111-retry_made_worse-00000", "retry_made_worse", True),
    ("Rtry20261111-retry_disabled-00000", "retry_disabled", False),
)


class _SettingOnly:
    setting = "R1"


def _existing_ids() -> set[str]:
    with RESULTS.open(newline="", encoding="utf-8") as stream:
        return {row["request_id"] for row in csv.DictReader(stream)}


async def _run_one(request_id: str, scenario: str, retry: bool) -> None:
    app = create_app(
        StandinConfig(
            seed=SEED,
            token_bytes_per_token=4,
            base_delay_ms=0,
            load_delay_per_inflight_ms=0,
            slow_success_probability=0,
            slow_success_extra_ms=0,
            failure_5xx_probability=1,
            failure_5xx_status=503,
            hang_probability=0,
            hang_duration_ms=0,
            chunk_chars=8,
            stream_chunk_delay_ms=0,
            reply_prefix="Stand-in reply: ",
            limits=RateLimits(
                requests_per_minute=1000,
                tokens_per_minute=100000,
                requests_per_day=1000,
                tokens_per_day=1000000,
            ),
        )
    )
    config = AppConfig(
        provider="standin",
        groq=GroqConfig(
            base_url="https://api.groq.com/openai/v1",
            api_key_env="GROQ_API_KEY",
            model=None,
        ),
        standin=ProviderConfig(base_url="http://standin.local/v1"),
    )
    async with LLMClient(
        config,
        ledger_path=LEDGER,
        transport=httpx.ASGITransport(app=app),
    ) as client:
        policy = None
        budget = None
        if retry:
            policy = RetryPolicy(
                max_attempts=2,
                base_delay_s=BACKOFF_S,
                max_delay_s=BACKOFF_S,
                random_value=lambda: 1.0,
            )
            budget = GlobalRetryBudget(limit=1)
        pipeline = CitedAnswerPipeline(
            client,
            _SettingOnly(),  # type: ignore[arg-type]
            results_path=RESULTS,
            trace_dir=TRACES,
            retry_policy=policy,
            retry_budget=budget,
        )
        await pipeline.run(
            QUESTION,
            request_id=request_id,
            experiment="standin-retry-worse",
            deadline_monotonic=time.monotonic() + 5,
            scenario=scenario,
            scheduled_arrival=datetime.now(timezone.utc),
        )


def main() -> None:
    present = _existing_ids()
    collision = [request_id for request_id, _, _ in RUNS if request_id in present]
    if collision:
        raise SystemExit(f"refusing to append duplicate request ids: {collision}")
    for request_id, scenario, retry in RUNS:
        asyncio.run(_run_one(request_id, scenario, retry))
    with RESULTS.open(newline="", encoding="utf-8") as stream:
        rows = {row["request_id"]: row for row in csv.DictReader(stream)}
    for request_id, scenario, retry in RUNS:
        row = rows[request_id]
        print(
            request_id,
            "scenario", scenario,
            "status", row["status"],
            "retries", row["retries"],
            "total_ms", row["total_ms"],
            "grade", row["answer_grade"],
        )


if __name__ == "__main__":
    main()
