# 0009 — Load shapes that exercise the pipeline, not the cache

- Status: proposed            <!-- only Aloo changes this to accepted / rejected -->
- Date: 2026-11-13
- Phase / gate: D

## Problem
Every old stand-in load row measured the answer cache: 4 questions, a 60-second cache, and 4 of 75 "sustained overload" requests reaching the model. No request ever timed out or gave up, because the deadlines could not bind.

## Options
1. Keep the old runs and explain them.
2. **New scenarios in `load/scenarios.yaml`** (implemented; old file kept as `load/scenarios.legacy-2026-11-11.yaml`): cache off for the main rows and one burst row with it on; a pool of the 35 legacy questions plus Aloo's; rates sized from the calibrated stand-in's measured capacity; each request's deadline is its own patience draw; two extra rows comparing the retry policy with naive retries on the same arrivals.

## Evidence
- Old runs: 4 of 11, 4 of 40 and 4 of 75 rows had `tokens_in > 0` (CTO review, recomputed from `results.csv`).
- Capacity: 70 full requests on the calibrated stand-in at R1 used 1,335 tokens on average, so 8,000 tokens per minute sustain about 6.0 answers per minute. Requests per minute (30) does not bind.

## Decision (builder's recommendation)
Option 2, with rates of 60% (steady), 300% (sustained overload) and 80% (limit halved) of that capacity.

## What it costs
A full batch takes about 16 minutes of wall-clock time. **User patience is an assumption, not a measurement:** lognormal, median 8 s, capped at 20 s. Every give-up number depends on it. The stand-in's capacity (6.0 per minute) is not the real service's: real answers used more tokens.

## What would change our mind
Real traffic data on how long people wait. A real tokens-per-answer figure far from 1,335 would mean re-sizing the rates.

## CTO review
2026-11-13, final submission review of the whole tree: `GO WITH CHANGES`, projected 68/100 as it stood and about 82 with its fixes. Builder-side fixes it asked for are applied (two prose figures corrected, M4 regraded, disclaimer rule tightened, dead limiter branch removed). Still open and Aloo's: this record's acceptance, the personal sections, blind grading, missing transcripts, `docs/reflection_draft.md`.

## Aloo's call
