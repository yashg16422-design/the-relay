#!/usr/bin/env python3
"""Tables 2a, 2b and 3 computed from results/results.csv and nothing else.

Zero provider calls. No exclusion file, no grade file, no traces: every cell
below is a function of the twelve columns of results.csv. (Table 1 describes
the provider, not requests, so it is built by analysis/table1.py from the saved
probe files.) Run from the repo root:

    .venv/bin/python analysis/tables.py            # print
    .venv/bin/python analysis/tables.py --write    # also write results/generated_tables.md
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "results.csv"
PRICES = ROOT / "config" / "prices.json"
OUTPUT = ROOT / "results" / "generated_tables.md"
BEGIN, END = "<!-- BEGIN GENERATED TABLES -->", "<!-- END GENERATED TABLES -->"

# Row order and display names for Tables 2a and 2b. A scenario that has no rows is left out.
LOAD_ROWS = [
    ("steady", "real"), ("burst", "real"), ("steady_prefix", "real"),
    ("steady", "stand-in"), ("burst", "stand-in"), ("burst_cache_on", "stand-in"),
    ("sustained_overload", "stand-in"), ("per_minute_limit_halved", "stand-in"),
    ("daily_budget_exhausted", "stand-in"), ("retry_disciplined", "stand-in"),
    ("retry_storm", "stand-in"),
]
NAMES = {
    "steady_prefix": "steady, before the rework (n=5)", "burst_cache_on": "burst, answer cache on",
    "sustained_overload": "sustained overload", "per_minute_limit_halved": "per-minute limit halved",
    "daily_budget_exhausted": "daily budget exhausted",
    "retry_disciplined": "overload, retry policy", "retry_storm": "overload, naive retries",
}
# results.csv has no model column, so the price model is fixed per row type and stated in the notes.
LOAD_MODEL = "openai/gpt-oss-120b"
PREFIX_STEADY_MODEL = "openai/gpt-oss-20b"
SETTINGS = {
    "R1": "R1: 900 characters, 3 pieces, no re-rank",
    "R2": "R2: 1,600 characters, 4 pieces, no re-rank",
    "R2b": "R3 (redefined): 1,600 characters, 4 pieces, re-rank",
    "R3": "old R3: 2,600 characters, 6 pieces, re-rank",
}
MODEL_OF_SLUG = {"qwen_qwen3_8_27b": "qwen/qwen3.8-27b", "openai_gpt_oss_20b": "openai/gpt-oss-20b",
                 "openai_gpt_oss_120b": "openai/gpt-oss-120b"}


def read(path: Path = RESULTS) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def nearest_rank(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def spread(values: list[float], *, p99: bool) -> str:
    """p50 / p95 [/ p99] in ms with n; p99 only when n >= 100."""
    if not values:
        return "not measured (n=0)"
    cells = [f"{nearest_rank(values, 50):.1f}", f"{nearest_rank(values, 95):.1f}"]
    if p99:
        cells.append(f"{nearest_rank(values, 99):.1f}" if len(values) >= 100 else "not measured")
    return " / ".join(cells) + f" (n={len(values)})"


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "not measured"


def floats(rows: list[dict[str, str]], field: str) -> list[float]:
    return [float(row[field]) for row in rows if row[field] != ""]


def epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def peak_in_system(rows: list[dict[str, str]]) -> int:
    """Most requests in the system at once: sweep over [start_time, start_time + total_ms]."""
    events = []
    for row in rows:
        start = epoch(row["start_time"])
        events += [(start, 1), (start + float(row["total_ms"]) / 1000, -1)]
    level = peak = 0
    for _, change in sorted(events, key=lambda item: (item[0], item[1])):
        level += change
        peak = max(peak, level)
    return peak


def cost_per_1000(rows: list[dict[str, str]], model: str, prices: dict) -> str:
    """Published price for ALL tokens the scenario used, per 1,000 answered requests."""
    answered = [row for row in rows if row["status"] == "ok"]
    if not answered:
        return "not measured (no answers)"
    price = prices["models"][model]
    usd = sum(int(row["tokens_in"] or 0) * price["input_usd_per_million"]
              + int(row["tokens_out"] or 0) * price["output_usd_per_million"] for row in rows) / 1e6
    return f"${1000 * usd / len(answered):.2f}"


def tokens_per_answer(rows: list[dict[str, str]]) -> str:
    """All tokens the scenario used divided by the requests that were answered."""
    answered = sum(row["status"] == "ok" for row in rows)
    total = sum(int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0) for row in rows)
    return f"{total / answered:.1f}" if answered else "not measured (no answers)"


def render(rows: list[dict[str, str]]) -> str:
    prices = json.loads(PRICES.read_text(encoding="utf-8"))
    out = [BEGIN, "", "## Table 2a: Latency, one row per load scenario", "",
           "| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | "
           "First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |",
           "| --- | --- | ---: | ---: | ---: | --- | --- |"]
    table_2b = ["## Table 2b: Outcomes, same rows", "",
                "| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | "
                "Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for scenario, source in LOAD_ROWS:
        group = [row for row in rows if row["scenario"] == scenario and row["source"] == source]
        if not group:
            continue
        n = len(group)
        starts = [epoch(row["start_time"]) for row in group]
        ends = [epoch(row["start_time"]) + float(row["total_ms"]) / 1000 for row in group]
        span = max(starts) - min(starts)
        rate = f"{60 * n / span:.1f}" if span > 0 else "all at once"
        label = NAMES.get(scenario, scenario)
        out.append(f"| {label} | {source} | {n} | {max(ends) - min(starts):.1f} | {rate} | "
                   f"{spread(floats(group, 'first_token_ms'), p99=True)} | "
                   f"{spread(floats(group, 'total_ms'), p99=True)} |")
        count = lambda status: sum(row["status"] == status for row in group)  # noqa: E731
        model = PREFIX_STEADY_MODEL if scenario == "steady_prefix" else LOAD_MODEL
        table_2b.append(
            f"| {label} ({source}) | {pct(count('ok'), n)} | {pct(count('rate_limited'), n)} | "
            f"{pct(count('timeout'), n)} | {sum(int(row['retries'] or 0) for row in group) / n:.2f} | "
            f"{peak_in_system(group)} | {tokens_per_answer(group)} | {cost_per_1000(group, model, prices)} |")
    out += ["", *table_2b, "",
            "## Table 3: Retrieval settings", "",
            "| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | "
            "Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | "
            "Total time p50 / p95 (ms) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    notes_3 = []
    for prefix, note in (("E5-quality-", ""), ("E5-prefix-", " — before the rework")):
        for setting, description in SETTINGS.items():
            group = [row for row in rows if row["scenario"].startswith(f"{prefix}{setting}-")]
            if not group:
                continue
            graded = [row for row in group if row["answer_grade"]]
            grade = lambda value: sum(row["answer_grade"] == value for row in graded)  # noqa: E731
            g = len(graded)
            out.append(
                f"| {description}{note} | {len(group)} | {pct(grade('correct'), g)} | "
                f"{pct(grade('wrong'), g)} | {pct(grade('refused_correctly'), g)} | "
                f"{tokens_per_answer(group)} | {spread(floats(group, 'first_token_ms'), p99=False)} | "
                f"{spread(floats(group, 'total_ms'), p99=False)} |")
            models = sorted({MODEL_OF_SLUG.get(row["scenario"].split(f"{setting}-", 1)[1], "?")
                             for row in group})
            notes_3.append(
                f"- {description}{note}: graded {g} of {len(group)}; wrongly refused "
                f"{pct(grade('refused_wrongly'), g)}; not answered because of a limit or error "
                f"{sum(row['status'] != 'ok' for row in group)}; models: {', '.join(models)}.")
    out += ["", "### Notes under the tables", "",
            "- Every cell is computed from `results/results.csv` alone by `analysis/tables.py`. "
            "Percentiles are nearest-rank; n is printed beside them; p99 needs n ≥ 100.",
            "- **First token** is the first character of a checked answer reaching the person. It is "
            "empty when no answer was delivered, so its n can be smaller than Requests sent.",
            "- **Duration** runs from the first arrival to the last completion. **Offered rate** is "
            "requests divided by the time between the first and last arrival.",
            "- **Peak queue length** is the largest number of requests in the system at the same moment, "
            "found by sweeping over each row's `start_time` to `start_time + total_ms`. It counts "
            "requests being worked on as well as those waiting.",
            "- **Tokens per answer** is all tokens the scenario used (including work on requests that "
            "ended rate-limited) divided by the requests answered. **Cost** applies Groq's published "
            f"paid price ({prices['source_url']}, read {prices['price_date']}) to those tokens: "
            f"{LOAD_MODEL} for load rows, {PREFIX_STEADY_MODEL} for the pre-rework steady row. "
            "It is hypothetical: the free tier bills nothing, and stand-in tokens are simulated.",
            "- **Succeeded** is status `ok`: a checked answer or an honest \"the papers do not say\" was "
            "delivered. It is not the share of correct answers; real rows carry grades in `answer_grade`.",
            "- Rate-limited rows include requests that were sent retrieved passages instead of a model answer. "
            "Rows with status `error` are in Requests sent but in none of the three outcome columns.",
            "- Stand-in rows have no answer grade: stand-in replies are synthetic text.",
            "- Table 3 percentages are of graded answers. Wrong % counts only answers graded `wrong`.",
            *notes_3, "", END, ""]
    return "\n".join(out)


def main() -> None:
    # --preview reads the view that has not yet replaced results.csv (record 0007 pending).
    source = RESULTS.with_name("results_view_preview.csv") if "--preview" in sys.argv else RESULTS
    text = render(read(source))
    print(text)
    if "--write" in sys.argv:
        OUTPUT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
