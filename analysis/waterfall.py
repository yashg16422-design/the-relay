#!/usr/bin/env python3
"""Explain one request's wait/work/retry time from its persisted trace."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Mapping

from analysis.make_tables import SCENARIO_ALIASES, _read_json, nearest_rank, read_results


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_trace(trace_dir: Path, request_id: str) -> dict[str, Any]:
    for path in sorted(trace_dir.glob("*.json")):
        data = _read_json(path)
        if isinstance(data, dict) and data.get("request_id") == request_id:
            data["_path"] = str(path)
            return data
    raise FileNotFoundError(f"No trace found for request_id={request_id!r} in {trace_dir}")


def _select_request(
    rows: list[dict[str, str]], request_id: str | None, scenario: str | None
) -> tuple[dict[str, str], str]:
    if request_id:
        matches = [row for row in rows if row["request_id"] == request_id]
        if not matches:
            raise ValueError(f"request_id not found in results.csv: {request_id}")
        return matches[0], "explicit request_id"
    if not scenario:
        raise ValueError("Provide --request-id or --scenario")
    normalized_scenario = SCENARIO_ALIASES.get(scenario.casefold(), scenario)
    candidates = [
        row
        for row in rows
        if SCENARIO_ALIASES.get(row["scenario"].casefold(), row["scenario"]) == normalized_scenario
        and _number(row.get("total_ms")) is not None
    ]
    if not candidates:
        raise ValueError(f"No requests with measured total_ms in scenario {scenario!r}")
    p95 = nearest_rank([_number(row["total_ms"]) for row in candidates], 0.95)
    selected = next(row for row in candidates if _number(row["total_ms"]) == p95)
    return selected, f"nearest-rank p95 total_ms within scenario {scenario!r} (n={len(candidates)})"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _span_rows(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    spans = trace.get("spans", [])
    if not isinstance(spans, list):
        return []
    rows = []
    for span in spans:
        if not isinstance(span, Mapping):
            continue
        start = _number(span.get("start_ms"))
        end = _number(span.get("end_ms"))
        if start is None or end is None or end < start:
            continue
        kind = str(span.get("kind", "unknown"))
        if kind not in {"wait", "work", "retry"}:
            raise ValueError(f"Invalid trace span kind {kind!r}; expected wait/work/retry")
        rows.append(
            {
                "name": str(span.get("name", "unnamed")),
                "kind": kind,
                "start": start,
                "end": end,
                "duration": end - start,
            }
        )
    rows.sort(key=lambda row: (row["start"], row["end"], row["name"]))
    for previous, current in zip(rows, rows[1:]):
        if current["start"] < previous["end"]:
            raise ValueError(
                "Overlapping trace spans would double-count latency: "
                f"{previous['name']} [{previous['start']:.1f}, {previous['end']:.1f}] and "
                f"{current['name']} [{current['start']:.1f}, {current['end']:.1f}]"
            )
    return rows


def render_waterfall(
    results_path: str | Path,
    trace_dir: str | Path,
    *,
    request_id: str | None = None,
    scenario: str | None = None,
) -> str:
    rows = read_results(results_path)
    selected, rule = _select_request(rows, request_id, scenario)
    trace = _load_trace(Path(trace_dir), selected["request_id"])
    spans = _span_rows(trace)
    if not spans:
        raise ValueError(f"Trace has no valid spans: {trace.get('_path', 'unknown file')}")

    # results.csv defines this as the first answer character delivered after
    # the sentence gate, so it is authoritative over any provider-local TTFT.
    first_token = _number(selected.get("first_token_ms"))
    if first_token is None:
        first_token = _number(trace.get("first_token_ms"))
    by_kind: dict[str, float] = {"wait": 0.0, "work": 0.0, "retry": 0.0}
    before = 0.0
    after = 0.0
    for span in spans:
        kind = span["kind"]
        by_kind[kind] = by_kind.get(kind, 0.0) + span["duration"]
        if first_token is not None:
            before += max(0.0, min(span["end"], first_token) - span["start"])
            after += max(0.0, span["end"] - max(span["start"], first_token))

    first_display = f"{first_token:.1f} ms" if first_token is not None else "not measured"
    split = (
        f"Before first token: {before:.1f} ms; after first token: {after:.1f} ms."
        if first_token is not None
        else "Before/after-first-token split: not measured (no first-token timestamp)."
    )
    lines = [
        f"Selected request: `{selected['request_id']}` (scenario `{selected['scenario']}`).",
        f"Selection rule: {rule}.",
        f"Trace source: `{trace.get('_path', 'unknown')}`.",
        f"First answer character after the gate: {first_display}.",
        f"Wait: {by_kind.get('wait', 0.0):.1f} ms; work: {by_kind.get('work', 0.0):.1f} ms; retry: {by_kind.get('retry', 0.0):.1f} ms.",
        split,
        "",
        "| Span | Kind | Start (ms) | End (ms) | Duration (ms) | Relative to first token |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for span in spans:
        if first_token is None:
            relative = "unknown"
        elif span["end"] <= first_token:
            relative = "before"
        elif span["start"] >= first_token:
            relative = "after"
        else:
            relative = "crosses first token"
        lines.append(
            f"| {span['name']} | {span['kind']} | {span['start']:.1f} | "
            f"{span['end']:.1f} | {span['duration']:.1f} | {relative} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default=str(PROJECT_ROOT / "results" / "results.csv"))
    parser.add_argument("--traces", default=str(PROJECT_ROOT / "results" / "traces"))
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--request-id")
    selector.add_argument("--scenario")
    args = parser.parse_args()
    print(
        render_waterfall(
            args.results,
            args.traces,
            request_id=args.request_id,
            scenario=args.scenario,
        ),
        end="",
    )


if __name__ == "__main__":
    main()
