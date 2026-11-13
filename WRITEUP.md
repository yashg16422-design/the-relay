# The Relay

## 1. What I built and why

Five steps: **understand** (a model call names query and paper), **retrieve** (a tool: BM25 within that paper), **decide** (a branch: the drafter may answer `NOT_IN_PAPERS`), **draft** (streamed, every sentence cited) and a **sentence gate** with one repair loop. Quality runs took half of Qwen's daily tokens (101,689 of 200,000), so load runs use gpt-oss-120b, whose 12 real steady answers graded 10 correct.

The decision that shaped everything: **nothing reaches the person unless a retrieved passage supports it; under pressure the answer gets weaker, never less honest.** Costs: the gate holds each draft a median 233 ms (p95 390 ms, n=42) and, being lexical, still accepts 30.2% of 53 written attacks.

## 2. The real service and my stand-in

Limits (Table 1) come from 332 response headers and four probes. Per-minute limits are refilling buckets, not windows: a real 429 said "Limit 8000, Used 5724, Requested 2945. Please try again in 5.0175s", exactly the shortfall divided by 133.3 tokens per second. "Requested" includes the output cap, so a larger cap costs headroom even unused.

The stand-in has the same four buckets, streams, bills tokens drawn from real counts, draws latency per step from a lognormal fitted on 319 real calls, and can inject errors, hangs and slowdown. Its drafts are slowest against real (p50 966 ms against 634 ms, n=105).

## 3. Results

Real: steady (n=12) and burst (n=8); the rest is stand-in. The answer cache barely helped a burst (26.7% answered against 23.3%, 1 hit in 30): the limit turned most repeats away first.

Quality on my 15 questions (Qwen, R1): 40.0% correct, 26.7% wrong, 33.3% correctly refused, 1,378.5 tokens per answer. Every "wrong" is a missing fact, none a false one. I blind-graded the 15 R1 answers and matched the assistant's suggested grades on all 15; R2, R3 and the load rows carry its grades under my rule; an assistant wrote the five unanswerable questions at my request.

The number I could not have guessed: a real answer costs 1,485.2 tokens in 2.25 calls, so tokens allow 5.4 answers a minute and about 135 a day, where requests would allow 13.3 and 444. Before the rework it answered 11 of 107: a truncated judge reply was read as "not answerable" (a review I commissioned found it).

## 4. Where the time goes

Rule: the p95 total-time request of the real burst. It took 7,501.0 ms: 5.4 ms waiting, 1,763.1 ms working, 5,731.8 ms retrying (76.4%): our limiter held its draft for the bucket. All of it came before the first token.

## 5. When a limit is hit

Each request gets the richest level whose predicted wait plus p95 fits its patience; running requests go first.

- **Per minute** (overload, n=275): 56 got a full answer, 25 one without repair; 93 were held a median 4.2 s first; 189 got passages with a notice (the wait would outlast them); 5 got nothing.
- **Per day** (35 requests): 9 answered, then 26 got passages. Nobody was delayed: no wait would help.

## Retrieval: quality versus cost

R1 to R2 changes piece size; R2 to R3 adds re-ranking (Table 3). Correct: 40.0%, 33.3%, 40.0%; tokens per answer: 1,378.5, 2,070.4, 2,271.7. That is noise at n=15 (a since-fixed gate bug also cost R2 and R3 one answer each), so I keep R1 at 61% of R3's tokens. Near the limit the repair goes (p95 1,367 tokens against 2,267); past it, passages are sent as-is.

## How this library will mislead a naive retriever

1. **Cross-paper collision.** "How many GPUs was Moshi trained on?" returns the Transformer's hardware section (p. 7). Fixed: search only the named paper.
2. **Chat transcripts.** "What does the Moshi paper say about SoundStream?" returns an appendix dialogue (p. 63). Fixed: drop question words.
3. **Shredded tables.** "What bits-per-byte does H-Net (2-stage) XL reach?" returns a plot's axis labels (p. 13). Not fixed.

## 6. Retries

Retry only 429s, 5xx and timeouts; wait `retry-after`; never past the deadline; three attempts; a shared budget. Made worse: on the same overload, retrying at once failed faster (total p95 2,490 ms against 7,145 ms) but sent 2.8 times the calls (367 against 133) for the same answers (27 of 105 against 29); wasted calls spend request budget.

## 7. What the person sees

"Today's model budget for this assistant is used up, so I cannot write you a checked answer right now. The budget refills gradually rather than at midnight: the next full answer can be served in about 9 minutes." Two cited passages follow, labelled "passages, not an answer". (Stand-in; the real daily limit was never reached.)

## 8. What I'm unsure about, what I left out, what I learned

Least certain: patience, assumed at a median of 8 s. Halving it left answers unchanged (37 of 105 against 35), but 14 gave up against 5.

Not built: failover to another model (it dodges a per-model limit); releasing sentences as each passes the gate (a later one can fail).

New to me: I started thinking a rate limit was a simple gate: a request is accepted or rejected, and a 429 means no useful work happened. But one answer is a chain of steps, and a later repair call can hit a limit after earlier calls have already used tokens. And elapsed time on a failed request is not time to first token.

## Your feedback on Vaani

What stood out was the "Teacher AI" build flow: the agent asks clarifying questions, assembles the prompt and tools, then shows a change diff for approval. I could not tell how the readiness score is calculated, or how a team decides which past-call details "Memories" should keep. I would explain both, concretely, near the demo.

## How I worked with the assistant

History: `transcripts/`. Three prompts, by item number:

1. Item 157: "evaluate answer quality, latency, provider limits, and cost; compare at least two options; and choose the smallest experiment that can distinguish them." It made the competing goals visible first.
2. Item 116: "Please run the real R2 evaluations for Day 1 and Day 2, then proceed to R3 if the budget and approval gates permit. Keep failures in the ledger…" Same questions across settings, failures kept.
3. Item 54: "I approve decision record 0004. Please proceed with its trace-audit change and preserve the agreed results CSV contract." Every answer became traceable by request id.

**Got wrong.** The first cost summary missed spend from attempts that later failed with a 429 during repair. A review I requested recomputed it from traces.

**Overruled.** After rate limits interrupted the evaluation, I did not accept a stand-in-only comparison: I had R2 and R3 run with real calls, budgeted, 429s visible.

**Mine.** What to measure, which evidence to trust, and where to stop; I did not type every line.

## Results tables (required by the brief)

<!-- TABLES START -->

## Table 1: Limits you found in the real service

| Limit | Value you found | How you found it | Confidence |
| --- | --- | --- | --- |
| Requests per minute | 30 | 30 calls accepted in 13.8 s, call 31 refused with 429 "Limit 30, Used 30"; `results/raw/E2-20261112T185736Z-openai_gpt-oss-20b.json` (openai/gpt-oss-20b). Matches the docs page. | high for this model; docs only for the other two |
| Tokens per minute | 8,000 | `x-ratelimit-limit-tokens` on 332 real responses; bucket refills 133.3 tokens/s (reset header = used ÷ rate, median error 0.5 ms); `results/raw/limit_refill_analysis.json` | high |
| Requests per day | 1,000 | `x-ratelimit-limit-requests` on 332 real responses; refills one request per 86.4 s; never reached | medium |
| Tokens per day | 200,000 (docs only) | `results/raw/groq-public-rate-limits.json` (Groq docs, read 2026-11-11). No header reports it and it was never reached. | low |
| Requests in flight at once | at least 20; no separate limit found | single-call median 446 ms (n=5); 10 at once: 10 accepted, 0 refused, p50 633 ms, p95 725 ms; 20 at once: 20 accepted, 0 refused, p50 518 ms, p95 637 ms; `results/raw/E4-20261112T190338Z-openai_gpt-oss-20b.json` (openai/gpt-oss-20b) | medium (a lower bound) |
| What it returns when a limit is hit | HTTP 429 with `retry-after` and the `x-ratelimit-*` headers | RPM: HTTP 429, `retry-after: 2`, body: "Rate limit reached for model `openai/gpt-oss-20b` in organization `org_REDACTED` service tier `on_demand` on requests per minute (RPM): Limit 30, Used 30, Requested 1. Please try again in 2s."<br>TPM: HTTP 429, `retry-after: 6`, body: "Rate limit reached for model `openai/gpt-oss-20b` in organization `org_REDACTED` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 5724, Requested 2945. Please try again in 5.0175s." (`results/raw/E3-20261112T190331Z-openai_gpt-oss-20b.json` (openai/gpt-oss-20b)) | high |
| Time until it recovers | seconds, in proportion to the size of the refused request | RPM: first success 2.18 s after the 429 (probed every 2 s), so it refills about one request per 2 s<br>TPM: `retry-after` between 1 and 16 s on 7 real 429s; it is the time for the bucket to refill enough for the refused request, not a full reset | high for per-minute limits; daily limits not observed |

<!-- BEGIN GENERATED TABLES -->

## Table 2a: Latency, one row per load scenario

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| --- | --- | ---: | ---: | ---: | --- | --- |
| steady | real | 12 | 474.0 | 1.5 | 1469.7 / 2436.5 / not measured (n=12) | 1469.7 / 2436.5 / not measured (n=12) |
| burst | real | 8 | 7.5 | all at once | 1868.1 / 7501.0 / not measured (n=5) | 1146.2 / 7501.0 / not measured (n=8) |
| steady, before the rework (n=5) | real | 5 | 121.8 | 2.5 | 1517.7 / 3174.9 / not measured (n=5) | 1517.7 / 3174.9 / not measured (n=5) |
| steady | stand-in | 57 | 857.1 | 4.0 | 1901.3 / 3353.7 / not measured (n=51) | 1898.2 / 4309.8 / not measured (n=57) |
| burst | stand-in | 30 | 19.6 | 186.4 | 1942.2 / 10732.4 / not measured (n=7) | 9.0 / 10732.4 / not measured (n=30) |
| burst, answer cache on | stand-in | 30 | 19.6 | 186.4 | 1939.6 / 10979.8 / not measured (n=8) | 9.9 / 6232.9 / not measured (n=30) |
| sustained overload | stand-in | 275 | 909.7 | 18.4 | 4792.0 / 13247.0 / not measured (n=81) | 33.3 / 10829.4 / 17117.8 (n=275) |
| per-minute limit halved | stand-in | 62 | 725.7 | 5.2 | 2203.2 / 9324.4 / not measured (n=43) | 1846.4 / 9324.4 / not measured (n=62) |
| daily budget exhausted | stand-in | 35 | 476.4 | 4.4 | 1547.6 / 2522.4 / not measured (n=9) | 14.4 / 2522.4 / not measured (n=35) |
| overload, retry policy | stand-in | 105 | 358.3 | 17.6 | 1904.1 / 6396.3 / not measured (n=29) | 1059.4 / 7144.9 / 12606.1 (n=105) |
| overload, naive retries | stand-in | 105 | 359.1 | 17.6 | 1584.4 / 3611.8 / not measured (n=27) | 1002.2 / 2489.9 / 3685.2 (n=105) |

## Table 2b: Outcomes, same rows

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| steady (real) | 100.0% | 0.0% | 0.0% | 0.00 | 2 | 1485.2 | $0.32 |
| burst (real) | 62.5% | 37.5% | 0.0% | 0.25 | 8 | 1210.8 | $0.26 |
| steady, before the rework (n=5) (real) | 100.0% | 0.0% | 0.0% | 0.00 | 1 | 1715.6 | $0.20 |
| steady (stand-in) | 89.5% | 3.5% | 7.0% | 0.00 | 3 | 1412.6 | $0.33 |
| burst (stand-in) | 23.3% | 76.7% | 0.0% | 0.03 | 5 | 1316.1 | $0.32 |
| burst, answer cache on (stand-in) | 26.7% | 73.3% | 0.0% | 0.00 | 5 | 1167.6 | $0.28 |
| sustained overload (stand-in) | 29.5% | 68.4% | 2.2% | 0.07 | 3 | 1500.3 | $0.35 |
| per-minute limit halved (stand-in) | 69.4% | 29.0% | 1.6% | 0.10 | 3 | 1390.4 | $0.33 |
| daily budget exhausted (stand-in) | 25.7% | 71.4% | 2.9% | 0.00 | 3 | 1605.6 | $0.38 |
| overload, retry policy (stand-in) | 27.6% | 72.4% | 0.0% | 0.19 | 6 | 1877.8 | $0.47 |
| overload, naive retries (stand-in) | 25.7% | 72.4% | 1.9% | 1.50 | 7 | 1946.2 | $0.47 |

## Table 3: Retrieval settings

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| R1: 900 characters, 3 pieces, no re-rank | 15 | 40.0% | 26.7% | 33.3% | 1378.5 | 1341.1 / 10109.0 (n=15) | 1341.1 / 10109.0 (n=15) |
| R2: 1,600 characters, 4 pieces, no re-rank | 15 | 33.3% | 26.7% | 33.3% | 2070.4 | 1137.6 / 9167.2 (n=15) | 1137.6 / 9167.2 (n=15) |
| R3 (redefined): 1,600 characters, 4 pieces, re-rank | 15 | 40.0% | 20.0% | 33.3% | 2271.7 | 1592.9 / 9968.1 (n=15) | 1592.9 / 9968.1 (n=15) |
| R1: 900 characters, 3 pieces, no re-rank — before the rework | 46 | 6.7% | 0.0% | 20.0% | 1278.8 | 1284.9 / 1723.2 (n=45) | 1282.0 / 1723.2 (n=46) |
| R2: 1,600 characters, 4 pieces, no re-rank — before the rework | 46 | 2.2% | 0.0% | 20.0% | 2169.0 | 1552.2 / 9289.5 (n=45) | 1542.1 / 9289.5 (n=46) |
| old R3: 2,600 characters, 6 pieces, re-rank — before the rework | 49 | 15.9% | 0.0% | 20.5% | 3479.9 | 1993.3 / 3961.0 (n=44) | 1993.3 / 3961.0 (n=49) |

### Notes under the tables

- Every cell is computed from `results/results.csv` alone by `analysis/tables.py`. Percentiles are nearest-rank; n is printed beside them; p99 needs n ≥ 100.
- **First token** is the first character of a checked answer reaching the person. It is empty when no answer was delivered, so its n can be smaller than Requests sent.
- **Duration** runs from the first arrival to the last completion. **Offered rate** is requests divided by the time between the first and last arrival.
- **Peak queue length** is the largest number of requests in the system at the same moment, found by sweeping over each row's `start_time` to `start_time + total_ms`. It counts requests being worked on as well as those waiting.
- **Tokens per answer** is all tokens the scenario used (including work on requests that ended rate-limited) divided by the requests answered. **Cost** applies Groq's published paid price (https://console.groq.com/docs/models, read 2026-11-11) to those tokens: openai/gpt-oss-120b for load rows, openai/gpt-oss-20b for the pre-rework steady row. It is hypothetical: the free tier bills nothing, and stand-in tokens are simulated.
- **Succeeded** is status `ok`: a checked answer or an honest "the papers do not say" was delivered. It is not the share of correct answers; real rows carry grades in `answer_grade`.
- Rate-limited rows include requests that were sent retrieved passages instead of a model answer. Rows with status `error` are in Requests sent but in none of the three outcome columns.
- Stand-in rows have no answer grade: stand-in replies are synthetic text.
- Table 3 percentages are of graded answers. Wrong % counts only answers graded `wrong`.
- R1: 900 characters, 3 pieces, no re-rank: graded 15 of 15; wrongly refused 0.0%; not answered because of a limit or error 0; models: qwen/qwen3.8-27b.
- R2: 1,600 characters, 4 pieces, no re-rank: graded 15 of 15; wrongly refused 6.7%; not answered because of a limit or error 0; models: qwen/qwen3.8-27b.
- R3 (redefined): 1,600 characters, 4 pieces, re-rank: graded 15 of 15; wrongly refused 6.7%; not answered because of a limit or error 0; models: qwen/qwen3.8-27b.
- R1: 900 characters, 3 pieces, no re-rank — before the rework: graded 45 of 46; wrongly refused 73.3%; not answered because of a limit or error 1; models: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b.
- R2: 1,600 characters, 4 pieces, no re-rank — before the rework: graded 45 of 46; wrongly refused 77.8%; not answered because of a limit or error 1; models: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b.
- old R3: 2,600 characters, 6 pieces, re-rank — before the rework: graded 44 of 49; wrongly refused 63.6%; not answered because of a limit or error 5; models: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b.

<!-- END GENERATED TABLES -->

<!-- TABLES END -->
