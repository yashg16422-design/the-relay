# Cursor completion prompt

Continue The Relay assignment in this repository. The user has authorized completion of all remaining work **except conversation transcript export**, which the user will do later. Do not ask for renewed approval for the bounded file edits and experiments listed here unless their scope or risk materially changes.

## Read before acting

Read `CLAUDE.md`, `AGENTS.md`, `KICKOFF.md`, `HANDOVER_CURSOR.md`, and `brief/The_Relay_Assignment_Brief.pdf`. The PDF defines assignment requirements; the other files define project workflow. Inspect `git status` first because this working tree contains substantial uncommitted work. Do not reset, clean, or overwrite unrelated changes.

## Explicit authorization: grade merge

The user approves a one-time exception to the append-only rule **for the `answer_grade` field only**:

1. Copy the current `results/results.csv` to a clearly named pre-grade-merge snapshot outside `results/raw/` before editing.
2. Join `eval/human_grades.csv` to `results/results.csv` by exact `request_id`.
3. Fill only the 134 blank `answer_grade` cells for matching successful E5 rows. Do not change any other field, any existing nonblank grade, any of the 7 rate-limited E5 attempts, any E6 row, or any stand-in row. Keep the raw results and review evidence untouched.
4. Fail safely if the join is not exactly one-to-one for all 134 grades, if a grade value is invalid, or if a target row is not a successful E5 attempt. Report the mismatches without guessing.
5. Update the project decision/audit record to document this user-approved exception and preserve `eval/human_grades.csv` as the grading source.
6. Make `analysis/make_tables.py` derive Table 3 quality figures from `results/results.csv` alone after the merge. Do not silently depend on an external grade join for the published table.

## Finish the non-transcript work

1. **Two-page narrative:** Reduce the write-up prose to the brief's two-page limit while retaining every required heading and required facts. Keep the generated results tables intact and machine-derived. Preserve the user's approved reflection meaning and first-person voice; do not fabricate experience. Keep the transcript section as a clear user-to-fill placeholder.
2. **Retry-worsens case:** The saved results currently have zero retries, so there is no measured example. Run a bounded, reproducible stand-in fault-injection experiment that can demonstrate a retry making a request worse (for example, a retry consumes deadline/patience or adds latency without improving the outcome). Use a fixed seed and compare the relevant retry/no-retry behavior. Save the trace and result rows with an honest scenario label. Do not claim a result unless the run demonstrates it.
3. **Stand-in calibration:** Review the real latency evidence and stand-in parameters. Derive only parameters that the real sample supports, show sample sizes and uncertainty, and leave the rest marked unverified. Do not claim a reliable distribution from five E6 requests. If additional real calls are required, use the guarded `LLMClient`, estimate requests/tokens first, obtain CTO review, and stay within a conservative ledger-based budget.
4. **E7 daily exhaustion:** Do not blindly exhaust the user's shared Groq quota. Check whether current account-specific remaining daily usage can be verified and safely budgeted. Run only if the data and CTO review establish a safe plan; otherwise preserve the no-go, state exactly what was unavailable, and leave real E7 as `not measured`. Do not imply that stand-in E7 is a real-provider result.
5. **S6 and any other plan gaps:** Check the PDF and current plan. Do not invent an S6 definition if the source does not define it; label it clearly as undefined/not run if applicable.

## Working and verification rules

- Treat HTTP 429 as `rate_limited`, never as a wrong answer. Keep the 7 failed E5 attempts visible and ungraded.
- Never inspect, print, or commit `.env` or a key. Never make a provider call outside the guarded client.
- Keep all result values generated from `results/results.csv` by `analysis/make_tables.py`. Say `not measured` when evidence is missing.
- Keep `results/raw/` append-only. Do not commit unless the user separately asks.
- After changes, run the full test suite, `compileall`, `analysis.make_tables`, `analysis.checks`, and a final CTO review. Confirm the tables can be recomputed from `results.csv` alone. Report test counts, checks, and any remaining blocked requirement.
- Do not create, edit, or fabricate transcripts. The user will provide those later.

## First response

Before editing, briefly report the current repository state and confirm the exact grade counts and target rows. Then proceed with the authorized grade merge and the listed work, stopping only for a concrete safety/data-integrity blocker. At the end, give the user a short list of completed items and anything that remains genuinely unmeasured or user-owned.
