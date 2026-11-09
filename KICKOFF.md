# Kickoff prompts — paste these into your builder agent, one gate at a time

These are starting points. Edit them in your own words before pasting; the edits are part of what shows you directed the work.
After every agent session: run `/export` (Claude Code) and save it into `transcripts/`.

---

## Phase 0 — foundation (tonight)

```
Read CLAUDE.md fully, then the brief in brief/. Summarise back to me in 10 lines what you think is being graded and the three biggest risks, so I can check you understood. Do not write code yet.

Then do Phase 0 only:
1. Write decision records 0001 (stack) and 0003 (citation format) as proposals.
2. Build relay/config.py and relay/llm_client.py (streaming + non-streaming, header capture, budget ledger). Explain each module to me per CLAUDE.md §6 and wait for me to run it.
3. Write a script that lists Groq models (GET /openai/v1/models) and makes ONE tiny real call per candidate model, printing every response header and the usage block. Show me the estimated cost first and wait for my "go".
4. From those results, write decision record 0002 (model per step).
Call the cto agent on 0001–0003 and paste its verdicts. Then stop at Gate A.
```

## Phase 1 — measure the real service

```
Phase 1. For each of E1–E4 (playbook §5): show me the script, the estimated requests and tokens per model, and the remaining budget from budget_ledger.csv. Ask the cto agent for a GO/NO-GO on the plan. Wait for my "go" before each one.
Save raw headers and raw 429 bodies to results/raw/. After all four, generate analysis/table1.md from the raw files (with a "how found" pointer to file and line, and a confidence level), and fit the latency distributions into standin/config.yaml with a comment on every parameter naming the measurement it came from.
Tell me the three most surprising things in the raw data before I look at the table.
```

## Phase 2 — library, retrieval, pipeline v1

```
Phase 2. Fetch the three papers at exactly 1706.03762v7, 2410.00037v1, 2507.07955v2 and record SHA-256 hashes. Ingest with page numbers and sections; tables as their own chunks; make dropping the references section a switchable option.
Build chunk settings R1, R2, R3 and eval/recall.py (zero model calls).
Propose 40 candidate eval questions across the 5 types in playbook §7, each with the exact passage text and page that answers it (or, for unanswerable ones, the nearest passage and why it does NOT answer it). I will pick and rewrite 30 and write the gold answers myself.
Also test the five "naive retriever" failure candidates from playbook §6 against the real chunks and show me, for each, the question, what a plain BM25 top-4 returned, and whether it actually misleads. Do not keep any you cannot demonstrate.
```

## Phase 3 — stand-in, limiter, scheduler, load

```
Phase 3. Write decision records first for: the scheduler (deadlines, priority, shedding), the fallback ladder, the retry policy, the sentence gate, and the patience distribution. Each must name the measurement behind it. Get cto verdicts, then wait for my approval.
Then build the stand-in, limiter, scheduler, cache/coalescing, retry policy and open-loop loadgen, one module at a time, explaining each per CLAUDE.md §6.
Run S1–S3 on the stand-in and show me the raw numbers before any interpretation.
```

## Phase 4 — experiments, tables, write-up support

```
Phase 4. Plan E5 (split across two days), E6, S4–S6 and E7 with budgets; get cto GO/NO-GO; wait for my go on each real run.
Then generate all tables with analysis/make_tables.py, the slow-request waterfall with analysis/waterfall.py (state your selection rule), and run analysis/checks.py.
Finally ask the cto agent to recompute every table independently from results.csv and diff it against WRITEUP.md.
Do NOT draft my Vaani feedback, my "new to me" paragraph, my "distinctly mine" paragraph or my overrules.
```

---

## Useful one-liners for you

- Ask for a review at any point: `Use the cto agent to review <file or decision record>.`
- When the agent guesses: `What measurement is that based on? If none, propose the cheapest experiment that would give it.`
- When code appears too fast: `Stop. Explain this module per CLAUDE.md §6 before going further.`
