"""Build results/results.csv as a labelled view of the full request log (decision record 0007).

Zero provider calls. The full log is never edited. This script keeps EVERY
row and changes only labels, so that the tables can be computed from
results.csv alone, with no side files:

  1. rows listed in results/excluded_runs.csv get scenario "<name>_excluded";
  2. stand-in load runs made before the 2026-11-12 rework get "<name>_superseded"
     (they measured the answer cache, not the pipeline);
  3. "E5-model-selection-<setting>-<model>" becomes "E5-prefix-<setting>-<model>"
     (quality runs made before the decide-step fix), and the five real "steady"
     requests of 2026-11-11 become "steady_prefix";
  4. the placeholder grade "not_graded" becomes empty (stand-in replies are synthetic);
  5. first_token_ms is emptied where no answer was delivered (status is not "ok").

Runs made after the rework each write their own file under results/raw/runs/
and pass through unchanged.

By default this writes a PREVIEW to results/results_view_preview.csv.
`--write` replaces results/results.csv and is only for use after Aloo accepts
decision record 0007. Run from the repo root:

    .venv/bin/python analysis/build_submission_csv.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL_LOG = ROOT / "results" / "raw" / "results_full_log_2026-11-12.csv"
FULL_LOG_SHA256 = "c492cf9e9f47fab33dd1f6a7a79527cbb9544443caf0f7f86f69bfe51e546674"
RUNS_DIR = ROOT / "results" / "raw" / "runs"  # one results file per post-rework run
EXCLUDED = ROOT / "results" / "excluded_runs.csv"
PREVIEW = ROOT / "results" / "results_view_preview.csv"
RESULTS = ROOT / "results" / "results.csv"
FIELDS = (
    "request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms",
    "status", "retries", "tokens_in", "tokens_out", "retrieval_setting", "answer_grade",
)
SUPERSEDED_STANDIN = frozenset(
    {"steady", "burst", "sustained_overload", "per_minute_limit_halved",
     "daily_budget_exhausted", "retry_disabled", "retry_made_worse"}
)
GRADES = {"correct", "wrong", "refused_correctly", "refused_wrongly"}
OLD_E5_PREFIX, NEW_E5_PREFIX = "E5-model-selection-", "E5-prefix-"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError(f"{path} does not have the results.csv columns")
        return list(reader)


def relabel(row: dict[str, str], excluded_ids: set[str]) -> dict[str, str]:
    """Return the view of one pre-rework row. Only labels change, never measurements."""
    view = dict(row)
    if row["request_id"] in excluded_ids:
        view["scenario"] = f"{row['scenario']}_excluded"
    elif row["source"] == "stand-in" and row["scenario"] in SUPERSEDED_STANDIN:
        view["scenario"] = f"{row['scenario']}_superseded"
    elif row["source"] == "real" and row["scenario"] == "steady":
        view["scenario"] = "steady_prefix"  # the five real steady requests of 2026-11-11
    elif row["scenario"].startswith(OLD_E5_PREFIX):
        view["scenario"] = NEW_E5_PREFIX + row["scenario"][len(OLD_E5_PREFIX):]
    if view["answer_grade"] == "not_graded":
        view["answer_grade"] = ""
    if view["status"] != "ok":
        view["first_token_ms"] = ""
    return view


def build() -> list[dict[str, str]]:
    actual = sha256(FULL_LOG)
    if actual != FULL_LOG_SHA256:
        raise ValueError(f"{FULL_LOG.name} changed: SHA-256 is {actual}")
    with EXCLUDED.open(newline="", encoding="utf-8") as stream:
        excluded_ids = {row["request_id"] for row in csv.DictReader(stream)}
    full = read_rows(FULL_LOG)
    unknown = excluded_ids - {row["request_id"] for row in full}
    if unknown:
        raise ValueError(f"excluded_runs.csv names unknown request ids: {sorted(unknown)[:3]}")
    rows = [relabel(row, excluded_ids) for row in full]
    for run_file in sorted(RUNS_DIR.glob("**/*.csv")):
        if run_file.name.endswith("_ledger.csv"):
            continue  # the stand-in's own call log, not request rows
        for row in read_rows(run_file):
            if row["answer_grade"] == "not_graded":  # stand-in replies are synthetic
                row["answer_grade"] = ""
            rows.append(row)
    # Grades for post-rework real runs live in eval/grades_*.csv (request_id, grade,
    # grader). They are copied into answer_grade here, so the tables need only results.csv.
    grades: dict[str, str] = {}
    for grade_file in sorted((ROOT / "eval").glob("grades_*.csv")):
        with grade_file.open(newline="", encoding="utf-8") as stream:
            for item in csv.DictReader(stream):
                if item["grade"] not in GRADES:
                    raise ValueError(f"{grade_file.name}: bad grade {item['grade']!r}")
                grades[item["request_id"]] = item["grade"]
    by_id = {row["request_id"]: row for row in rows}
    for request_id, grade in grades.items():
        if request_id not in by_id:
            raise ValueError(f"grade for unknown request {request_id}")
        by_id[request_id]["answer_grade"] = grade
    ids = Counter(row["request_id"] for row in rows)
    duplicates = [request_id for request_id, count in ids.items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate request ids in the view: {duplicates[:3]}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="replace results/results.csv (only after record 0007 is accepted)")
    args = parser.parse_args()
    rows = build()
    target = RESULTS if args.write else PREVIEW
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"full log: {FULL_LOG.relative_to(ROOT)} ({len(read_rows(FULL_LOG))} rows, SHA-256 verified)")
    print(f"wrote {len(rows)} rows to {target.relative_to(ROOT)}")
    for (scenario, source), count in sorted(Counter((r["scenario"], r["source"]) for r in rows).items()):
        print(f"  {count:4d}  {source:9s} {scenario}")
    grades = Counter(row["answer_grade"] or "(empty)" for row in rows)
    print(f"answer_grade values: {dict(grades)}")
    print(f"first_token_ms filled on non-ok rows: "
          f"{sum(bool(r['first_token_ms']) for r in rows if r['status'] != 'ok')}")


if __name__ == "__main__":
    main()
