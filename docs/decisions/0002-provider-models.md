# 0002 — Provider models by pipeline step

- Status: accepted
- Date: 2026-11-10
- Phase / gate: A

## Problem

The pipeline has several model steps, but the assignment expects us to learn the free-tier service limits and compare answer quality. A model that is fast on a one-token probe may still be poor at producing grounded, cited answers.

## Options

1. Use one model for every step to simplify the system and make results easy to attribute.
2. Use a smaller model for query understanding and answerability, and a larger model for drafting, provided the quality tests show that this split is safe.
3. Keep model selection unresolved until a same-question, human-graded comparison is available.

## Evidence

- Groq model discovery on 2026-11-09 returned 11 active model IDs. The saved lists are `results/raw/20261109T181307404564Z-phase0-model-discovery.json` and `results/raw/20261109T180801131963Z-phase0-model-discovery.json`.
- Three candidate IDs were verified available: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and `qwen/qwen3.8-27b`.
- The latest bounded response probes requested exactly `OK` with a 64-token completion ceiling. All returned HTTP 200 and visible text `OK`: 120B used 45 output tokens (35 reasoning) and took 685.8 ms (`results/raw/20261109T181344033117Z-phase0-openai_gpt-oss-120b.json`; `budget_ledger.csv:20`); 20B used 62 output tokens (52 reasoning) and took 471.4 ms (`results/raw/20261109T181419604694Z-phase0-openai_gpt-oss-20b.json`; `budget_ledger.csv:21`); Qwen used 2 output tokens and took 316.0 ms (`results/raw/20261109T181455728498Z-phase0-qwen_qwen3.8-27b.json`; `budget_ledger.csv:22`). These are single observations, not reliable latency distributions or quality scores.
- The earlier one-token responses for the two GPT-OSS models had no visible answer. That probe was too small to compare; it has been corrected to a 64-token cap.
- Successful responses exposed `x-ratelimit-limit-requests: 1000` and `x-ratelimit-limit-tokens: 8000`, with remaining values. Under the project's documented header interpretation, these are requests per day and tokens per minute for this account at that time. Account limits can change.
- Groq's public [Rate Limits documentation](https://console.groq.com/docs/rate-limits), checked 2026-11-10, lists the free-plan summary for these three candidate models as 30 RPM, 1,000 RPD, 8,000 TPM, and 200,000 TPD. The successful responses agree with the documented RPD and TPM values. The account-specific Limits page was not accessed, so treat the public table as a summary and the response headers as the observed account values; the dashboard is the authority for current exact limits.
- When this record was first written, no question-answer quality comparison had been run. The 2026-11-11 update below is the later graded comparison. It still does not assign one model to each step.
- Update 2026-11-11: Aloo accepted `eval/gold_answers.md` as the key. The 134 successful E5 grades now live in `eval/human_grades.csv` and in the matching `results.csv` cells. That comparison still does not assign one model to each pipeline step.

## Decision (builder's recommendation)

Recommend option 3 for now. Keep all three models as candidates; do not assign production pipeline steps based on probe latency alone. Compare the same selected questions and retrieval evidence across candidates, then choose a per-step mapping from answer correctness/citation quality, output tokens, latency, and observed limits. Keep citation validation deterministic unless a measured benefit justifies a model call for it.

## What it costs

This delays a final model mapping and requires a small, approved set of real requests plus human grading. Using more than one model can increase prompt complexity and make failures harder to attribute. Free-tier limits are shared across this project and any other use of the account.

## What would change our mind

Accept option 1 or 2 if a same-question comparison shows a clear quality/latency advantage while staying within the measured account limits. Revisit the candidates if the models disappear from `/models`, fail the citation gate, or return materially different quality.

## CTO review

Pending review.

## Aloo's call

Accepted by Aloo on 2026-11-11: "i accept descision record 0002". This accepts option 3. The three verified model ids stay candidates until a graded comparison chooses a per-step mapping. In the same message Aloo accepted `eval/gold_answers.md` as the evaluation key. The later grades of the saved outputs do not by themselves pick a winning model for each step.
