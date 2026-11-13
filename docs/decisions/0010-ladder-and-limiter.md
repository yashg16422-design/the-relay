# 0010 — Degradation ladder and a limiter that models refill

- Status: accepted            <!-- only Aloo changes this to accepted / rejected -->
- Date: 2026-11-13
- Phase / gate: D

## Problem
When a limit is close the system must decide, per request, who waits, who gets a weaker answer and who is told no. The old code compared the deadline with fixed guesses and waited for a full window reset. Two real runs showed what that costs: in E6b two of ten requests at 1.5 per minute were refused, and in the real burst E8 none of eight was answered.

## Options
1. First come, first served, and let the provider's 429s decide.
2. **A ladder decided before any token is spent** (implemented in `load/loadgen.py::choose_level`): full (with one repair), lite (no repair), passages only (no model call, with a notice), or an immediate no. A level fits when predicted wait plus that level's measured p95 is within the request's remaining patience. Tokens promised to requests already running count against new arrivals. A request that fits only after a short refill is held before it starts.
3. Option 2 plus failing over to another model. Rejected: that is dodging a per-model limit.

## Evidence
- Limiter defects found by the real burst E8 (2026-11-12 19:03 UTC, `results/raw/runs/E8-20261112T190347Z/`): it could not parse Groq's `667ms` reset values, so it predicted no wait; it reserved one token per byte of request, about 4 times the real count (E3: 2,929 tokens for about 12,000 bytes; a draft: 982 tokens for about 3,300 bytes), so it refused drafts the provider would have taken; four of seven gpt-oss-120b understand calls spent their whole 300-token cap on reasoning.
- Groq admits on input tokens plus the output cap (E3: "Requested 2945" for 2,929 input tokens and a cap of 16), so a larger cap uses up headroom even when it is not used.
- Ladder inputs measured on the calibrated stand-in: full p95 3.65 s and 2,267 tokens, lite p95 2.94 s and 1,367 tokens, passages 3.2 ms.

## Decision (builder's recommendation)
Option 2, with the limiter modelling continuous refill (`continuous_refill: true`), reservations at 3 bytes per token, `reasoning_effort: low` for the gpt-oss models, and the understand cap at 200.

## What it costs
People with little patience get passages instead of an answer even when a slightly longer wait would have produced one. Promised tokens use the p95, so the system under-uses the bucket by design. The passage fallback counts as rate-limited (or timeout), not as a success. Reserving at 3 bytes per token can under-reserve for text that tokenises unusually densely; the provider's own 429 is then the backstop.

## What would change our mind
Rerun of the real burst still answering fewer than a bucket's worth (about 3 of 8); real per-level p95 far from the stand-in's.

## CTO review
2026-11-13, final submission review of the whole tree: `GO WITH CHANGES`, projected 68/100 as it stood and about 82 with its fixes. Builder-side fixes it asked for are applied (two prose figures corrected, M4 regraded, disclaimer rule tightened, dead limiter branch removed). Still open and Aloo's: this record's acceptance, the personal sections, blind grading, missing transcripts. (`docs/reflection_draft.md` was removed by Aloo's instruction on 2026-11-13.)

## Aloo's call
Accepted. Aloo, in the Claude Code session on 2026-11-13: "yeah those 2 records are acceptable too".
