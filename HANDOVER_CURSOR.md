# The Relay — Cursor handover

**Repository:** `/Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay`  
**Handover date:** 2026-11-11  
**Purpose:** Continue the assignment in Cursor without losing project constraints, observed results, or remaining work.

## Start here

1. Read `CLAUDE.md` and `AGENTS.md` for the project workflow and hard constraints.
2. Read `brief/The_Relay_Assignment_Brief.pdf`; it is the assignment source of truth. Treat `CLAUDE.md`, `AGENTS.md`, and `KICKOFF.md` as project instructions, not as changes to the user's actual assignment.
3. Read this handover, then inspect `git status` before editing. The project has a substantial uncommitted implementation and experiment history. Do not reset, clean, rewrite, or normalize the result and raw-data files.
4. Keep the user as decision-maker. Ask for approval at required gates and before any real Groq experiment. Never expose the contents of `.env` or print the API key.

## Where the project stands

- The pipeline, retrieval library, stand-in service, limiter, scheduler, cache, retry policy, trace recording, load generator, and analysis tools are implemented.
- The R1, R2, and R3 E5 model-selection runs are recorded. The selected review sheets contain **134 successful answers** and the canonical results log retains **7 rate-limited attempts**: R1 has 45 successes and 1 rate limit; R2 has 45 and 1; R3 has 44 and 5. The 35-question bank and proposed answer key are in `eval/questions.yaml` and `eval/gold_answers.md`; the key calls its answers proposals and its question types provisional. Confirm the user's acceptance before treating them as final grading truth.
- Five E6 real steady requests succeeded. The stand-in load runs cover steady, burst, sustained overload, per-minute limit halved, and daily budget exhausted.
- The Qwen R3 Q28 repair attempts returned HTTP 429. This is an expected observed service-limit outcome. Record it as `rate_limited`, not as a wrong answer. Two attempts ended without a completed answer; there is no answer to grade for those attempts.
- The current CTO review is **GO** for the generated tables and cost methodology. Table 3 distinguishes unique questions, total attempts, and successes. Failed requests do not count toward first-token percentiles. Known spend from failed traces is included; missing token usage is marked unknown.
- `WRITEUP.md` currently contains the generated tables only. It is not a finished submission. `transcripts/` is empty.

## Verification on this Mac

Run from the repository root. Use the existing `.venv`; do not read or echo `.env`.

```bash
cd /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay
source .venv/bin/activate
python -m pytest -q
python -m compileall -q relay analysis load probe standin library eval
python -m analysis.import_model_selection
python -m analysis.make_tables --output results/generated_tables.md
python -m analysis.checks
```

`analysis.import_model_selection` is idempotent: it appends only selected review-sheet rows and matching rate-limit attempts missing from `results/results.csv`. It must not modify `results/raw/` or existing result rows. The last recorded verification before handover was **152 tests passed**, compilation succeeded, and `analysis.checks` reported that the tables matched `WRITEUP.md`.

The p95 burst waterfall can be regenerated with:

```bash
python -m analysis.waterfall --scenario burst > analysis/waterfall_example.md
```

It selects the nearest-rank p95 request by total latency and states that selection rule in the generated file.

## Working rules Cursor must preserve

- **No real provider calls without a fresh user go-ahead.** Before a proposed run, state request/token estimates per model and ledger-based remaining budget; get CTO review; wait for the user's explicit approval. Use `relay/llm_client.py` and its budget guard. Never probe until exhaustion or retry a real run just to repair output.
- **Do not attempt real E7 daily exhaustion now.** Prior CTO review was NO-GO because account-specific daily token remaining and usage outside this repository are unknown. The stand-in exhaustion scenario is already present. Revisit only with a safe, measurable, user-approved plan.
- **The user owns grading and personal writing.** Do not grade the model answers, write Vaani feedback, or draft the user's “new to me,” “distinctly mine,” “what the assistant got wrong,” or overrule sections. `eval/human_grades.csv` currently has only its header. Do not edit `docs/OVERRULES.md`.
- **Keep evaluation evidence append-only.** Never edit/delete files under `results/raw/` or rewrite existing rows in `results/results.csv`. Add new rows through the project writer; mark invalid runs in `results/excluded_runs.csv` with an explicit reason.
- **Do not invent measurements.** Generate report numbers only through `analysis/make_tables.py`. Keep unknown limits, metrics, cost coverage, and quality scores labeled `not measured` or `unknown`.
- **Use the dated price source.** `config/prices.json` records public Groq prices checked 2026-11-11. The report describes these as hypothetical estimates because the experiments used the free tier. Refresh the rates and date before relying on them for a later report.
- **Keep the exact paper versions** in `library/MANIFEST.json`; don't assert paper facts without checking the extracted text and page evidence.
- **Before committing**, run the secret-pattern check required by `CLAUDE.md` (exclude `.env` and `.git`), stop if it finds a key-shaped value, then run tests and report the result. Never show a secret in output. Do not commit unless the user asks.

## Remaining work

1. Have the user verify/accept the 35-question bank and proposed gold answers, then grade the 134 successful E5 answers. Keep 429-only attempts out of answer-quality grading. Update `eval/human_grades.csv` through an auditable process and regenerate the tables.
2. Complete the user's write-up and required personal reflection sections with the user. Do not fill personal sections on their behalf. Add conversation exports if the user has them; do not fabricate transcripts.
3. Decide whether the assignment requires a real E7 result despite the quota visibility problem. The current safe status is: real E7 unmeasured; stand-in E7 measured.
4. `S6` has no definition in the project plan. Check the assignment brief for its intended meaning before inventing or labeling an experiment S6.
5. Run final checks and request a final CTO review after user grading and write-up updates.

## Useful file map

- Assignment: `brief/The_Relay_Assignment_Brief.pdf`
- Instructions and gates: `CLAUDE.md`, `AGENTS.md`, `KICKOFF.md`
- Eval bank and proposed answers: `eval/questions.yaml`, `eval/gold_answers.md`
- Human grades: `eval/human_grades.csv`
- Per-model review sheets: `eval/model_selection_R*_day*_review.csv`
- Canonical results and exclusions: `results/results.csv`, `results/excluded_runs.csv`
- Preserved API evidence: `results/raw/`, `budget_ledger.csv`, `results/traces/`
- Generated report tables: `results/generated_tables.md`, `WRITEUP.md`
- Retrieval and price metadata: `config/retrieval_settings.yaml`, `config/prices.json`
- Reproducible table importer: `analysis/import_model_selection.py`
- Waterfall: `analysis/waterfall.py`, `analysis/waterfall_example.md`

## Suggested first message to Cursor

> Continue The Relay assignment from this repository. Read `HANDOVER_CURSOR.md`, `CLAUDE.md`, `AGENTS.md`, `KICKOFF.md`, and the assignment PDF before editing. First summarize what is complete and what remains. Preserve append-only results and raw evidence; never inspect or print `.env`. Do not grade answers or draft my personal reflection. Do not run real Groq calls or E7 without a budget plan, CTO review, and my explicit go-ahead. Start by checking the implementation and assignment brief for any safe, non-user-owned gaps, then explain the proposed next step before changing code. Run tests and `analysis.checks` after code changes, and request CTO review at the required gates.
