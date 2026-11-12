#!/usr/bin/env python3
"""Guarded model discovery and tiny-response candidate probes for Phase 0."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from relay.config import AppConfig, load_config
from relay.llm_client import (
    MODEL_LIST_MODEL,
    ExperimentApproval,
    LLMClient,
    LLMRequestError,
    ModelEstimate,
    approve_real_experiment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "results" / "raw"
LEDGER = PROJECT_ROOT / "budget_ledger.csv"
TINY_MESSAGES = [{"role": "user", "content": "Reply with exactly: OK"}]
PROBE_MAX_COMPLETION_TOKENS = 64


def _real_config() -> AppConfig:
    raw = load_config().model_dump()
    raw["provider"] = "groq"
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid Groq configuration: {exc}") from exc


def _raw_path(prefix: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix)
    return RAW_DIR / f"{timestamp}-{safe_prefix}.json"


def _save_raw(prefix: str, data: dict[str, Any]) -> Path:
    path = _raw_path(prefix)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return path


def _print_headers(headers: dict[str, str]) -> None:
    print("Response headers:")
    for name, value in sorted(headers.items()):
        print(f"  {name}: {value}")


async def _approve_and_list(client: LLMClient) -> list[dict[str, Any]]:
    approval = await approve_real_experiment(
        "phase0-model-discovery",
        {MODEL_LIST_MODEL: ModelEstimate(requests=1, input_tokens=0, output_tokens=0)},
        ledger_path=LEDGER,
    )
    try:
        models, headers = await client.list_models(
            experiment="phase0-model-discovery", approval=approval
        )
    except LLMRequestError as exc:
        _print_headers(dict(exc.headers))
        print(f"HTTP {exc.status_code} body:\n{exc.body}")
        path = _save_raw(
            "phase0-model-discovery-error",
            {"status_code": exc.status_code, "headers": dict(exc.headers), "body": exc.body},
        )
        print(f"Saved raw response: {path}")
        raise

    _print_headers(dict(headers))
    path = _save_raw(
        "phase0-model-discovery",
        {"status": "ok", "headers": dict(headers), "data": models},
    )
    print(f"Saved raw model list: {path}")
    print("Available models:")
    for item in models:
        print(f"  {item['id']}")
    return [dict(item) for item in models]


async def _probe_model(client: LLMClient, model: str) -> None:
    input_bound = LLMClient.input_token_upper_bound(
        TINY_MESSAGES,
        model=model,
        stream=False,
        max_completion_tokens=PROBE_MAX_COMPLETION_TOKENS,
    )
    experiment = f"phase0-tiny-response-{model}"
    approval = await approve_real_experiment(
        experiment,
        {
            model: ModelEstimate(
                requests=1,
                input_tokens=input_bound,
                output_tokens=PROBE_MAX_COMPLETION_TOKENS,
            )
        },
        ledger_path=LEDGER,
    )
    try:
        result = await client.complete(
            TINY_MESSAGES,
            experiment=experiment,
            model=model,
            approval=approval,
            max_completion_tokens=PROBE_MAX_COMPLETION_TOKENS,
        )
    except LLMRequestError as exc:
        _print_headers(dict(exc.headers))
        print(f"HTTP {exc.status_code} body:\n{exc.body}")
        path = _save_raw(
            f"phase0-{model}-error",
            {"status_code": exc.status_code, "headers": dict(exc.headers), "body": exc.body},
        )
        print(f"Saved raw response: {path}")
        raise

    _print_headers(dict(result.headers))
    print(f"Usage: {result.usage}")
    print(f"Response: {result.text}")
    path = _save_raw(
        f"phase0-{model}",
        {
            "status_code": result.status_code,
            "headers": dict(result.headers),
            "usage": result.usage.__dict__,
            "text": result.text,
            "total_ms": result.total_ms,
        },
    )
    print(f"Saved raw response: {path}")


async def run(args: argparse.Namespace) -> None:
    config = _real_config()
    async with LLMClient(config, ledger_path=LEDGER) as client:
        models = await _approve_and_list(client)
        available = {str(item["id"]) for item in models}
        requested = list(dict.fromkeys(args.model or []))
        if not requested:
            print("No candidate models supplied; discovery only. No tiny model probes ran.")
            return
        missing = [name for name in requested if name not in available]
        if missing:
            raise ValueError(f"These candidate models were not listed by /models: {missing}")
        for model in requested:
            await _probe_model(client, model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        help="candidate model ID to probe (repeat once per candidate); discovery always runs first",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
