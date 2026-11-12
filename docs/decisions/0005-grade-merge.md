# 0005 — Copy the 134 human grades into results.csv

- Status: accepted
- Date: 2026-11-11
- Phase / gate: D

## Problem
The brief says every table figure must be recomputable from `results/results.csv`. The 134 E5 grades lived only in `eval/human_grades.csv`, so a reviewer reading the results file alone would see blank `answer_grade` cells and Table 3 quality would not match the write-up.

## Options
1. Leave the cells blank and keep joining the grade file at table time.
2. Copy the 134 grades into the matching successful E5 `answer_grade` cells, change nothing else, and keep the grade file as the source record.
3. Re-grade or drop the rate-limited attempts.

## Evidence
`eval/human_grades.csv` has 134 unique request ids: 11 `correct`, 27 `refused_correctly`, 96 `refused_wrongly`. Each id matched one successful real E5 row whose `answer_grade` was blank. The seven E5 rows with status `rate_limited` are not in the grade file. The pre-merge file is `results/results.pre-grade-merge-20261111.csv`, SHA-256 `05a5be6568e77ada746d55ca69062f8ed00d3c577c4a6b18e9b0bedf0336c2a0`.

## Decision (builder's recommendation)
Option 2. This is a one-time exception to the append-only rule for the `answer_grade` field only. `eval/human_grades.csv` stays the grading source. Published tables read `answer_grade` from `results.csv` and do not join the grade file.

## What it costs
No new provider calls. The results file is no longer a pure append of the original rows: 134 previously blank cells now hold a grade. Every other field, the seven rate-limited attempts, the E6 rows, and the stand-in rows stay as they were. `results/raw/` was not edited.

## What would change our mind
A mismatch between a grade-file request id and a successful E5 row, or a nonblank grade already in the target cell. Either one would have stopped the merge.

## CTO review
Pending review of the finished diff. Follow-up review on 2026-11-11: `GO`, no blocking issues. That review does not by itself accept this record.

## Aloo's call
Accepted by Aloo on 2026-11-11: "I authorize the remaining work described in CURSOR_COMPLETION_PROMPT.md, including the one-time merge of the 134 human grades into only the matching successful E5 answer_grade cells in results/results.csv."
