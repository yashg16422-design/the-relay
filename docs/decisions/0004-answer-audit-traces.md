# 0004 — Persist answer audit data in request traces

- Status: accepted
- Date: 2026-11-10
- Phase / gate: B (part 2)

## Problem

The required results CSV stores timing, token totals, and an answer-grade field, but not the question or answer. The current trace also omits both. Once model calls are run, a grader cannot reconstruct which answer was judged or inspect its citations from the result row alone. This weakens reproducibility for the required question-quality results.

## Options

1. Keep the current trace schema and rely on the operator's terminal output. This is smallest, but results cannot be independently audited after the run.
2. Add the question text, final answer, optional question ID, and retrieved chunk IDs to the append-only per-request trace. Keep `results.csv` unchanged and join grades by `request_id`.
3. Write a second answer-output file and keep traces timing-only. This separates content from metrics but introduces another artifact and join path.

## Evidence

- The assignment requires answer-quality grades for at least 15 questions and says reviewers should recompute table values from `results.csv`.
- `results/results.csv` has only the required header today; no request rows exist yet.
- `eval/questions.yaml` and `eval/human_grades.csv` are intentionally empty until Aloo supplies the final questions and grades.
- `relay/pipeline.py` already has the question and final answer in memory when `_persist()` writes a trace, but `RequestTrace` currently stores neither. It already has retrieved chunk IDs in `PipelineResult`.
- Proposed verification: run three stand-in questions, then inspect traces to confirm the exact question, final answer, question ID when provided, and retrieved chunk IDs are present and join to the CSV by `request_id`; confirm that the CSV header stays unchanged and that trace files remain create-only.

## Decision (builder's recommendation)

Recommend option 2. Store those fields in the trace so an answer and its evidence can be reviewed after the run without changing the assignment's CSV contract. Keep grades in the existing human-grades file and let Aloo make the quality judgments.

## What it costs

Trace files grow and retain the question and answer text. This assignment uses a small research-paper question set; a deployed system with private prompts would need an explicit retention and redaction policy. Chunk IDs are useful only while the versioned chunk corpus is preserved.

## What would change our mind

Use option 1 if all evaluation is performed live and reviewers do not need to audit responses later. Use option 3 only if a downstream tool requires answer files separate from trace metadata.

## CTO review

`GO` — the record keeps `results.csv` unchanged, compares three viable storage options, identifies the trace-retention trade-off, and includes a stand-in verification plan. The question and answer fields support the assignment's human-graded quality results.

## Aloo's call

Accepted by Aloo on 2026-11-10: “accept 0004 bro i approve”. The accepted scope is to add question text, final answer, optional question ID, and retrieved chunk IDs to per-request traces while preserving the results CSV schema.

## Implementation and verification

- Added the fields to normal pipeline, extractive, cache-hit, coalesced, timeout, expired-admission, and queue-shed trace paths. `results.csv` field order is unchanged.
- Three stand-in smoke requests wrote traces with the supplied question ID, exact question, answer, and retrieved chunk IDs. Their results and traces were written under `/private/tmp/relay-audit-smoke`; they did not alter the append-only project results.
- The smoke query asking which GPU served Moshi received a stand-in answer, so this smoke check verifies trace persistence only, not answer quality.
- Regression coverage checks request-specific audit fields on cache/coalesced requests, timeout, expired admission, and queue shedding. Full suite: 144 passed. Focused trace/pipeline/loadgen tests: 23 passed. Compileall and diff-check passed.
- Final CTO code-review verdict: `GO`, with no findings.
