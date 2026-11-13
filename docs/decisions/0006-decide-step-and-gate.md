# 0006 — How "decide" is made, and what the sentence gate checks

- Status: proposed            <!-- only Aloo changes this to accepted / rejected -->
- Date: 2026-11-12
- Phase / gate: D (rework before the paid quality runs)

## Problem
The pipeline refused 96 of 107 answerable questions in the 134 graded real answers (11 correct). The brief wants every reply good and nobody turned away, and it also forbids a wrong answer. The old pipeline bought "never wrong" by almost never answering.

Two separate defects are involved.

1. **Decide.** The judge call had a 120-token cap, and `relay/pipeline.py` turned any reply it could not parse into "not answerable". Nothing recorded `finish_reason` or the judge's text, so a cut-off reply looked the same as a real "no".
2. **Gate.** The sentence gate was purely lexical. It did not check which paper a citation came from, it skipped numbers with a unit attached ("160ms", "7B"), and it passed negated claims.

## Options

**For decide**

1. Do nothing. Keeps a 10.3% answer rate on answerable questions.
2. **A — keep the judge call, make it robust.** Raise its cap, read the reply in three states (yes / no / uncertain), retry once when it is unreadable, and let "uncertain" continue to the drafter. Only an explicit `"answerable": false` refuses.
3. **B — drop the judge call.** A zero-token retrieval check (best BM25 score against a threshold) refuses early; otherwise the drafter either answers with citations or replies exactly `NOT_IN_PAPERS`, which becomes the honest refusal.

Both A and B are implemented behind `decide_mode: llm_judge | precheck_abstain` in `config/pipeline.yaml`. The config still says `llm_judge`, with the old caps, until Aloo decides.

**For the gate**

1. Leave it lexical as it was.
2. **Strict lexical gate (implemented, `CitationGate(strict=True)`):** paper-scope check, unit-suffixed numbers, thousands commas, a negation check, a number-context check, line-break hyphen repair, and the paper's own model name allowed as a subject. `strict=False` reproduces the old gate exactly for comparison.
3. **Strict gate plus a local entailment model** (not implemented): a small NLI cross-encoder on CPU scores each sentence against its cited passage. Zero provider tokens. It needs a one-time model download, which needs Aloo's permission, and adds CPU time before the first token (not measured).

**For paper scope** (new, applies to both decide modes): the question is matched against the three paper names and the names of their parts (Moshi, Mimi, Helium, H-Net, Transformer, arXiv ids). If the question names a paper, retrieval and citations are restricted to it. If it names none, the understand step's guess is used; if that is empty, there is no restriction.

## Evidence
All numbers below were recomputed on 2026-11-12 from files in the repo.

- 96 of 107 answerable questions wrongly refused; 71 of those 96 had judge `tokens_out` ≥ 118 against a cap of 120; all 11 correct answers were below that; 17 of 27 correct refusals were also at the cap (CTO review recomputed these from `results/raw/model_selection/*/traces/`).
- The cap is a strong correlate, not a proven cause: Qwen has 27 wrong refusals with only 2 at the cap, and no judge text was ever saved. The new trace fields (`finish_reason`, `parse_state`, `raw_text`) exist to settle this in experiment E0.
- **Gate, measured at zero cost** by `analysis/gate_adversarial.py` on `eval/gate_adversarial_cases.yaml` (53 attacks, 23 faithful claims, all citing real R1 chunks; output in `results/gate_adversarial_report.json`):

  | Gate | Attacks accepted | Faithful claims rejected |
  | --- | --- | --- |
  | old (`strict=False`) | 36 of 53 (67.9%) | 8 of 23 (34.8%) |
  | strict | 16 of 53 (30.2%) | 0 of 23 (0.0%) |

  By attack type, the strict gate accepts 0 of 10 wrong-paper, 0 of 8 wrong-number, 0 of 5 unit-swap, 0 of 4 structural, 3 of 10 negation, 7 of 10 number-in-another-context and 6 of 6 swapped-relation cases. Three of the faithful cases are real Qwen drafts from 2026-11-12 that an earlier version of the strict gate wrongly rejected; after those fixes, `analysis/replay_gate.py` passes all 39 real drafts saved from the final quality runs.
- **Caveats on those figures.**
  - 43 attacks and 15 faithful cases were written by the assistant, the same author as the strict gate; 8 attacks were written by the CTO reviewer, and 5 faithful paraphrases were added after its review found two short true answers wrongly rejected. The rate is not an unbiased estimate.
  - The 10 wrong-paper cases are stopped by a set-membership check that assumes the paper was detected correctly. Without them the strict gate accepts 16 of 43 (37.2%).
  - "0 of 20 faithful rejected" is on written cases. **The strict gate has not been measured on real drafts.** Traces now store the raw draft (`raw_answer`) so both gates can be replayed offline.
- **Already live by default while this record is proposed:** the strict gate (`gate_strict: true`), paper scoping (`scope_to_named_papers: true`), the new understand and draft prompts, and three-state judge parsing. Each has a switch in `config/pipeline.yaml` except the prompts. Only `decide_mode` and the token caps are unchanged. No run has been made with the new code.
- **Gate option 3 was measured on 2026-11-13 and is not recommended.** `cross-encoder/nli-deberta-v3-small` on CPU (41 ms per sentence, 109 s first load), run on the same 71 cases after the strict gate: requiring "not a contradiction" cut accepted attacks from 15 to 5 of 51, but rejected 7 of 20 faithful claims (35.0%), including "The models were trained on one machine with 8 NVIDIA P100 GPUs." against the passage that says exactly that. Requiring entailment ≥ 0.5 rejected 10 of 20. That would rebuild the refusal problem this record exists to fix. Not adopted; the model is not a dependency.
- **E0, real calls, 2026-11-12 18:56 UTC, one probe per model** (`results/raw/E0-20261112T1856*.json`, `E0-20261112T1857*.json`):
  - `temperature: 0`, `reasoning_effort: low`, `response_format: json_object` and `finish_reason` were accepted or returned by all three models.
  - A reply cut off at 8 tokens came back from both gpt-oss models as **empty text** with `finish_reason: length` (the reasoning used the tokens). This is the mechanism behind the old refusals, now seen directly.
  - Trap question "How many GPUs was Moshi trained on?" with only the Transformer hardware passage as evidence: Qwen and gpt-oss-120b replied `NOT_IN_PAPERS`; **gpt-oss-20b ignored the instruction and wrote "Moshi was trained on 8 GPUs" citing the Transformer passage.** All three, asked as a judge in JSON mode, said not answerable.
  - n is 1 per model. It is enough to say gpt-oss-20b cannot be trusted with option B, not enough to say the other two always comply.
- Token cost per answer for A versus B is **not measured** on the new code. The handover's estimate from the old traces is about 3.3k tokens (A) versus about 2.0k (B) at R1.
- `precheck_min_bm25` is **not tuned**; it is 0 (disabled). It can only be tuned on Aloo's own questions, and tuning it on the same questions that are scored is a bias the write-up must state.

## Decision (builder's recommendation)
Decide: **B (`precheck_abstain`)**, with the BM25 threshold left at 0 unless Aloo's questions show a clean separation. With the threshold at 0, B is simply "the drafter may say NOT_IN_PAPERS", which removes the fragile JSON step and one evidence-sized call. Gate: **option 2 (strict)**. Option 3 was tried and rejected on its measured false-reject rate (see Evidence).

The CTO reviewer disagrees in part: it prefers a minimal A (cap 512, unreadable goes to draft, sentinel in the draft prompt) because B tunes a threshold on scored questions. With the threshold at 0 that objection does not apply, and B is cheaper per answer; with a tuned threshold it does apply.

**Consequence of E0 for model roles:** quality runs use Qwen and real load runs use gpt-oss-120b (both abstained); gpt-oss-20b is used only for the limit probes. With B, the wrong-paper answer gpt-oss-20b gave is stopped by two later checks (scoped retrieval would not have fetched that passage; the gate rejects a citation from a paper the question is not about), but B should not rest on those alone.

## What it costs
- **B:** the drafter decides answerability, so there is no second opinion before drafting; a model that ignores the sentinel instruction will guess, and only the gate stands in its way. Refusals for unanswerable questions cost a full draft-sized call instead of a judge-sized one.
- **A:** one extra evidence-sized call per answer (about 1,000 input tokens at R1), and a second one whenever the first reply cannot be read. Under the old 120-token cap 71 of 96 wrong refusals were at the cap, so without a higher cap A would often pay two judge calls plus the draft. This matters because tokens per day is the binding limit.
- **Strict gate:** more rejections of loosely worded but true sentences are possible (0 of 15 on the written faithful cases; not measured on real drafts). Each rejection costs one repair call or a refusal.
- **Paper scope:** a question that names one paper but needs another is refused. A wrong guess by the understand step, when the question names no paper, also leads to a refusal.
- **Honest limit:** 30.2% of written attacks still pass. The gate cannot tell which fact a number belongs to, or who did what to whom.
- Trace contract change: spans gain `finish_reason`, `parse_state`, `raw_text`; traces gain `decide`. `results.csv` columns are unchanged.

## What would change our mind
- E0 shows the models ignore `NOT_IN_PAPERS` (answer anyway on unanswerable probes): choose A.
- On the E5-sel answers, `wrong` exceeds 10%: stop and tighten (handover §8.4); do not loosen refusals.
- The strict gate rejects more than 1 in 5 drafts that Aloo grades as correct: relax the number-context rule first.
- An entailment model cuts accepted attacks below 10% with no faithful claim rejected: adopt option 3.

## CTO review
2026-11-12, on the first diff: `GO WITH CHANGES`. Blocking: (1) the bare word "Transformer" scoped Moshi and H-Net questions to the wrong paper; (2) the paid runners ignored the new config keys; (3) this record omitted A's retry cost, the caveats on the gate figures, and that the strict gate was live by default. All three are fixed above and in the code; the reviewer's eight extra attack cases are in the case file. Re-review: 2026-11-13, final submission review of the whole tree: `GO WITH CHANGES`, projected 68/100 as it stood and about 82 with its fixes. Builder-side fixes it asked for are applied (two prose figures corrected, M4 regraded, disclaimer rule tightened, dead limiter branch removed). Still open and Aloo's: this record's acceptance, the personal sections, blind grading, missing transcripts, `docs/reflection_draft.md`.

## Aloo's call
