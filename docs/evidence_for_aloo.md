# Evidence for Aloo's own sections

Collected by Claude Code on 2026-11-13. This file holds **facts and verbatim quotes only**. It does not choose your three prompts, and it does not word your Vaani feedback, "new to me", "distinctly mine", "what it got wrong" or "where I overruled it". Those are yours to write.

Transcript item numbers refer to `transcripts/relay_transcript.json` (Codex Desktop export, 181 items, 35 user turns). The export's `created_at` values are short counters (1794234 to 1794421), not readable timestamps; item numbers are the stable reference.

## 1. Things you should know before you write

- **`docs/OVERRULES.md` is empty.** The old write-up claims an overrule. If you claim one, enter it there with its evidence.
- **`docs/reflection_draft.md` was drafted by the assistant** (transcript item 162 delivers it as "sample wording"). It says its three prompts are "cleaned-up versions". The brief asks for the real history, so quote prompts exactly as they are in the transcript.
- **Transcript items 159 and 163** are you asking the assistant to present reasoning as yours. A reviewer who reads the transcript will see that. Decide how you want to describe it.
- **Missing histories.** The repo has `HANDOVER_CURSOR.md` and `CURSOR_COMPLETION_PROMPT.md`, so Cursor was used; its history is not in `transcripts/`. The claude.ai review session and this Claude Code session are not there yet either (handover task H6).
- **`transcripts/README.md`** already discloses the Google Search AI help with prompt wording.
- **Questions:** `eval/questions_aloo.yaml` says T, M and H questions are yours and U1 to U5 were written by the claude.ai session and accepted by you. The write-up must say so.

## 2. Candidate prompts, verbatim (you choose; these are not ranked)

- **Item 9:** "I want to maximize the assignment score and technical depth, but keep additions defensible. If work beyond the brief improves quality, reliability, or evidence, include it and explain the trade-off. Please proceed."
- **Item 12:** "Please run the tests and build on my Mac, and arrange independent CTO reviews of the implementation using agents or subagents. I want the review to challenge the design, not just confirm that it works."
- **Item 47:** "Please audit what remains against the brief. Since the implementation seems close, propose one additional idea that would improve the system in a measurable and defensible way, even if it goes beyond the minimum requirements."
- **Item 141:** "A failed answer may be an expected outcome of the evaluation rather than a system error. Please explain how it should be graded and complete the remaining work that does not require changing that result."
- **Item 166 (first paragraph):** "I found a reproducibility gap in Table 3. Its quality metrics are not stored in results.csv: all 141 paid rows, including the 134 graded answers, have blank answer_grade values. The grades are in eval/human_grades.csv and are joined only when the tables are built. The brief requires reviewers to recompute every figure from results.csv; from that file alone, correct, wrong, and correctly refused are unmeasured, so the write-up cannot be reproduced. The review you accepted leaves this gap open."
- **Item 170:** "If the grade merge is the remaining reproducibility fix, what is the concrete risk in approving it? I believe the other work is complete; please inspect the current state without changing files and explain the trade-off."
- **This Claude Code session, 2026-11-12 (your own words after the pasted brief):** "bro tell me whenever u are ready and go on with the work also you are also allowed for proposing and working on ideas of ur own as long as the maximise the points okay and authority to make paid calls too just use them wisely"
- **This session, 2026-11-13:** "okay to download but use it wisely and only it adds real value" (about the entailment model, which was then measured and rejected).

## 3. The judge-cap finding: who found what

- Found by the claude.ai review session you commissioned to grade the submission (stated in `HANDOVER_CLAUDE_CODE.md`, D1). Not found by Codex, Cursor or Claude Code.
- Recomputed independently by the CTO reviewer subagent on 2026-11-12: 96 of 107 answerable questions wrongly refused; 71 of those 96 had judge `tokens_out` ≥ 118 against a cap of 120; all 11 correct answers below it; 17 of 27 correct refusals at the cap.
- Seen directly on 2026-11-12 18:57 UTC in probe E0: a gpt-oss reply cut off at 8 tokens came back as **empty text** with `finish_reason: length` (`results/raw/E0-20261112T185702Z-openai_gpt-oss-20b.json`).
- Your own check (handover task H4) is not recorded anywhere yet. If you did it, say what you opened.

## 4. Things the assistants got wrong, and who caught each one

| What was wrong | Who caught it | Evidence |
| --- | --- | --- |
| Judge capped at 120 tokens; unreadable reply treated as "not answerable" (Codex) | claude.ai review session you commissioned | handover D1; CTO recompute |
| Load runs measured the answer cache: 4 of 75 overload requests reached the model (Codex) | claude.ai review session | handover D2 |
| Table 3 grades not in `results.csv` (Codex) | you, transcript item 166 | item 166 |
| Bare word "Transformer" scoped Moshi and H-Net questions to the wrong paper (Claude Code, 2026-11-12) | CTO reviewer subagent | record 0006, CTO review section |
| Paid runners ignored the new `decide_mode` config (Claude Code) | CTO reviewer subagent | record 0006 |
| Limiter could not parse Groq's `667ms` reset values, and reserved 1 token per byte (Codex code) | the real burst E8: 0 of 8 answered | `results/raw/runs/E8-20261112T190347Z/`, record 0010 |
| A request with little patience got one unchecked extracted sentence marked `ok` (Codex code) | the real steady run E6b, row 5 | `results/raw/runs/E6b-20261112T185754Z/` |
| Handover expected naive retries to lower success under overload | the first stand-in batch: naive retries answered slightly more, at many more calls | `results/raw/superseded/L1-20261113/` |
| Probes E3 and E4 under-budgeted, so the project's own guard stopped calls (Claude Code) | the probe output itself | first E3 and E4 files in `results/raw/` |

## 5. Decisions that were put to you, and what the record shows

- Records 0006 to 0011 are all still `proposed`. You have not accepted or rejected any of them in writing. Accepting, changing or rejecting each one, in your own words in its "Aloo's call" section, is direct evidence of you directing the work.
- Places where the assistant's recommendation and another view differ, which you could decide either way:
  - Decide step: Claude Code recommends dropping the judge call (B); the CTO reviewer preferred keeping a hardened judge (A). E0 then showed gpt-oss-20b ignoring the abstain instruction.
  - Results file: Claude Code recommends keeping every row with labels; the CTO reviewer suggested keeping only the rows the tables use.
  - Entailment model for the gate: proposed by Claude Code, allowed by you, rejected on measurement (7 of 20 faithful claims wrongly rejected).
  - Dropping damaged tables from retrieval: recall-neutral, left off pending your call (record 0011).
- The 2026-11-12 standing permission for paid calls is yours (quoted in section 2). Every real run made under it is listed with its token use in `budget_ledger.csv`.

## 6. Numbers you may want (all recomputable)

- Old pipeline: 11 of 107 answerable questions answered.
- Requests per minute: 30 accepted, the 31st refused; recovered after 2.18 s (`results/raw/E2-20261112T185736Z-openai_gpt-oss-20b.json`).
- Tokens per minute: the real 429 said "Limit 8000, Used 5724, Requested 2945. Please try again in 5.0175s"; (5,724 + 2,945 − 8,000) ÷ 133.3 = 5.02 s (`results/raw/E3-20261112T190331Z-openai_gpt-oss-20b.json`).
- Groq counts input tokens plus the output **cap** at admission ("Requested 2945" for a 16-token cap and 2,929 input tokens).
- Gate: old gate accepted 35 of 51 written attacks, strict gate 15 of 51, 0 of 20 faithful claims rejected (`results/gate_adversarial_report.json`).
- 25 of 35 extracted tables lost their decimal points (`results/naive_retriever_report.json`).
