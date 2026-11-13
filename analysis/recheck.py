#!/usr/bin/env python3
"""Independent check: recompute the table cells with pandas and compare with WRITEUP.md.

Zero provider calls. This does not import analysis/tables.py. It reads
results/results.csv with pandas, recomputes each number its own way, and
confirms that the number printed in WRITEUP.md's tables is the same. It also
checks the results.csv contract (columns and allowed values). Exit code 1 on
any mismatch. Run from the repo root:

    .venv/bin/python analysis/recheck.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms",
           "status", "retries", "tokens_in", "tokens_out", "retrieval_setting", "answer_grade"]
LABELS = {  # table label -> (scenario, source)
    "steady": "steady", "burst": "burst", "steady, before the rework (n=5)": "steady_prefix",
    "burst, answer cache on": "burst_cache_on", "sustained overload": "sustained_overload",
    "per-minute limit halved": "per_minute_limit_halved",
    "daily budget exhausted": "daily_budget_exhausted",
    "overload, retry policy": "retry_disciplined", "overload, naive retries": "retry_storm",
}


def rank(values: pd.Series, percent: float) -> float:
    ordered = np.sort(values.dropna().to_numpy())
    return float(ordered[int(np.ceil(len(ordered) * percent / 100)) - 1])


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def main() -> int:
    name = "results_view_preview.csv" if "--preview" in sys.argv else "results.csv"
    frame = pd.read_csv(ROOT / "results" / name, dtype={"answer_grade": "string"})
    problems: list[str] = []
    if list(frame.columns) != COLUMNS:
        problems.append(f"results.csv columns are {list(frame.columns)}")
    if not frame["request_id"].is_unique:
        problems.append("request_id is not unique")
    for column, allowed in (("source", {"real", "stand-in"}),
                            ("status", {"ok", "rate_limited", "timeout", "error"})):
        extra = set(frame[column].unique()) - allowed
        if extra:
            problems.append(f"{column} has values outside the brief: {extra}")
    grades = set(frame["answer_grade"].dropna().unique()) - {
        "correct", "wrong", "refused_correctly", "refused_wrongly"}
    if grades:
        problems.append(f"answer_grade has values outside the brief: {grades}")
    if frame.loc[frame["status"] != "ok", "first_token_ms"].notna().any():
        problems.append("a row with no delivered answer has a first_token_ms")

    text = (ROOT / "WRITEUP.md").read_text(encoding="utf-8")
    checked = 0
    section = ""
    for line in text.splitlines():
        if line.startswith("## Table"):
            section = line
        if not line.startswith("| ") or line.startswith("| ---") or "Scenario" in line or "Setting (" in line:
            continue
        row = cells(line)
        if "Table 2a" in section:
            group = frame[(frame["scenario"] == LABELS[row[0]]) & (frame["source"] == row[1])]
            expected = [str(len(group))]
            for column in ("first_token_ms", "total_ms"):
                values = group[column].dropna()
                if values.empty:
                    expected.append("not measured (n=0)")
                    continue
                p99 = f"{rank(values, 99):.1f}" if len(values) >= 100 else "not measured"
                expected.append(f"{rank(values, 50):.1f} / {rank(values, 95):.1f} / {p99} (n={len(values)})")
            actual = [row[2], row[5], row[6]]
        elif "Table 2b" in section:
            label, source = re.fullmatch(r"(.*) \((real|stand-in)\)", row[0]).groups()
            group = frame[(frame["scenario"] == LABELS[label]) & (frame["source"] == source)]
            share = lambda status: f"{100 * (group['status'] == status).mean():.1f}%"  # noqa: E731
            answered = int((group["status"] == "ok").sum())
            tokens = group["tokens_in"].fillna(0).sum() + group["tokens_out"].fillna(0).sum()
            expected = [share("ok"), share("rate_limited"), share("timeout"),
                        f"{group['retries'].fillna(0).mean():.2f}",
                        f"{tokens / answered:.1f}" if answered else "not measured (no answers)"]
            actual = [row[1], row[2], row[3], row[4], row[6]]
        elif "Table 3" in section:
            setting = re.match(r"(?:old )?(R\d)(?: \(redefined\))?", row[0])
            name = {"R3 (redefined)": "R2b"}.get(row[0].split(":")[0], row[0].split(":")[0].replace("old ", ""))
            prefix = "E5-prefix-" if "before the rework" in row[0] else "E5-quality-"
            group = frame[frame["scenario"].str.startswith(f"{prefix}{name}-")]
            graded = group[group["answer_grade"].notna()]
            share = lambda grade: (f"{100 * (graded['answer_grade'] == grade).mean():.1f}%"  # noqa: E731
                                   if len(graded) else "not measured")
            expected = [str(len(group)), share("correct"), share("wrong"), share("refused_correctly")]
            actual = row[1:5]
            del setting
        else:
            continue
        checked += len(expected)
        for want, got in zip(expected, actual):
            if want != got:
                problems.append(f"{section} / {row[0]}: write-up says {got!r}, results.csv gives {want!r}")
    for problem in problems:
        print("MISMATCH:", problem)
    print(f"{checked} table cells recomputed from results.csv; {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
