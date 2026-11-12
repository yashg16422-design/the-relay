#!/usr/bin/env python3
"""Grade the saved E5 answers for the 15-question cohort against the accepted gold key.

Writes eval/human_grades.csv. Does not modify results/results.csv or results/raw/.
HTTP 429 rows are left ungraded. A substantive answer that is not in the reviewed
set stops the script instead of receiving a guessed grade.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from relay.pipeline import SAFE_REFUSAL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COHORT = {
    "Q02", "Q04", "Q06", "Q08", "Q10", "Q13", "Q16", "Q17",
    "Q19", "Q24", "Q25", "Q28", "Q31", "Q32", "Q34",
}
UNANSWERABLE = {"Q31", "Q32", "Q34"}
# Each of these delivered answers was read against eval/gold_answers.md on 2026-11-11.
REVIEWED_CORRECT = {
    "20261111T052722457416Z-R1-openai_gpt_oss_120b-Q02",
    "20261111T053257348423Z-R1-qwen_qwen3_8_27b-Q02",
    "20261111T082751736982Z-R3-openai_gpt_oss_120b-Q02",
    "20261111T083306686437Z-R3-openai_gpt_oss_20b-Q02",
    "20261111T053257348423Z-R1-qwen_qwen3_8_27b-Q04",
    "20261111T092716646228Z-R3-qwen_qwen3_8_27b-Q04",
    "20261111T094433797377Z-R3-qwen_qwen3_8_27b-Q06",
    "20261111T092819169988Z-R3-qwen_qwen3_8_27b-Q10",
    "20261111T084957434790Z-R3-qwen_qwen3_8_27b-Q19",
    "20261111T073959735447Z-R2-qwen_qwen3_8_27b-Q25",
    "20261111T094754428750Z-R3-qwen_qwen3_8_27b-Q25",
}
GRADER = "builder-assistant at Aloo's request on 2026-11-11; compared with eval/gold_answers.md"
FIELDS = ("request_id", "question_id", "answer_grade", "grader", "notes")


def grade_saved_answer(question_id: str, request_id: str, answer: str) -> tuple[str, str]:
    """Return (answer_grade, notes) for one successful saved answer."""
    if question_id not in COHORT:
        raise ValueError(f"{request_id} is outside the 15-question cohort: {question_id}")
    text = answer.strip()
    if text == SAFE_REFUSAL:
        if question_id in UNANSWERABLE:
            return (
                "refused_correctly",
                "exact safe refusal on an unanswerable gold question",
            )
        return (
            "refused_wrongly",
            "exact safe refusal on an answerable gold question",
        )
    if request_id in REVIEWED_CORRECT:
        return ("correct", "cited answer matches the accepted gold key")
    raise ValueError(
        f"Ungraded substantive answer {request_id}; read it against the gold key before writing a grade"
    )


def _answers_by_request(raw_dir: Path) -> dict[str, str]:
    answers: dict[str, str] = {}
    for path in raw_dir.rglob("traces/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        request_id = data.get("request_id")
        if isinstance(request_id, str) and request_id:
            answers[request_id] = data.get("answer") or ""
    return answers


def grade_rows(results_path: Path, raw_dir: Path) -> list[dict[str, str]]:
    answers = _answers_by_request(raw_dir)
    graded: list[dict[str, str]] = []
    with results_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if not row["scenario"].startswith("E5-") or row["status"] != "ok":
                continue
            match = re.search(r"-(Q\d+)$", row["request_id"])
            if match is None:
                raise ValueError(f"E5 request id has no question suffix: {row['request_id']}")
            question_id = match.group(1)
            if row["request_id"] not in answers:
                raise ValueError(f"No trace answer for {row['request_id']}")
            grade, notes = grade_saved_answer(question_id, row["request_id"], answers[row["request_id"]])
            graded.append(
                {
                    "request_id": row["request_id"],
                    "question_id": question_id,
                    "answer_grade": grade,
                    "grader": GRADER,
                    "notes": notes,
                }
            )
    return graded


def write_grades(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = grade_rows(
        PROJECT_ROOT / "results" / "results.csv",
        PROJECT_ROOT / "results" / "raw",
    )
    write_grades(PROJECT_ROOT / "eval" / "human_grades.csv", rows)
    print(f"wrote {len(rows)} grades to eval/human_grades.csv")


if __name__ == "__main__":
    main()
