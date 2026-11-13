#!/usr/bin/env python3
"""Where the time went for one slow request: waiting, working, retrying.

Zero provider calls. Selection rule (stated, not hand-picked): among the rows
of one scenario and source in results/results.csv, take the nearest-rank p95
by total_ms; if that row has no waiting or retrying span, take the slowest
answered row that has one. Then read that request's trace and add up its spans
by kind. Run from the repo root:

    .venv/bin/python analysis/slow_request.py sustained_overload stand-in
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def trace_of(request_id: str) -> dict:
    return json.loads((ROOT / "results" / "traces" / f"{request_id}.json").read_text(encoding="utf-8"))


def kinds(trace: dict) -> dict[str, float]:
    total = {"wait": 0.0, "work": 0.0, "retry": 0.0}
    for span in trace["spans"]:
        total[span["kind"]] += span["end_ms"] - span["start_ms"]
    return total


def main(scenario: str, source: str) -> None:
    name = "results_view_preview.csv" if "--preview" in sys.argv else "results.csv"
    with (ROOT / "results" / name).open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream)
                if row["scenario"] == scenario and row["source"] == source]
    ordered = sorted(rows, key=lambda row: float(row["total_ms"]))
    chosen = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
    rule = f"nearest-rank p95 by total_ms of {len(rows)} rows"
    by_kind = kinds(trace_of(chosen["request_id"]))
    if by_kind["wait"] + by_kind["retry"] < 1 or chosen["status"] != "ok":
        answered = [row for row in reversed(ordered) if row["status"] == "ok"
                    and sum(v for k, v in kinds(trace_of(row["request_id"])).items() if k != "work") >= 1]
        if answered:
            chosen = answered[0]
            rule += "; that row had no wait or retry (or no answer), so: the slowest answered row that has one"
    trace = trace_of(chosen["request_id"])
    by_kind = kinds(trace)
    total = float(chosen["total_ms"])
    print(f"scenario: {scenario} ({source}); selection rule: {rule}")
    print(f"request: {chosen['request_id']}; status {chosen['status']}; level {trace['level']}; "
          f"total {total:.1f} ms; first token {chosen['first_token_ms'] or 'none'} ms\n")
    print("| Span | Kind | Start (ms) | End (ms) | Duration (ms) | Note |")
    print("| --- | --- | ---: | ---: | ---: | --- |")
    for span in trace["spans"]:
        note = []
        if span.get("ttft_ms") is not None:
            note.append(f"provider's first chunk at {span['start_ms'] + span['ttft_ms']:.1f} ms")
        if span.get("status") not in (None, "ok"):
            note.append(str(span["status"]))
        if span["name"] == "sentence_gate" and span.get("raw_text") and span.get("status") == "rejected":
            note.append(span["raw_text"])
        print(f"| {span['name']} | {span['kind']} | {span['start_ms']:.1f} | {span['end_ms']:.1f} | "
              f"{span['end_ms'] - span['start_ms']:.1f} | {'; '.join(note)} |")
    other = total - sum(by_kind.values())
    print(f"\nwaiting {by_kind['wait']:.1f} ms ({100 * by_kind['wait'] / total:.1f}%), "
          f"working {by_kind['work']:.1f} ms ({100 * by_kind['work'] / total:.1f}%), "
          f"retrying {by_kind['retry']:.1f} ms ({100 * by_kind['retry'] / total:.1f}%), "
          f"unaccounted {other:.1f} ms")
    drafts = [span for span in trace["spans"] if span["name"] in ("draft", "repair") and span.get("ttft_ms") is not None]
    if drafts and chosen["first_token_ms"]:
        provider_first = drafts[0]["start_ms"] + drafts[0]["ttft_ms"]
        user_first = float(chosen["first_token_ms"])
        print(f"provider's first draft chunk: {provider_first:.1f} ms; first token the person saw: "
              f"{user_first:.1f} ms; held back by the gate: {user_first - provider_first:.1f} ms")
        print("All of the waiting and retrying came before the first token the person saw."
              if user_first >= total - 1 else "")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
