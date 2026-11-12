#!/usr/bin/env python3
"""Validate results.csv and diff the generated results tables against WRITEUP.md."""

from __future__ import annotations

import argparse
from pathlib import Path

from analysis.make_tables import (
    GENERATED_BEGIN,
    GENERATED_END,
    render_tables,
    read_results,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def extract_generated_block(text: str) -> str:
    if text.count(GENERATED_BEGIN) != 1 or text.count(GENERATED_END) != 1:
        raise ValueError("WRITEUP.md must contain exactly one generated-results marker pair")
    start = text.index(GENERATED_BEGIN)
    end = text.index(GENERATED_END) + len(GENERATED_END)
    if end < start:
        raise ValueError("Generated-results markers are out of order")
    return text[start:end] + "\n"


def first_difference(expected: str, actual: str) -> str:
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    max_lines = max(len(expected_lines), len(actual_lines))
    for index in range(max_lines):
        expected_line = expected_lines[index] if index < len(expected_lines) else "<missing>"
        actual_line = actual_lines[index] if index < len(actual_lines) else "<missing>"
        if expected_line != actual_line:
            return (
                f"First difference at generated-block line {index + 1}:\n"
                f"  expected: {expected_line}\n"
                f"  actual:   {actual_line}"
            )
    return "No difference"


def check_writeup(
    results_path: str | Path,
    writeup_path: str | Path,
    *,
    ledger_path: str | Path | None = None,
    trace_dir: str | Path | None = None,
    raw_dir: str | Path | None = None,
    prices_path: str | Path | None = None,
    settings_path: str | Path | None = None,
    excluded_runs_path: str | Path | None = None,
) -> tuple[bool, str]:
    read_results(results_path)
    expected = render_tables(
        results_path,
        ledger_path=ledger_path,
        trace_dir=trace_dir,
        raw_dir=raw_dir,
        prices_path=prices_path,
        settings_path=settings_path,
        excluded_runs_path=excluded_runs_path,
    )
    path = Path(writeup_path)
    if not path.exists():
        return False, f"WRITEUP.md not found: {path}"
    try:
        actual = extract_generated_block(path.read_text(encoding="utf-8")).rstrip("\n")
    except ValueError as exc:
        return False, str(exc)
    if actual == expected.rstrip("\n"):
        return True, "Generated tables match WRITEUP.md."
    return False, first_difference(expected.rstrip("\n"), actual)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default=str(PROJECT_ROOT / "results" / "results.csv"))
    parser.add_argument("--writeup", default=str(PROJECT_ROOT / "WRITEUP.md"))
    parser.add_argument("--ledger", default=str(PROJECT_ROOT / "budget_ledger.csv"))
    parser.add_argument("--traces", default=str(PROJECT_ROOT / "results" / "traces"))
    parser.add_argument("--raw", default=str(PROJECT_ROOT / "results" / "raw"))
    parser.add_argument("--prices", default=str(PROJECT_ROOT / "config" / "prices.json"))
    parser.add_argument("--settings", default=str(PROJECT_ROOT / "config" / "retrieval_settings.yaml"))
    parser.add_argument("--excluded-runs", default=str(PROJECT_ROOT / "results" / "excluded_runs.csv"))
    args = parser.parse_args()
    okay, message = check_writeup(
        args.results,
        args.writeup,
        ledger_path=args.ledger,
        trace_dir=args.traces,
        raw_dir=args.raw,
        prices_path=args.prices,
        settings_path=args.settings,
        excluded_runs_path=args.excluded_runs,
    )
    print(message)
    raise SystemExit(0 if okay else 1)


if __name__ == "__main__":
    main()
