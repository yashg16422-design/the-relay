#!/usr/bin/env python3
"""Append the selected E5 model-selection attempts to canonical results.csv.

Review sheets define the selected successful answer for each model/question/
setting. Raw CSVs supply its canonical row and any recorded rate-limit attempts
for that same selected question. Raw evidence is read-only and never modified.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from analysis.make_tables import PROJECT_ROOT, RESULT_FIELDS, read_results


REVIEW_GLOB = "model_selection_R*_day*_review.csv"
REQUEST_KEY = re.compile(r"-R(?P<setting>[123])-(?P<model>.+)-(?P<question>Q\d+)$")


def import_selected(
    results_path: Path,
    review_dir: Path,
    raw_dir: Path,
) -> tuple[int, int]:
    selected_ids: set[str] = set()
    selected_questions: set[tuple[str, str]] = set()
    for review_path in sorted(review_dir.glob(REVIEW_GLOB)):
        with review_path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                request_id = row.get("request_id", "").strip()
                match = REQUEST_KEY.search(request_id)
                if not match:
                    raise ValueError(f"Cannot parse selected request ID in {review_path}: {request_id}")
                selected_ids.add(request_id)
                selected_questions.add((match["setting"], match["question"]))

    existing = read_results(results_path)
    existing_ids = {row["request_id"] for row in existing}
    appended: list[dict[str, str]] = []
    for raw_path in sorted(raw_dir.glob("*/results.csv")):
        with raw_path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != RESULT_FIELDS:
                raise ValueError(f"Unexpected results.csv header in {raw_path}")
            for row in reader:
                request_id = row["request_id"]
                match = REQUEST_KEY.search(request_id)
                if not match:
                    continue
                key = (match["setting"], match["question"])
                is_selected_success = request_id in selected_ids
                is_selected_rate_limit = key in selected_questions and row["status"] == "rate_limited"
                if not (is_selected_success or is_selected_rate_limit):
                    continue
                if request_id in existing_ids:
                    continue
                appended.append({field: row[field] for field in RESULT_FIELDS})
                existing_ids.add(request_id)

    if not appended:
        return 0, len(existing)
    with results_path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writerows(appended)
    read_results(results_path)
    return len(appended), len(existing) + len(appended)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=PROJECT_ROOT / "results/results.csv")
    parser.add_argument("--reviews", type=Path, default=PROJECT_ROOT / "eval")
    parser.add_argument("--raw", type=Path, default=PROJECT_ROOT / "results/raw/model_selection")
    args = parser.parse_args()
    added, total = import_selected(args.results, args.reviews, args.raw)
    print(f"Appended {added} E5 rows; canonical results.csv now has {total} rows.")


if __name__ == "__main__":
    main()
