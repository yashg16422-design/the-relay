# 0007 — Make results.csv a labelled view of the full request log

- Status: accepted            <!-- only Aloo changes this to accepted / rejected -->
- Date: 2026-11-12
- Phase / gate: D
- **This record overrides CLAUDE.md hard rule 4 (results.csv is append-only). It needs Aloo's explicit acceptance before `--write` is run.**

## Problem
The brief says reviewers recompute the tables from `results.csv`, "so they must match". Today they do not, unless the reviewer also knows about two side files:

- The 44 rows named in `results/excluded_runs.csv` are still in `results.csv` under the same scenario names as the kept runs. From the file alone, the limit-halved scenario is 10.4% rate-limited and the daily-exhausted scenario 47.5%; the write-up says 20.8% and 95.0%.
- 216 rows carry `not_graded`, which is not one of the brief's four grade values.
- All 33 rows whose status is not `ok` have a `first_token_ms`, although no answer was delivered (CLAUDE.md §4 says the cell must be empty).
- Every stand-in load row measured the answer cache (4 of 75 "sustained overload" requests reached the model) and will be superseded by new runs. If the old rows keep the names `steady`, `burst` and so on, old and new rows will be mixed.

## Options
1. Leave the file as it is and explain the side files in the write-up. Fails "must match".
2. **A view that keeps every row and changes only labels** (implemented as a preview): `analysis/build_submission_csv.py`.
3. Keep in `results.csv` only the rows the tables use, and move everything else to `results/raw/` (the CTO reviewer's suggestion). Simplest file for a reviewer; but rows disappear from the one file the brief names, and "we kept only the good runs" is harder to defend than "every run is here, labelled".

## Evidence
- `results/results.csv` at commit 04688fb: 362 rows, SHA-256 `c492cf9e9f47fab33dd1f6a7a79527cbb9544443caf0f7f86f69bfe51e546674`.
- A byte-for-byte copy was made on 2026-11-12 at `results/raw/results_full_log_2026-11-12.csv` (same SHA-256, write permission removed). The builder refuses to run if that hash changes.
- Preview output `results/results_view_preview.csv`: 362 rows; 0 `not_graded`; 0 non-ok rows with a first token; the 134 grades unchanged (11 correct, 96 refused_wrongly, 27 refused_correctly).
- `tests/test_build_submission_csv.py` checks that every row of the full log is in the view and that `source`, `start_time`, `total_ms`, `status`, `retries`, `tokens_in`, `tokens_out` and `retrieval_setting` are identical.

## Decision (builder's recommendation)
Option 2. The rules, applied only to rows from before the rework:

1. A row listed in `excluded_runs.csv` gets scenario `<name>_excluded`.
2. A stand-in row from `steady`, `burst`, `sustained_overload`, `per_minute_limit_halved`, `daily_budget_exhausted`, `retry_disabled` or `retry_made_worse` gets `<name>_superseded`.
3. `E5-model-selection-<setting>-<model>` becomes `E5-prefix-<setting>-<model>`.
4. `not_graded` becomes empty.
5. `first_token_ms` is emptied where status is not `ok`.

New runs append to `results/run_log.csv` and pass through unchanged. After acceptance, `make_tables.py` will read only `results.csv` (no exclusion file, no grade file), and `checks.py` will recompute independently with pandas. Those two changes are not made yet.

## What it costs
- `results.csv` stops being the file the runners append to; it becomes a generated file. Anyone who edits it by hand loses the edit on the next build.
- Until the new load runs exist, every stand-in row is labelled superseded, so Tables 2a and 2b would be empty apart from the real steady row. **Do not run `--write` before the new runs are in**, or accept an interim write-up with "not measured" rows.
- Rule 5 removes 33 first-token values that were really "time until the failure was reported". That time is still in `total_ms`.
- The scenario names get longer, and a reviewer must read one line of explanation to know what `_superseded` means.

## What would change our mind
- Aloo prefers that reviewers see only current runs in `results.csv`: choose option 3.
- A reviewer-facing reason to keep the old names (for example, the old tables are kept in the write-up as context): keep the old names and add a suffix to the new runs instead.

## CTO review
2026-11-13, final submission review of the whole tree: `GO WITH CHANGES`, projected 68/100 as it stood and about 82 with its fixes. Builder-side fixes it asked for are applied (two prose figures corrected, M4 regraded, disclaimer rule tightened, dead limiter branch removed). Still open and Aloo's: this record's acceptance, the personal sections, blind grading, missing transcripts. (`docs/reflection_draft.md` was removed by Aloo's instruction on 2026-11-13.) The reviewer also suggested the simpler option 3 (see Options).

## Aloo's call
Accepted. Aloo, in the Claude Code session on 2026-11-13, answering "Do you accept record 0007 so I can replace results.csv with the view and commit?": "Yes, write and commit". `results/results.csv` was replaced by the view the same day (SHA-256 of the pre-rework file is in Evidence; the byte-for-byte copy is `results/raw/results_full_log_2026-11-12.csv`).
