# 0011 — Retrieval settings for the paid comparison, and two query fixes

- Status: accepted            <!-- only Aloo changes this to accepted / rejected -->
- Date: 2026-11-13
- Phase / gate: D

## Problem
The three old settings changed piece size, pieces fetched and re-ranking all at once (R3 was 2,600 characters, 6 pieces, re-rank), so no difference between them could be attributed to one cause, and R3 cost about 7,500 tokens per answer against a 200,000-token day.

## Options
1. Keep R1, R2, R3 as they were.
2. **R1 (900 characters, 3 pieces), R2 (1,600, 4), R3b (R2's pieces, 4 fetched, re-ranked locally by `cross-encoder/ms-marco-MiniLM-L-6-v2`).** R1 to R2 isolates piece size; R2 to R3b isolates re-ranking.
3. Add a semantic retriever. Not built: no measurement yet says BM25 with the fixes below is the bottleneck.

Independently of the settings: scope retrieval to the paper the question names, and strip question words from the BM25 query (`content_word_query: true`).

## Evidence
`results/naive_retriever_report.json`, 28 answerable legacy questions, BM25 only, share with every gold passage retrieved:

| Variant | R1 | R2 | R3 |
| --- | --- | --- | --- |
| naive BM25 | 67.9% | 60.7% | 89.3% |
| + paper scope | 75.0% | 64.3% | 89.3% |
| + content-word query | 75.0% | 67.9% | 92.9% |
| ...and drop damaged tables | 75.0% | 67.9% | 92.9% |
| ...and drop debris passages | 67.9% | 67.9% | 89.3% |

25 of 35 table chunks lost their decimal points in extraction ("4.92" became "492").

## Decision (builder's recommendation)
Option 2, with paper scope and the content-word query on. Dropping "debris" passages is rejected (it lowered recall). Dropping damaged tables is recall-neutral and is left off until Aloo decides: it removes a source of wrong numbers the gate cannot catch, at the cost of hiding 25 tables.

## What it costs
The legacy questions were proposed by the assistant, so these recall figures are a development measurement, not the scored one. R3b costs one local re-rank per question (time not yet measured on this Mac). The old R3 rows are not comparable with R3b.

## What would change our mind
Aloo's own questions showing a different ordering; re-rank time above about 300 ms per question.

## CTO review
2026-11-13, final submission review of the whole tree: `GO WITH CHANGES`, projected 68/100 as it stood and about 82 with its fixes. Builder-side fixes it asked for are applied (two prose figures corrected, M4 regraded, disclaimer rule tightened, dead limiter branch removed). Still open and Aloo's: this record's acceptance, the personal sections, blind grading, missing transcripts. (`docs/reflection_draft.md` was removed by Aloo's instruction on 2026-11-13.)

## Aloo's call
Accepted. Aloo, in the Claude Code session on 2026-11-13: "i accept 0006,8,11".
