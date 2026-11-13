#!/usr/bin/env python3
"""Small real-Groq probes: E0 parameters, E2 requests per minute, E3 tokens per minute, E4 in flight.

Each probe prints its estimate, asks for "go" on standard input, sends its calls
through relay/llm_client.py (so every call is in budget_ledger.csv), and saves
every status, header set and error body to results/raw/<probe>-<stamp>.json.

Machine: Aloo's Mac. Directory: the repo root.
  .venv/bin/python -m probe.real_probes e0 --model qwen/qwen3.8-27b
  .venv/bin/python -m probe.real_probes e2 --model openai/gpt-oss-20b
  .venv/bin/python -m probe.real_probes e3 --model openai/gpt-oss-20b
  .venv/bin/python -m probe.real_probes e4 --model openai/gpt-oss-120b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from relay.config import load_config  # noqa: E402
from relay.llm_client import (  # noqa: E402
    LLMClient, LLMRequestError, ModelEstimate, approve_real_experiment,
)
from relay.pipeline import DRAFT_SYSTEM, JUDGE_SYSTEM  # noqa: E402

LEDGER = ROOT / "budget_ledger.csv"
RATE_KEYS = ("retry-after", "x-ratelimit-limit-requests", "x-ratelimit-limit-tokens",
             "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
             "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")
PASSAGE = ("We trained our models on one machine with 8 NVIDIA P100 GPUs. For our base models "
           "each training step took about 0.4 seconds.")


class Probe:
    def __init__(self, client: LLMClient, approval: Any, experiment: str, model: str) -> None:
        self.client, self.approval, self.experiment, self.model = client, approval, experiment, model
        self.records: list[dict[str, Any]] = []
        self.started = time.perf_counter()

    async def call(self, label: str, messages: list[dict[str, str]], **options: Any) -> dict[str, Any]:
        sent = time.perf_counter()
        record: dict[str, Any] = {"label": label, "sent_s": round(sent - self.started, 3),
                                  "options": {k: v for k, v in options.items()}}
        try:
            response = await self.client.complete(
                messages, experiment=self.experiment, model=self.model,
                approval=self.approval, **options)
            record.update(status=200, text=response.text[:300], finish_reason=response.finish_reason,
                          tokens_in=response.usage.input_tokens, tokens_out=response.usage.output_tokens,
                          reasoning_tokens=response.usage.reasoning_tokens,
                          headers={k: v for k, v in response.headers.items() if k in RATE_KEYS})
        except LLMRequestError as exc:
            # The provider's error text names the account's organisation id. It is
            # replaced before anything is saved; nothing else in the body is changed.
            record.update(status=exc.status_code,
                          body=re.sub(r"org_[A-Za-z0-9]+", "org_REDACTED", exc.body[:2000]),
                          headers={k: v for k, v in exc.headers.items() if k in RATE_KEYS})
        except (TimeoutError, RuntimeError) as exc:
            record.update(status="transport", body=str(exc)[:300])
        record["ms"] = round((time.perf_counter() - sent) * 1000, 1)
        self.records.append(record)
        return record


TINY = [{"role": "user", "content": "Reply with the single word: ok"}]


async def e0(probe: Probe) -> None:
    evidence = json.dumps({"question": "How many GPUs was Moshi trained on?", "evidence": [
        {"chunk_id": "a74289279fd72be7", "paper": "Attention Is All You Need", "text": PASSAGE}]})
    await probe.call("temperature0", TINY, max_completion_tokens=64, temperature=0)
    await probe.call("reasoning_effort_low", TINY, max_completion_tokens=64, reasoning_effort="low")
    await probe.call("json_mode_judge", [{"role": "system", "content": JUDGE_SYSTEM},
                                         {"role": "user", "content": evidence}],
                     max_completion_tokens=512, response_format={"type": "json_object"})
    await probe.call("abstain_sentinel", [{"role": "system", "content": DRAFT_SYSTEM},
                                          {"role": "user", "content": "Write a concise answer to this question using the evidence.\n" + evidence}],
                     max_completion_tokens=700)
    await probe.call("forced_truncation", [{"role": "user", "content": "Write three sentences about attention."}],
                     max_completion_tokens=8)


async def e2(probe: Probe) -> None:
    """35 tiny calls back to back; on the first 429, probe every 2 s until one succeeds."""
    limited_at = None
    for index in range(35):
        record = await probe.call(f"burst_{index}", TINY, max_completion_tokens=16)
        if record["status"] == 429:
            limited_at = time.perf_counter()
            break
        if record["status"] != 200:
            return
    if limited_at is None:
        return
    for index in range(25):
        await asyncio.sleep(2)
        record = await probe.call(f"recovery_{index}", TINY, max_completion_tokens=16)
        if record["status"] == 200:
            probe.records.append({"label": "recovered",
                                  "seconds_from_first_429": round(time.perf_counter() - limited_at, 2)})
            return


async def e3(probe: Probe) -> None:
    """Four calls of about 3,000 input tokens back to back, to capture a tokens-per-minute 429."""
    filler = (PASSAGE + " ") * 95  # about 12,000 characters
    for index in range(4):
        record = await probe.call(f"large_{index}", [{"role": "user", "content": filler + "\nReply with the single word: ok"}],
                                  max_completion_tokens=16)
        if record["status"] != 200:
            return


async def e4(probe: Probe) -> None:
    """Five single calls for a baseline, then 10 at once, 70 s later 20 at once."""
    for index in range(5):
        await probe.call(f"single_{index}", TINY, max_completion_tokens=16)
    for size in (10, 20):
        await asyncio.sleep(70)
        await asyncio.gather(*(probe.call(f"batch{size}_{i}", TINY, max_completion_tokens=16)
                               for i in range(size)))


PROBES = {"e0": (e0, 5, 3000), "e2": (e2, 60, 6000), "e3": (e3, 4, 60000), "e4": (e4, 35, 20000)}


async def main_async(args: argparse.Namespace) -> None:
    run, requests, tokens = PROBES[args.probe]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    experiment = f"{args.probe.upper()}-{stamp}"
    load_dotenv(ROOT / ".env", override=False)
    config = load_config().model_copy(update={"provider": "groq"})
    _ = config.api_key
    approval = await approve_real_experiment(
        experiment, {args.model: ModelEstimate(requests=requests, input_tokens=tokens, output_tokens=requests * 700)},
        ledger_path=LEDGER)
    client = LLMClient(config, ledger_path=LEDGER)  # no local limiter: the point is to see the provider's
    probe = Probe(client, approval, experiment, args.model)
    try:
        await run(probe)
    finally:
        await client.aclose()
        out = ROOT / "results" / "raw" / f"{experiment}-{args.model.replace('/', '_')}.json"
        out.write_text(json.dumps({"experiment": experiment, "model": args.model,
                                   "records": probe.records}, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
    for record in probe.records:
        print({k: record.get(k) for k in ("label", "status", "ms", "finish_reason", "tokens_out",
                                          "reasoning_tokens", "text", "body", "seconds_from_first_429")
               if record.get(k) is not None})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("probe", choices=sorted(PROBES))
    parser.add_argument("--model", required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
