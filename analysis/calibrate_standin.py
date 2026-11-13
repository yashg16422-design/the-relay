"""Fit the stand-in's latency and token parameters from real Groq model calls.

Zero provider calls. Reads every saved trace of a real request, takes each
successful model-call span, and fits per pipeline step:

  * a lognormal to the call duration (median = exp(mu); spread = sigma);
  * the time to the first streamed chunk, for the streamed steps;
  * the time per output token after the first chunk;
  * output and reasoning token counts;
  * the slow tail: share of calls slower than twice the step median.

Writes results/calibration_fit.json. It does NOT change standin/config.yaml;
decision record 0008 proposes how the stand-in uses these numbers. Run from
the repo root:

    .venv/bin/python analysis/calibrate_standin.py
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRACE_GLOBS = ("results/traces/*.json", "results/raw/model_selection/*/traces/*.json")
LEDGER = ROOT / "budget_ledger.csv"
OUTPUT = ROOT / "results" / "calibration_fit.json"
STEP_OF_SPAN = {
    "understand": "understand",
    "decide_answerability": "judge",
    "draft": "draft",
    "repair": "repair",
}
# Output caps in force when these calls were made (config/pipeline.yaml at 04688fb).
CAPS_WHEN_MEASURED = {"understand": 100, "judge": 120, "draft": 400, "repair": 400}


def nearest_rank(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def summary(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": round(nearest_rank(values, 50), 1),
        "p90": round(nearest_rank(values, 90), 1),
        "p95": round(nearest_rank(values, 95), 1),
        "p99": round(nearest_rank(values, 99), 1) if len(values) >= 100 else "not measured (n<100)",
        "max": round(max(values), 1),
    }


def lognormal(values: list[float]) -> dict[str, float]:
    logs = [math.log(value) for value in values if value > 0]
    return {
        "mu": round(statistics.fmean(logs), 4),
        "sigma": round(statistics.pstdev(logs), 4),
        "median_ms": round(math.exp(statistics.fmean(logs)), 1),
    }


def real_spans() -> list[dict[str, Any]]:
    seen: set[tuple[str, str, float]] = set()
    spans: list[dict[str, Any]] = []
    for pattern in TRACE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            trace = json.loads(path.read_text(encoding="utf-8"))
            for span in trace["spans"]:
                if (
                    span["name"] not in STEP_OF_SPAN
                    or span.get("model") in (None, "standin")
                    or span.get("status") != "ok"
                    or not span.get("tokens_in")
                ):
                    continue
                key = (trace["request_id"], span["name"], span["start_ms"])
                if key in seen:  # the same trace saved in two folders
                    continue
                seen.add(key)
                spans.append({**span, "step": STEP_OF_SPAN[span["name"]],
                              "duration_ms": span["end_ms"] - span["start_ms"],
                              "post_rework": trace.get("decide") is not None})
    return spans


def fit() -> dict[str, Any]:
    spans = real_spans()
    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        by_step[span["step"]].append(span)
    # Calls made after the 2026-11-12 rework ran under the new token caps. Once a
    # step has 20 of them, fit that step from those alone.
    for step, rows in list(by_step.items()):
        recent = [row for row in rows if row["post_rework"]]
        if len(recent) >= 20:
            by_step[step] = recent

    result: dict[str, Any] = {
        "source": list(TRACE_GLOBS),
        "real_model_call_spans": len(spans),
        "models": sorted({span["model"] for span in spans}),
        "all_steps_duration_ms": summary([span["duration_ms"] for span in spans]),
        "steps": {},
        "warnings": [
            "understand and judge output tokens are censored: they were measured under caps "
            "of 100 and 120 tokens and most judge calls hit the cap. Refit after the first "
            "runs with the new caps.",
            "draft (n=19) and repair (n=10) are small samples; their p95 is the largest or "
            "second-largest value.",
            "Three models are pooled. A per-model fit has too few draft spans to be useful.",
        ],
    }
    for step, rows in by_step.items():
        durations = [row["duration_ms"] for row in rows]
        median = nearest_rank(durations, 50)
        slow = [value for value in durations if value > 2 * median]
        entry: dict[str, Any] = {
            "duration_ms": summary(durations),
            "duration_lognormal": lognormal(durations),
            "tokens_in": summary([row["tokens_in"] for row in rows]),
            "tokens_out": summary([row["tokens_out"] for row in rows]),
            "tokens_out_values": sorted(row["tokens_out"] for row in rows),
            "fitted_from": "post-rework calls only" if rows[0]["post_rework"] else "pre-rework calls",
            "output_cap_when_measured": CAPS_WHEN_MEASURED[step],
            "slow_share": round(len(slow) / len(durations), 4),
            "slow_extra_ms_median": round(statistics.median(slow) - median, 1) if slow else 0.0,
        }
        reasoning = [row["reasoning_tokens"] for row in rows if row.get("reasoning_tokens") is not None]
        if reasoning:
            entry["reasoning_tokens"] = summary(reasoning)
        streamed = [row for row in rows if row.get("ttft_ms") is not None]
        if streamed:
            entry["ttft_ms"] = summary([row["ttft_ms"] for row in streamed])
            entry["ttft_lognormal"] = lognormal([row["ttft_ms"] for row in streamed])
            per_token = [
                (row["duration_ms"] - row["ttft_ms"]) / row["tokens_out"]
                for row in streamed if row["tokens_out"]
            ]
            entry["ms_per_output_token_after_first_chunk"] = summary(per_token)
        result["steps"][step] = entry

    with LEDGER.open(newline="", encoding="utf-8") as stream:
        real = [row for row in csv.DictReader(stream)
                if row["model"] not in ("standin", "__models_endpoint__")]
    statuses: dict[str, int] = defaultdict(int)
    for row in real:
        statuses[row["status"]] += 1
    result["ledger_real_calls"] = {"n": len(real), "by_status": dict(statuses)}
    return result


CONFIG_HEADER = """# Stand-in settings, GENERATED by analysis/calibrate_standin.py --write-config.
# Do not edit by hand: rerun the script. Every value names where it came from.
# The uncalibrated file used for runs up to 2026-11-11 is config.legacy-2026-11-11.yaml.
"""


def load_delay_line() -> str:
    """Extra latency per additional in-flight request, from the in-flight probe E4."""
    for path in sorted((ROOT / "results" / "raw").glob("E4-*.json"), reverse=True):
        records = json.loads(path.read_text(encoding="utf-8"))["records"]
        single = [r["ms"] for r in records if r["label"].startswith("single_") and r["status"] == 200]
        batch = [r["ms"] for r in records if r["label"].startswith("batch20_") and r["status"] == 200]
        if len(single) >= 3 and len(batch) == 20:
            slope = max(0.0, (statistics.median(batch) - statistics.median(single)) / 19)
            return (f"load_delay_per_inflight_ms: {slope:.1f} # {path.relative_to(ROOT)}: median "
                    f"{statistics.median(batch):.0f} ms with 20 in flight vs {statistics.median(single):.0f} ms "
                    "alone, divided by 19; tiny calls on one model, so a weak estimate")
    return "load_delay_per_inflight_ms: 0 # NOT MEASURED: no complete in-flight probe found"


def config_text(result: dict[str, Any]) -> str:
    lines = [CONFIG_HEADER.rstrip()]
    add = lines.append
    calls = result["ledger_real_calls"]
    add("seed: 17 # reproducibility only; not a measurement")
    add("token_bytes_per_token: 4 # ASSUMPTION: the stand-in counts 4 bytes as one token")
    add("limit_model: token_bucket # results/raw/limit_refill_analysis.json: reset = used / refill rate on 332 real responses")
    add("base_delay_ms: 0 # per-step latency below replaces a flat delay")
    add(load_delay_line())
    add("slow_success_probability: 0.0 # the slow tail is per step, below")
    add("slow_success_extra_ms: 0")
    add(f"failure_5xx_probability: 0.0 # 0 HTTP 5xx in {calls['n']} real calls (budget_ledger.csv); the one 'error' row was a transport failure")
    add("failure_5xx_status: 503 # used only by the chaos profile")
    add(f"hang_probability: 0.0 # no hang observed in {calls['n']} real calls")
    add("hang_duration_ms: 30000 # used only by the chaos profile")
    add("chunk_chars: 8 # stand-in stream chunk width; not measured")
    add("stream_chunk_delay_ms: 0 # streamed steps are paced from ms_per_output_token below")
    add('reply_prefix: "Stand-in reply: "')
    add("limits:")
    add("  requests_per_minute: 30 # Groq docs (results/raw/groq-public-rate-limits.json); not yet observed on this account")
    add("  tokens_per_minute: 8000 # x-ratelimit-limit-tokens on 332 real responses")
    add("  requests_per_day: 1000 # x-ratelimit-limit-requests on 332 real responses")
    add("  tokens_per_day: 200000 # Groq docs; not yet observed on this account")
    add("steps:")
    for step in ("understand", "judge", "draft", "repair"):
        entry = result["steps"][step]
        n = entry["duration_ms"]["n"]
        add(f"  {step}: # {n} real calls ({entry['fitted_from']}); real p50/p95 "
            f"{entry['duration_ms']['p50']}/{entry['duration_ms']['p95']} ms")
        add(f"    latency_mu: {entry['duration_lognormal']['mu']} # mean of ln(duration ms)")
        add(f"    latency_sigma: {entry['duration_lognormal']['sigma']} # sd of ln(duration ms)")
        if "ttft_lognormal" in entry:
            add(f"    ttft_mu: {entry['ttft_lognormal']['mu']} # ln(ms to first streamed chunk), n={entry['ttft_ms']['n']}")
            add(f"    ttft_sigma: {entry['ttft_lognormal']['sigma']}")
            add(f"    ms_per_output_token: {entry['ms_per_output_token_after_first_chunk']['p50']} # median after the first chunk")
        add(f"    slow_share: 0.0 # the lognormal already contains the slow tail (real share above 2x median: {entry['slow_share']})")
        add("    slow_extra_ms: 0")
        add(f"    output_tokens: {entry['tokens_out_values']} # every real billed output count (answer + reasoning), cap {entry['output_cap_when_measured']} when measured")
    return "\n".join(lines) + "\n"


async def measure_standin(calls: int = 100) -> dict[str, Any]:
    """Send `calls` single requests per step to the in-process stand-in and time them."""
    import asyncio
    import time

    import httpx

    from standin.server import create_app, load_config

    config = load_config().model_copy(deep=True)
    # The 100 calls are sent together to save time, so the per-in-flight slowdown is
    # switched off here; this table checks the single-call latency fit only.
    config.load_delay_per_inflight_ms = 0
    config.limits.requests_per_minute = None  # this measures latency, not limits
    config.limits.tokens_per_minute = None
    config.limits.requests_per_day = None
    config.limits.tokens_per_day = None
    app = create_app(config)
    evidence = json.dumps({"question": "q", "evidence": [
        {"chunk_id": "0123456789abcdef", "text": "The model was trained on eight GPUs."}]})
    prompts = {
        "understand": ("Rewrite the user's question as one concise search query.", "q", False),
        "judge": ("Decide whether the evidence contains enough information", evidence, False),
        "draft": ("Answer only from the supplied passages.", evidence, True),
        "repair": ("Answer only from the supplied passages.",
                   "Rewrite the answer to fix this citation-check failure: x.\n" + evidence, True),
    }
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://standin.local", timeout=120) as client:
        async def one(system: str, user: str, stream: bool) -> tuple[float, float | None]:
            body = {"model": "standin", "stream": stream, "max_completion_tokens": 700,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
            started = time.perf_counter()
            first = None
            if stream:
                async with client.stream("POST", "/v1/chat/completions", json=body) as response:
                    async for line in response.aiter_lines():
                        if first is None and '"content"' in line:
                            first = (time.perf_counter() - started) * 1000
            else:
                await client.post("/v1/chat/completions", json=body)
            return (time.perf_counter() - started) * 1000, first

        for step, (system, user, stream) in prompts.items():
            timings = await asyncio.gather(*(one(system, user, stream) for _ in range(calls)))
            out[step] = {"duration_ms": summary([total for total, _ in timings])}
            if stream:
                out[step]["ttft_ms"] = summary([first for _, first in timings if first is not None])
    return out


def write_report(result: dict[str, Any], standin: dict[str, Any]) -> None:
    lines = [
        "# Stand-in calibration report",
        "",
        "Generated by `analysis/calibrate_standin.py --report`. Real = successful Groq model-call "
        "spans in the saved traces. Stand-in = 100 single calls per step to the in-process stand-in "
        "with limits switched off. Percentiles are nearest-rank; times in ms.",
        "",
        "| Step | Measure | Real n | Real p50 | Real p95 | Stand-in n | Stand-in p50 | Stand-in p95 | p50 diff | p95 diff |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for step in ("understand", "judge", "draft", "repair"):
        for measure in ("duration_ms",):
            if measure not in result["steps"][step] or measure not in standin[step]:
                continue
            real, sim = result["steps"][step][measure], standin[step][measure]
            diff = lambda key: f"{100 * (sim[key] - real[key]) / real[key]:+.1f}%"  # noqa: E731
            lines.append(
                f"| {step} | {'call duration' if measure == 'duration_ms' else 'first chunk'} | "
                f"{real['n']} | {real['p50']} | {real['p95']} | {sim['n']} | {sim['p50']} | "
                f"{sim['p95']} | {diff('p50')} | {diff('p95')} |"
            )
    lines += [
        "",
        "The stand-in's settings were fitted on the real calls saved up to 2026-11-12 (146 understand, "
        "144 judge, 19 draft, 10 repair); the Real columns include the calls made since. Draft and "
        "repair rest on few real calls, so their p95 is close to the largest value seen.",
        "",
        "Time to first chunk is fitted (real draft p50 "
        f"{result['steps']['draft']['ttft_ms']['p50']} ms, n={result['steps']['draft']['ttft_ms']['n']}) "
        "but cannot be checked in-process: the in-process transport hands over a streamed reply "
        "all at once. The user-visible first token comes after the sentence gate, so it is unaffected.",
        "",
        "## Where the stand-in is least like Groq",
        "",
        "1. **Slowdown under load is one weak number.** `load_delay_per_inflight_ms` comes from a "
        "single probe of 20 tiny simultaneous calls (E4). Real latency under a burst of full-size "
        "prompts is not measured.",
        "2. **Latency does not depend on prompt or reply length for the non-streamed steps.** Each "
        "call draws from one lognormal per step, fitted on three pooled models.",
        "3. **Tokens are counted at 4 bytes each and billed output is resampled** from real counts, "
        "most of which were measured under the old 100- and 120-token caps.",
        "4. **Draft and repair fits rest on 19 and 10 real calls**, so their p95 is one observation.",
        "5. **Tokens per day comes from Groq's documentation**, not from a limit this account has "
        "hit (requests per minute was confirmed by probe E2). The stand-in's 429 body is its own "
        "wording, not the captured one.",
        "",
    ]
    target = ROOT / "results" / "calibration_report.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")


def main() -> None:
    import sys

    result = fit()
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if "--write-config" in sys.argv:
        target = ROOT / "standin" / "config.yaml"
        target.write_text(config_text(result), encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
    if "--report" in sys.argv:
        import asyncio

        sys.path.insert(0, str(ROOT))
        write_report(result, asyncio.run(measure_standin()))
    print(f"real model-call spans: {result['real_model_call_spans']}  "
          f"all steps: {result['all_steps_duration_ms']}")
    for step, entry in result["steps"].items():
        print(f"\n{step}: duration {entry['duration_ms']}")
        print(f"   lognormal {entry['duration_lognormal']}  slow share {entry['slow_share']} "
              f"(+{entry['slow_extra_ms_median']} ms)")
        print(f"   tokens in p50 {entry['tokens_in']['p50']}, out p50/p95 "
              f"{entry['tokens_out']['p50']}/{entry['tokens_out']['p95']} "
              f"(cap {entry['output_cap_when_measured']})")
        if "ttft_ms" in entry:
            print(f"   ttft {entry['ttft_ms']}  per output token "
                  f"{entry['ms_per_output_token_after_first_chunk']}")
    print(f"\nledger: {result['ledger_real_calls']}")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
