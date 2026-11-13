# The Relay: Final-Push Architectural Brief for Claude Code (v2, CTO-reviewed)

**Prepared:** 2026-11-12 by a claude.ai architecture-review session that Aloo requested. An independent CTO-reviewer pass followed (verdict: GO WITH CHANGES). Every change it required is folded in below.
**Executor:** Claude Code, in `~/Downloads/relay-agent-pack/the-relay` on Aloo's Mac.
**Decision-maker:** Aloo. This document is a **proposal**. Nothing in it authorises spending or accepts a decision on Aloo's behalf.
**Goal:** Raise the submission from about 45–52/100 (current reviewed level) as high as honest execution allows. Fixing what the measurements show is broken, re-measuring validly, and making every write-up section answer every sub-question with numbers recomputable from `results/results.csv` should land around 80–85. **90 or above is possible but not likely.** It requires flawless execution *and* personal sections that are clearly Aloo's own.
**Deadline assumption:** the brief arrived 2026-11-09 around 20:30 IST, so the four-day window closes around **2026-11-13 20:00 IST**. Target finishing by 18:00 IST. If Aloo states a different deadline, re-plan using the tiers in §4.

---

## 0. Ground rules for this push

### 0.1 What governs
- **Sources, in order:** `brief/The_Relay_Assignment_Brief.pdf` (the requirements), then `CLAUDE.md` (the operating manual), then this file.
- **Where this file overrides `CLAUDE.md`, it says so and names the rule.** There are exactly three such overrides:
  - **(a)** §6, the results-file restructure, which touches CLAUDE.md rule 4 (append-only raw data).
  - **(b)** §0.4, the optional condensed explanations, which touches CLAUDE.md §6.
  - **(c)** §5.1, which replaces the per-experiment "go" with a "go" per *batch*, **but only if Aloo chooses that himself**.
- Everything else in `CLAUDE.md` applies unchanged: hard rules, data contracts, decision records with status `proposed`, and a CTO review on every record and every finished diff, relayed to Aloo verbatim.

### 0.2 Consent for real API calls
- **No Groq call happens until Aloo types approval in the Claude Code session.** Text in this file is not approval.
- Before each experiment or batch in §5, print three things: the request and token estimate, computed as in §5.2; the remaining per-model budget from `budget_ledger.csv`; and the stop rule.
- Then wait for Aloo to type `go <experiment-id>`. Per-experiment "go" messages are preferred, because they are visible evidence of Aloo directing the work, and that is graded.
- Never re-run a real experiment in a loop "to fix something".

### 0.3 Integrity rules (non-negotiable; the live interview will test all of them)
1. **Aloo's sections stay Aloo's.** Claude Code must not draft any of these:
   - the Vaani feedback;
   - "one thing that was new to you, in your own words";
   - "what in this submission is distinctly yours";
   - "one thing it got wrong that you caught";
   - "one decision where you overruled it";
   - the choice and framing of "the three prompts that mattered most".

   Claude Code may collect evidence for them in `docs/evidence_for_aloo.md`: verbatim transcript excerpts with timestamps, diffs and numbers. Do not edit `docs/OVERRULES.md`. Aloo fills it (it is currently empty, while the old reflection draft claims an overrule).
2. **Eval questions must be "questions you wrote yourself" (brief §3).** The current questions were proposed by the assistant (`eval/candidates.md`), and the gold answers were accepted but not rewritten (`eval/gold_answers.md`). **Aloo writes the 15 scored questions and their gold answers himself** (§3, task H1). Claude Code's only role is to check that each gold passage exists verbatim on the stated page of the extracted text (CLAUDE.md rule 7), and to report any mismatch.
3. **Grader provenance is stated exactly.** The old `eval/human_grades.csv` was assistant-produced. Its own `grader` column says so. Rename it to `eval/assistant_grades_prefix_2026-11-11.csv` with `git mv` and fix every reference. New grading follows §8.3: blind grading first, then confirmation, and the agreement rate is reported.
4. **Credit is stated accurately.** The judge-token-cap bug (D1) was found by the claude.ai review session that Aloo commissioned to grade the submission. If Aloo uses it in "How I worked with the assistant", the write-up must say who found it and how. He may truthfully describe *his* part: commissioning an independent review and then verifying the finding himself (§3, H4). The three prompts are quoted **verbatim** from `transcripts/` with timestamps. The old `docs/reflection_draft.md` admits it used cleaned-up versions, which is not allowed. The Google Search AI help with prompt wording (`transcripts/README.md`) is disclosed next to them.
5. **No invented numbers and no invented paper facts.** Every number comes from `analysis/make_tables.py` output or a named raw file. Every paper claim quotes extracted text with its page.
6. **Secrets.** Never print `.env`. Run the CLAUDE.md §1.1 key grep before every commit.
7. **No limit dodging.** Groq limits are per model, and the plan below gives different models different roles. That is legitimate, but it is a deliberate choice, and the write-up must state it in §2. The pipeline must **never** fail over to another model, or rotate models, to escape a limit under load. That is listed as deliberately not built (§10, §8 of the write-up).

### 0.4 Working cadence
At the start, ask Aloo one question: *"For this push, do you want the full CLAUDE.md §6 explanation after each module, or a condensed one (one scenario sentence, the key snippet with line-by-line notes, and the run command)?"* Default to condensed if he says time is short. Batch decision records 0006–0011 into **one** review sitting with Aloo rather than six interruptions.

---

## 1. Diagnosis: verified facts (repo at commit `04688fb`)

The CTO reviewer re-derived each item from the files. Re-check before acting; the one-line checks are in brackets.

**D1. The judge step mostly refuses because of a token cap, and Qwen has a second, unexplained cause.**
- The cap and the refusal path: `config/pipeline.yaml:3` sets `judge_max_completion_tokens: 120`. `relay/pipeline.py:909–917` returns `(False, …)` on invalid JSON or a missing boolean, and line 416 turns that into a refusal. Nothing reads `finish_reason` [`grep -n finish_reason relay/*.py` returns nothing].
- The counts across the 134 graded pre-fix answers:
  - 96 of 107 answerable questions were wrongly refused, and only 11 were correct.
  - 71 of the 96 wrong refusals had judge `tokens_out` of 118 or more, while all 11 correct answers were below that.
  - The gold passage was in the retrieved set in 70 of the 96 (exact normalized match).
  - 17 of the 27 "correct refusals" were also at the cap, so the 100% correct-refusal figure was partly luck.
- The cap explains gpt-oss (85 of 90 judge spans at the cap, with `reasoning_tokens` reaching 118 of 120). **It does not explain Qwen:** only 3 of 49 Qwen judge spans hit the cap, yet Qwen still has 25 wrong refusals (20 at the judge, 5 at the gate). The judge's raw text was never saved, so the cause is unknown. Fix the logging first (P0.1) and diagnose inside E0.

**D2. The stand-in load runs measured the answer cache, not the pipeline.**
- `load/scenarios.yaml` draws from a pool of only 4 questions, and the cache TTL is 60 s.
- How many requests actually reached the model in each run:

  | Run | Reached the model | Cache hits | Coalesced |
  | --- | --- | --- | --- |
  | S123 `burst` | 4 of 40 | 24 | 12 |
  | S123 `sustained_overload` | 4 of 75 | 71 | 0 |
  | S123 `steady` | 4 of 11 | 7 | 0 |
  | S45U `per_minute_limit_halved` | 15 of 24 | 9 | 0 |
- **Every existing stand-in load run is superseded.**

**D3. The stand-in is uncalibrated, and its token accounting overstates capacity.**
- All delay and failure parameters in `standin/config.yaml` are 0. RPM is 60 and TPM is 30,000, against real values of 30 and 8,000.
- `standin/server.py:117–170` charges only *input* tokens against TPM and TPD, and its replies are one copied sentence (median about 85 tokens). Real drafts are 200–400 output tokens plus reasoning.
- It uses sliding windows, while Groq refills like a token bucket (D5).
- The real data exists: **307 timed real model-call spans** (nearest-rank median 693 ms, p95 1,559 ms, maximum 8,733 ms). 27 of them carry a streaming TTFT (median 470 ms, p95 1,454 ms).

**D4. `results.csv` does not reproduce the tables by itself.**
- The S45 rows that `excluded_runs.csv` excludes are still in it, so computing from the file alone gives 10.4% and 47.5% rate-limited, where the tables say 20.8% and 95.0%.
- There are `not_graded` values and blank E6 grades.
- Table 3's "Wrong %" includes `refused_wrongly` (`analysis/make_tables.py:907`), which mixes safety and usefulness.

**D5. Real limits are refilling buckets, already visible in saved headers at zero cost.**
- Across 332 real header snapshots, `x-ratelimit-reset-tokens` equals tokens used × 60/8,000 (a TPM refill of 133.3 tokens/s) to within 1 ms at the median.
- `x-ratelimit-reset-requests` equals requests used × 86.4 s (`make_tables.py:534` already notes this).
- The ledger holds 7 real TPM 429s with `retry-after` from 1 to 16 s. No 429 *body* was ever saved.
- Requests-in-flight and time-to-recover are not measured.

**D6. Capacity is bound by tokens, and the daily binding limit is tokens per day.**
- Measured per-step input medians, from the E5 traces:
  - `understand`: about 190 tokens.
  - judge, draft and repair: about 970, 930 and 930 at R1; about 1,620, 1,780 and 1,950 at R2; about 2,700, 2,800 and 2,200 at R3.
- A full answer with raised caps therefore costs about **3.3k tokens at R1, 5.2k at R2 and 7.5–8k at R3**, so 8,000 TPM sustains only about **2.4 answers per minute at R1 and about 1 per minute at R3**.
- At 200k tokens per day, one model gives roughly 60 R1 answers per day, while 1,000 requests per day would allow about 250. **Tokens per day binds first.** This is likely the surprising number.
- Direct evidence: on 2026-11-11 the old pipeline, mostly stopping early at the judge, still used about 138k tokens on Qwen and about 106k and 95k on the gpt-oss models.

**D7. The write-up's answers are incomplete.**
- §4 analyses a request with 0 ms waiting and 0 ms retrying.
- §5 lacks the who / why.
- §7 lacks the reply text.
- The retrieval section lacks the "when the limit is close" rule.
- The naive-retriever section gives one mechanism with three examples instead of three mechanisms.
- §6's retry case is a toy.
- The write-up points readers to a 4-page appendix PDF and a 2,450-word README.
- Table 1 prints `/Users/...` paths.
- User-visible first token equals total time (the gate releases nothing until the whole draft passes), which the write-up does not cost out.

**D8. The sentence gate is purely lexical.**
- `relay/gate.py` checks that the citation was retrieved, and that numbers and capitalised terms appear in the cited chunk.
- It does **not** check that the cited paper is the one the question is about. A Moshi question answered from the Transformer paper passes if the drafter avoids writing "Moshi".
- Negated claims also pass.
- Its false-accept rate is unmeasured.

---

## 2. Target design: the principles the final submission defends

**The one decision stays:** *nothing reaches the user unless retrieved evidence supports it; under pressure we degrade the answer, never the honesty.* The changes below make that hold under load and make the system useful instead of a refusal machine.

- **P-1. "Decide" must be cheap, robust, and separate "unknown" from "no".** See DR 0006 (§3, P0.1) for the two options.
- **P-2. Budget reasoning as well as output.** Per step: set a reasoning-effort control where the model accepts one, size `max_completion_tokens` for reasoning plus output, set `temperature: 0` if accepted, and record `finish_reason`, `reasoning_tokens` and the raw text of the decide step in the trace.
- **P-3. Tokens per minute and tokens per day are the binding limits.**
  - Admission, scheduling, retrieval depth and the stand-in all account in tokens: input *and* output.
  - The scheduler predicts per-request tokens from measured per-step medians at the chosen retrieval setting.
- **P-4. An explicit degradation ladder with per-request rules.**
  - The levels, from richest to most degraded:
    - **full**: repair allowed;
    - **lite**: no repair, and `top_k` minus 1;
    - **extractive**: no model call; the top 2 passages verbatim with citations, under a header saying they are passages, not an answer;
    - **honest refusal**: with the next-capacity time.
  - The rule compares each request's remaining deadline slack with the limiter's predicted wait and token headroom.
- **P-5. Retry only what can succeed inside the deadline.**
  - Honour `retry-after`.
  - Never retry a 429 whose `retry-after` exceeds the remaining slack.
  - Use a global retry budget.
  - Show with a controlled experiment that naive retries make overload worse.
- **P-6. The cache is a feature, measured on purpose.**
  - Main load shapes run with the cache **off**.
  - One extra burst row runs with it **on**, and reports the share served from cache.
  - Cache keys include the normalized question, retrieval setting, model and library manifest hash.
  - Only answers that passed the gate are cached.

---

## 3. Work items

Tags: **[ZERO]** = no API cost. **[DR n]** = decision record required, status `proposed`. **H** items are Aloo's own.

### Aloo's tasks: start H1 immediately, in parallel with Claude Code's P0 work
- **H1 (45–60 min, before E5). Write the 15 scored questions and gold answers yourself.**
  - At least **5 unanswerable** (the papers do not say), so that false-accept behaviour is measurable.
  - Cover all three papers, and include at least 2 numeric, 1 table-based and 1 cross-paper-trap question.
  - Format: `eval/questions_aloo.yaml`, same schema as `eval/questions.yaml`; give paper id, page and an excerpt for each answerable one.
  - Claude Code verifies the excerpts against the extracted text and reports mismatches. It does not rewrite your questions.
- **H2 (20 min). One sitting to accept, reject or change decision records 0006–0011.**
- **H3 (about 60 min across the night and morning). Grade** per §8.3: blind grades first.
- **H4 (15 min). Verify the D1 finding yourself.** Open one truncated trace and the pipeline code, so you can explain it in the interview from your own check.
- **H5 (90 min, Nov 13 afternoon). Write your personal sections, and fill `docs/OVERRULES.md` with real entries and evidence.**
- **H6. Export this claude.ai conversation and any other claude.ai sessions used** into `transcripts/`, next to the Codex export and the Claude Code `/export`.

### P0: validity and correctness (must ship; nothing here may be cut)

**P0.1 Decide step + gate hardening [DR 0006] [ZERO until E0].** Present two options with this budget arithmetic.

- **Option A, fix the LLM judge.** Raise the judge and `understand` caps to about 512 and the draft/repair caps from 400 to about 700. Add a three-state parse (`yes` / `no` / `uncertain`): strip code fences and take the first `{...}`. A truncated reply (`finish_reason=="length"`), invalid JSON or a missing boolean triggers **one** JSON-mode retry of the judge; if it still fails, the state is `uncertain`, which proceeds to the draft with the gate deciding. An explicit `no` refuses. *Cost:* about 3.3k / 5.2k / 7.5–8k tokens per answer at R1 / R2 / R3. Model A's Table-3 runs alone would then need about 165–185k tokens, against a 200k daily ceiling.
- **Option B (recommended), merge "decide" into "draft" with a deterministic pre-check.** The decide step becomes:
  1. a deterministic retrieval check, at zero tokens: the top passage's BM25 score is above a threshold tuned offline on the H1 questions, and the top passages come from the paper the `understand` step named;
  2. failing that check refuses immediately;
  3. otherwise the drafter is told to answer with citations **or** output exactly `NOT_IN_PAPERS`; the sentinel maps to an honest refusal.

  The pipeline still has more than 4 steps (understand → retrieve [tool] → decide [branch] → draft → gate → repair [loop]). It removes the fragile JSON step entirely and saves one full evidence-sized call: about 2.0k / 3.1k tokens per answer at R1 / R2. That is fewer tokens and a lower first-token time, both of which are scored. Keep Option A's code path behind `decide_mode: llm_judge | precheck_abstain`, so the stand-in can compare them at zero cost.
- **Both options add gate hardening:**
  - a **paper-scope check**: reject a sentence whose citations come from a paper other than the one `understand` identified, unless the question is explicitly cross-paper;
  - a negation check on numeric claims;
  - `tests/test_gate_adversarial.py` with at least 30 cases: wrong-paper citations, negations, wrong numbers, numbers present but in a different context. Report the gate's **false-accept %** in the write-up's §1 and use it in §8.4.
- **Both options add trace fields:** `finish_reason`, `reasoning_tokens`, the raw decide text (truncated to 500 characters), the parse state, and the pre-check scores.
- **Acceptance:** `pytest -q` passes; a 3-question stand-in run shows all the new trace fields.

**P0.2 A quality runner that survives limits [DR 0006].**
- Remove the `raise RuntimeError` that stops on any non-ok result (`eval/run_quality.py`); record the result and continue.
- Honour `retry-after`, with at most 2 attempts, inside the deadline.
- **Pace by tokens:** before each question, wait until predicted TPM headroom covers the estimated tokens.
- Scenario name `E5-quality-<setting>-<model-slug>`.
- Questions come from `eval/questions_aloo.yaml`.

**P0.3 The results-file restructure [DR 0007; overrides CLAUDE.md rule 4, so it needs Aloo's acceptance].**
- Copy the current `results/results.csv` byte for byte to `results/raw/results_full_log_2026-11-12.csv`, record its SHA-256 in the record, and never touch the copy again.
- `analysis/build_submission_csv.py` generates `results/results.csv` as a **view that keeps every row**:
  - Every superseded run gets its `scenario` relabelled `<name>_superseded`. That covers S123 `steady`/`burst`/`sustained_overload`, S45 and S45U `per_minute_limit_halved`/`daily_budget_exhausted`, and the pre-fix E5 rows (`E5-model-selection-*` → `E5-prefix-<setting>-<model>`). Nothing is hidden, and the tables filter by name.
  - `not_graded` becomes empty in the view, and the write-up says stand-in replies are synthetic and ungraded.
  - The 5 E6 real rows get grades (§8.3).
- `make_tables.py` reads **only** `results/results.csv`: no `excluded_runs.csv` and no grade files.
- "Wrong %" counts only `wrong`; a note under Table 3 gives the `refused_wrongly` %, so the columns add up.
- All file paths printed in the tables are repo-relative.
- **Acceptance:** `analysis/checks.py` recomputes every table cell from `results/results.csv` alone and diffs it against `WRITEUP.md` with zero mismatches.

**P0.4 Stand-in v2: calibrated, token-bucket limits, realistic tokens [DR 0008] [ZERO].**
- **Limits:** replace the sliding windows with **refilling token buckets** matching D5: TPM refills at 8,000/60 per second; RPD refills at one request per 86.4 s; RPM refills at 30 per 60 s; TPD at 200,000/86,400 per second (docs value, labelled medium confidence and unverified unless E7 runs).
- **Charge input + output + reasoning tokens.**
- **Replies:** sample `tokens_out` and `reasoning_tokens` per step from the real per-step distributions, and pad the streamed reply to that length.
- **Latency:** `analysis/calibrate_standin.py` fits a lognormal to each of these, per step and preferring the load model's spans, and writes `standin/config.yaml` with a comment naming the source file and statistic for every value:
  - TTFT for streamed steps;
  - base latency for non-streamed steps;
  - per-output-token time;
  - a slow tail (`slow_success_probability` = share of calls above twice the median; extra time = their median excess).
- **Failures:**
  - 5xx rate = errors over real calls in the ledger (1 in about 330; say how weak that estimate is);
  - hangs set to 0 ("never observed in N real calls");
  - a separate labelled `chaos` profile for robustness runs only.
- **Load slowdown:** taken from E4; if none is measurable, keep a small value labelled as the stand-in's biggest unvalidated assumption.
- **429:** copy the exact body and headers of a real 429 from E2/E3.
- **Acceptance:** `results/calibration_report.md` shows real versus stand-in latency (p50/p90/p95/p99 with n), the stand-in side measured from 100 single calls to it. The p50 and p95 agree within about 15%. The report names the three biggest differences, for example:
  - no multi-tenant noise;
  - latency independent of prompt length, if that holds;
  - token counting approximated at 4 bytes per token.

**P0.5 Valid load scenarios [DR 0009] [ZERO].**
- Use a question pool of all H1 questions plus the 35 legacy questions, with Zipf 1.1.
- Add a `cache: on|off` flag per scenario.
- **Patience:** lognormal, median 8 s, capped at 20 s, labelled as an assumption.
- **Deadline = that request's patience draw** (not a separate 30 s, which could never bind).
- Size every scenario from the **measured** capacity of the calibrated stand-in at R1 (expect about 2.4 answers per minute; confirm before sizing):

  | Scenario | Arrival pattern | Duration | Cache |
  | --- | --- | --- | --- |
  | `steady` | Poisson at about 60% of capacity | 15 min | off |
  | `burst` | 30 requests within 10 s | then 15 min of drain | off |
  | `burst_cache_on` | the same as `burst` | the same | on |
  | `sustained_overload` | Poisson at about 3× capacity | 15 min (enough requests for a p99, n ≥ 100) | off |
  | `per_minute_limit_halved` | Poisson at about 80% of capacity; at t = 4 min, TPM 8,000 → 4,000 and RPM 30 → 15 through `/admin/limits` | 12 min | off |
  | `daily_budget_exhausted` | steady load; set remaining **TPD** so it runs out at about t = 3 min | 8 min | off |
  | `retry_storm` vs `retry_disciplined` | sustained overload, same seed and arrivals: (a) immediate retries ignoring `retry-after`, 3 attempts, no global budget; (b) the real policy | as `sustained_overload` | off |

  The write-up explains that TPM binds, not RPM, for the halved-limit scenario. After `daily_budget_exhausted`, capture every reply text.
- Run scenarios as **separate processes on separate stand-in ports, in parallel**.
- **Acceptance:** in every cache-off scenario, at least 80% of rows have `tokens_in > 0`; print this in the run summary.

**P0.6 Three distinct naive-retriever failures [ZERO; must not be cut, because the brief says "Name three ways"].** Run plain BM25 over R1/R2/R3 and show three different *mechanisms*, each with a question and the wrong top passage (quoted, with paper id and page). Only report mechanisms you can demonstrate with real retrieval output. Candidates to test:
1. **cross-paper term collision** (already shown: a Moshi GPU question retrieves Transformer p. 8);
2. **tables or equations flattened by PDF extraction** (the Transformer's Table 3 variants, the Table 2 BLEU scores);
3. **bibliography or related-work passages outranking the method section** (a Moshi "Mimi" question, or H-Net terms that appear in cited titles); or the word "chunk" in the Dynamic Chunking paper colliding with retrieval language.

Give the fix for each: the paper-scope check from P0.1, keeping tables intact as chunks, and dropping references. The ingest option for dropping references exists; measure its recall change.

**P0.7 Limits from saved headers [ZERO].** `analysis/limit_refill.py` derives the TPM refill rate and the RPD refill time from the 332 snapshots in `budget_ledger.csv`, and writes `results/raw/limit_refill_analysis.json`. Table 1 cites it.

### P1: answers that earn the marks

**P1.1 The degradation ladder and budget-aware retrieval [DR 0010].**
- Implement P-4. Thresholds come from measured p95 per level (full/lite from the E5 runs, extractive from the calibration).
- Lite is triggered when predicted TPM headroom is below 1.5× a full request's estimated tokens. The retrieval section states this rule and the measured token saving.
- **Extractive fallback rows:** `status=rate_limited`, empty `first_token_ms` (the passages are not a model answer); the trace has `fallback_delivered=true` and `level=extractive`.
- **Daily exhaustion reply:** say the daily model budget is used up; give the passages if the pre-check passes; give **when the next request can be served**, computed from the refill rate. Do **not** quote the full reset time as "when it comes back".

**P1.2 Peak queue length computable from the CSV.** Define it as the maximum number of requests in the system at once, found by sweeping over the intervals `[start_time, start_time + total_ms]` in `results.csv`. State the definition under Table 2b. It replaces "not measured".

**P1.3 Hypothetical cost for stand-in rows.** Use the load model's published Groq price applied to the stand-in's token counts, labelled hypothetical, citing `config/prices.json` with its URL and date.

**P1.4 Waterfall that shows the real costs.**
- Selection rule: the nearest-rank p95 `total_ms` request of the **real burst** (E8). If it has no wait or retry span, use the p95 of stand-in `sustained_overload`.
- Show its span table, split into waiting / working / retrying.
- Mark the **provider TTFT inside the draft span** next to the **user-visible first token**. Put a number on the gap the gate's buffering costs; that gap is the before/after-first-token answer.

### P2: stretch (only once all of P1 is done)
- **P2.1 Progressive gating.** Gate and release sentence by sentence as the draft streams. Measure on the stand-in plus at most 10 real requests, counted inside E5's cap. If it isn't built, it becomes a "deliberately not built" item, with the expected gain calculated from measured per-token times.

---

## 4. Cut order if time runs short
P2 → `burst_cache_on` → P1.3 → E7 → P1.2 (fall back to the scheduler's own queue counter, defined in the note).
**Never cut:** any P0 item, H1, H5, or any write-up sub-question in §9.

---

## 5. Real Groq experiment plan (each needs Aloo's typed "go")

### 5.1 Model roles (limits are per model; the write-up states this)
- **Model A (quality / Table 3):** chosen in E5-sel.
- **Model B (load path: E6b, E8; limit probes E2, E3):** the runner-up.
- **Model C (E4 concurrency; optional E7 token-per-day exhaustion):** the remaining model.

### 5.2 How to estimate before each "go"
- Compute tokens per answer from the **per-step medians in the existing traces**, scaled to the new caps and the chosen `decide_mode`.
- Print each model's total against 200,000 TPD, minus ledger use in the last 24 h, plus refill. Use the last-24h computation, not "the bucket is full".
- **Guard:** if any model's projected 24-hour total exceeds **170k**, shrink in this order: E6b to 10 → E8 to 6 → lower reasoning effort on `understand` → run E5 R3 last, after refill.

### 5.3 Experiments
The token figures assume Option B (in brackets: Option A). Q = Aloo's 15 questions (at least 5 unanswerable).

| ID | What | Model | Requests (cap) | Tokens (cap) | Notes |
| --- | --- | --- | --- | --- | --- |
| E0 | Parameter probe: which reasoning-effort values, `include_reasoning`/`reasoning_format`, `response_format=json_object`, `temperature: 0` and forced-truncation `finish_reason` each model accepts. For Qwen, also 3 calls replaying old wrongly-refused judge prompts with raw text logged | A, B, C | 10 each | 5k each | Results go into DR 0006 |
| E5-sel | 8 of Q (6 answerable + 2 unanswerable) × R1 | A, B, C | 8 (+4 retries) | 20k [30k] each | Choose A by: fewest `wrong` → highest correct-and-cited → lower p95 total → fewer tokens per answer. Disclose that selection used the same questions (bias). One-answer differences are noise at this n; say so |
| E5-A | Model A: the remaining 7 of Q on R1, then all 15 on R2 and on R3 | A | 37 (+8) | 95k [150k] | **R3 redefined [DR 0011] as R2's 1,600-char pieces + top-4 + re-rank**, so R1→R2 isolates piece size and R2→R3 isolates re-ranking. Cheaper and a cleaner experiment. Re-rank runs locally (`cross-encoder/ms-marco-MiniLM-L-6-v2`, CPU) |
| E6b | Real steady: 10 requests, Poisson about 1.5/min, R1 | B | 10 | 25k | Replaces the n=5 real steady as the main row; the old row stays |
| E8 | Real burst: 8 requests at once, R1, real retry policy | B | 8 (+ 429 attempts) | 20k | Should hit real TPM; shows queueing, `retry-after` and drain |
| E2 | RPM + recovery: 35 tiny calls back to back (`max_completion_tokens` 16, low reasoning), then 1 probe per 2 s until success | B | 60 | 4k | Save the full 429 body + headers. Recovery = first 429 → first success. If no 429 at 35, report "RPM ≥ 35/min burst" and stop |
| E3 | TPM 429 body: at most 4 calls of about 3k input tokens, back to back | B | 4 | 12k | Rolling vs fixed is already answered by P0.7 |
| E4 | In-flight: batches of 10, then 20, simultaneous tiny calls, **at least 70 s apart** so per-minute limits don't confound it | C | 30 | 3k | "In flight at once" = the largest batch with no rejection and no latency above 2× the single-call median; report "≥ 20, no limit found" as a lower bound if nothing breaks |
| E7 | **Optional, separate "go":** TPD exhaustion on C with about 7k-token prompts at TPM pace (about 25 min), then one full pipeline request to capture the real user-visible reply | C | ≤ 35 | ≤ remaining TPD | Verifies TPD (currently docs-only) and gives write-up §7 a real reply. C is unusable for about 24 h; schedule it overnight |

**Expected totals (Option B):** A ≈ 120k, B ≈ 90k, C ≈ 30k (+ E7). Option A pushes Model A to about 185k, over the guard, which is the main budget argument for Option B. **Order:** E0 → E2 ∥ E3 ∥ E4 → E5-sel → Aloo blind-grades the 8 × 3 → choose A → E5-A ∥ E6b → E8 → E7 overnight.
**Stop conditions:** 3 or more consecutive 5xx, an unexpected 400, or a ledger write failure. Stop and report; never retry the experiment.

---

## 6. `results.csv` contract (unchanged columns)
`request_id, scenario, source, start_time, first_token_ms, total_ms, status, retries, tokens_in, tokens_out, retrieval_setting, answer_grade`. Values must be in the brief's sets. Superseded runs are relabelled as in P0.3. There are no extra columns; extra fields go in traces.

---

## 7. Tables

- **Table 1:** every row measured, or "not measured" with the reason.
  - RPM: from E2.
  - TPM / RPD: from headers + P0.7 refill.
  - TPD: docs, or E7 if it runs.
  - In-flight: E4.
  - Returns: the real 429 body/headers from E2/E3.
  - Recovery: E2 / E3 plus the refill rates.
  - Confidence levels are honest.
  - Paths are repo-relative.
- **Table 2a/2b rows:**
  - real: steady E6b; steady E6 (old, n=5); burst E8; and optional daily exhaustion E7;
  - stand-in, the P0.5 set: steady, burst, `burst_cache_on`, sustained overload, per-minute limit halved, daily budget exhausted, `retry_storm`, `retry_disciplined`.
  - Peak queue per P1.2. Cost per 1,000 per P1.3.
- **Table 3:** R1, R2 and R3 on Model A, 15 questions each. Plus one context row, **"R1 pre-fix (judge 120-token cap), old questions, 3 models"**, with a note that it uses the old assistant-proposed questions and is not comparable one-to-one.
- **Formatting:** milliseconds, one decimal for percentages, p99 only when n ≥ 100, n beside each percentile, short cells, long notes *under* the table.

---

## 8. Evaluation protocol

- **8.1 Questions:** Aloo's H1 set only, for every post-fix row.
- **8.2 Grades:**
  - `correct`: matches gold, and every citation supports its sentence;
  - `wrong`: any unsupported or incorrect claim, **including a correct claim with a wrong citation**;
  - `refused_correctly`;
  - `refused_wrongly`.
- **8.3 Who grades:**
  1. Claude Code writes `eval/review_sheet_<run>.csv` (question, answer, cited passage text, gold excerpt) **without** suggested grades.
  2. **Aloo blind-grades at least 15 answers first**, including the 8 × 3 E5-sel answers if time allows, because the model choice depends on them.
  3. Claude Code then adds its suggested grades. Aloo confirms or changes every Model A grade and every E6b/E8 grade.
  4. The grade file records `grader` = `Aloo-blind`, `Aloo-confirmed-assistant-suggestion` or `assistant-only`.
  5. The write-up reports the **blind agreement %** between Aloo and the assistant, and exactly who graded what.
- **8.4 Safety stop:** if Model A's E5-sel `wrong` share exceeds 10%, or the gate's adversarial false-accept rate exceeds 10%, stop before E5-A and propose tightening the gate. Do not loosen refusals further.
- **8.5 Selection bias:** model and setting were chosen on the same questions used to report quality. Say so in one sentence.

---

## 9. Write-up specification (`WRITEUP.md`)

**Length.**
- At most **1,150 words of prose**, counted by `analysis/wordcount.py` excluding tables and table notes.
- Render to PDF (pandoc, 11pt, 2 cm margins): **the prose must fit in 2 pages**, with the four required tables after it under "Results tables (required by the brief)".
- Add one line at the top stating that interpretation: *"Two pages of writing; the required tables follow."*
- Remove the pointers to `additionaldetil.pdf` and to README as overflow. README stays as code documentation. With Aloo's agreement, move `output/pdf/additionaldetil.pdf` to `docs/archive/`.

**Headings, exactly in the brief's order:** 1, 2, 3, 4, 5; "Retrieval: quality versus cost"; "How this library will mislead a naive retriever"; 6, 7, 8; "Your feedback on Vaani"; "How I worked with the assistant".

**Who drafts what.** Claude Code drafts the factual parts of 1–8 and the two extra headings from measured data, in plain, complete sentences. Aloo then edits them into his own voice.

**What each section must contain:**
1. **What I built and why.**
   - The steps: understand → retrieve [tool] → decide [branch] → draft → sentence gate → one repair [loop].
   - The models per step.
   - The one decision.
   - Its measured costs: the provider-TTFT vs user-visible first-token gap, and the gate's false-accept %.
2. **The real service and my stand-in.**
   - Each limit and how it was found.
   - The bucket refill rates derived from headers.
   - Model roles per limit and why (per-model limits, stated openly).
   - Calibration method, n, and p50/p95 side by side.
   - The 2–3 biggest differences.
3. **Results.**
   - Real vs stand-in rows.
   - Quality on the 15 questions, with the blind agreement %.
   - Tokens per answer.
   - **The surprising number:** answers per minute and per day that TPM/TPD actually allow, versus the RPM ceiling.
   - The pre-fix false-refusal rate as the measurement that exposed the decide-step bug. Credit it accurately (§0.3.4).
4. **Where the time goes:** P1.4.
5. **When a limit is hit.** For the per-minute limit and then the per-day limit, state:
   - who is delayed (slack ≥ predicted wait);
   - who gets lite or extractive (slack below the wait but above the extractive p95);
   - who is refused or shed (slack below the extractive p95, or the queue beyond patience);

   with counts from the P0.5 runs. Give one sentence of reason for each rule, and note that tokens per day binds first.
6. **Retries.**
   - The policy in two sentences.
   - `retry_storm` vs `retry_disciplined`: success %, p95, and wasted calls and tokens.
   - The real case of a repair that hit a 429 after earlier steps had already spent tokens.
7. **What the person sees.** The exact reply text from daily exhaustion (E7 if it ran, otherwise the stand-in), explained part by part, with the next-request time from the refill rate.
8. **Unsure, left out, learned.**
   - The least certain assumption (user patience, or stand-in load slowdown) and what breaks if it is wrong. Measure it if possible: rerun overload with patience halved, on the stand-in.
   - Two things deliberately not built, with reason and cost. Candidates: cross-model failover (rejected as limit-dodging); progressive gating (if P2.1 isn't done); a semantic retriever.
   - "New to me" is Aloo's.

**The two extra headings:**
- **Retrieval: quality versus cost.** R1/R2/R3 on Model A (piece size, then re-rank isolated), the choice with numbers, and the under-pressure rule from P1.1.
- **How this library will mislead a naive retriever.** The three mechanisms from P0.6.

**Vaani** and **How I worked with the assistant:** Aloo writes both.
- Claude Code assembles `transcripts/`: the Codex export; this claude.ai conversation and any others; the Claude Code `/export`; and the Google AI disclosure.
- It also assembles `docs/evidence_for_aloo.md`: candidate prompts *verbatim with timestamps*, the D1 discovery timeline (who found it, and how Aloo verified it), and overrule candidates with file references.

---

## 10. Verification gates (all must pass before the final commit)
1. `python -m pytest -q` passes, including the new tests: decide-step parsing/sentinel, adversarial gate, token-bucket stand-in, the submission-CSV builder, peak queue.
2. `python analysis/build_submission_csv.py && python analysis/make_tables.py && python analysis/checks.py` gives zero mismatches.
3. The key grep from CLAUDE.md §1.1 finds nothing, and `grep -rn "/Users/" WRITEUP.md results/generated_tables.md` finds nothing.
4. The prose is 1,150 words or fewer, and the rendered PDF has the prose on 2 pages or fewer.
5. The CTO subagent gives `GO` on the final diff and on `WRITEUP.md`. Its review lists, for every brief heading and every table column, whether it is answered with a number computable from `results.csv`.
6. Aloo confirms that his personal sections, the questions and gold answers, and `OVERRULES.md` are his own work.

Commit in logical commits (fix, data, analysis, write-up). Never rewrite history. Push to `origin/main` only after gate 6.

---

## 11. Timeline (IST, realistic: Aloo sleeps)

| When | Claude Code | Aloo |
| --- | --- | --- |
| 11-12, 20:00–22:00 | P0.1 code + adversarial tests, P0.3, P0.4 v1, P0.6, P0.7 (all zero cost) | H1: write 15 questions + gold |
| 22:00–22:30 | Present DRs 0006–0011 + E0 estimate | H2: one review sitting; `go E0` |
| 22:30–00:00 | E0, E2 ∥ E3 ∥ E4, E5-sel | `go` each; H4 |
| 00:00–00:30 | Review sheets | Blind-grade the E5-sel answers; choose Model A |
| 00:30–02:00 | E5-A ∥ E6b, E8; queue E7 overnight if approved | `go` each, then sleep |
| 11-13, 02:00–08:00 | Calibration v2, P0.5 runs in parallel (about 20 min wall-clock), tables, P1.1–P1.4 | — |
| 08:00–10:00 | Suggested grades; write-up draft (factual parts) | H3: confirm grades |
| 10:00–14:00 | CTO review; fixes; transcripts + evidence file | H5: personal sections, OVERRULES, H6 |
| 14:00–17:00 | Gates 1–5; PDF render | Gate 6; final read |
| 17:00–18:00 | Commit + push | Submit |

---

## 12. Interview readiness (Aloo, 30 minutes on Nov 13)
Be able to explain the following without notes:
- D1 and how you verified it;
- why "decide" is no longer a fragile JSON call;
- why tokens per minute and tokens per day bind before requests;
- how the stand-in was calibrated and where it is least like Groq;
- the ladder rules;
- the retry-storm result;
- what you would change first if the interviewer halves TPM live.

The brief says you will "change your system" in the conversation. Practise one change on the stand-in: edit `standin/config.yaml` to halve TPM, rerun `sustained_overload`, and read off how the ladder's level mix shifts.
