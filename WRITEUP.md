# The Relay

*Two pages of writing; the required tables follow, generated from `results/results.csv`.*

## 1. What I built and why

Five steps: **understand** (a model call names the query and the paper), **retrieve** (a tool: BM25 within that paper), **decide** (a branch: the drafter may answer `NOT_IN_PAPERS`), **draft** (streamed, every sentence cited) and a **sentence gate** with one repair loop. Qwen runs quality, gpt-oss-120b load; gpt-oss-20b only probes limits (it ignored the abstain instruction on a trap question).

The decision that shaped everything: **nothing reaches the person unless a retrieved passage supports it; under pressure the answer gets weaker, never less honest.** It costs first-token time: the gate holds the whole draft a median 233 ms (p95 390 ms, n=42 answered drafts). And the gate is lexical: it accepts 30.2% of 53 written attacks.

## 2. The real service and my stand-in

Limits (Table 1) come from headers on 332 responses and four probes. Per-minute limits are refilling buckets, not windows: a real 429 said "Limit 8000, Used 5724, Requested 2945. Please try again in 5.0175s", exactly the shortfall divided by 133.3 tokens per second. "Requested" includes the output cap, so a larger cap costs headroom even unused. The pipeline never fails over between models.

The stand-in has the same four buckets, streams, bills tokens drawn from real counts, draws latency per step from a lognormal fitted on 319 real calls, and can inject errors, hangs and slowdown. Its understand p50 is +10.5% against real. It differs most in drafts (p50 966 ms against 634 ms on 105 later real drafts); tokens per day is docs-only.

## 3. Results

Real: steady (n=12) and burst (n=8); the rest is stand-in. Percentiles: nearest-rank p50, p95, and p99 when n ≥ 100.

Quality on my 15 questions (Qwen, R1): 40.0% correct, 26.7% wrong, 33.3% correctly refused, 1,378.5 tokens per answer. Every "wrong" is a missing fact, none a false one; all five unanswerable questions were refused. Grades are the assistant's suggestions under my rule, not yet graded blind by me; an assistant wrote the five unanswerable questions at my request.

The number I could not have guessed: a real answer costs 1,485.2 tokens in 2.25 calls, so tokens allow 5.4 answers a minute and about 135 a day, where requests would allow 13.3 and 444. Before the rework it answered 11 of 107: a judge reply cut off at 120 tokens was read as "not answerable" (found by a review I commissioned).

## 4. Where the time goes

Rule: the p95 total-time request of the real burst. It took 7,501.0 ms: 5.4 ms waiting, 1,763.1 ms working, 5,731.8 ms retrying (76.4%), because our limiter held its draft twice for the bucket. All of it came before the first token; the gate added 132 ms.

## 5. When a limit is hit

Before spending a token, each request gets the richest level whose predicted wait plus p95 fits its remaining patience; running requests come first.

- **Per minute** (overload, 275 requests): 56 got a full answer, 25 one without repair; 93 were held a median 4.2 s first; 189 got passages with a notice, because the wait would outlast them; 5 got nothing.
- **Per day** (35 requests): 9 answered, then 26 got passages. Nobody was delayed: no wait would help in time.

## Retrieval: quality versus cost

R1 to R2 changes piece size; R2 to R3 adds re-ranking (Table 3). Correct: 40.0%, 33.3%, 40.0%; tokens per answer: 1,378.5, 2,070.4, 2,271.7. One answer separates them (noise at n=15; one R2 and R3 answer was also lost to a gate bug fixed afterwards and not rerun), so I keep R1: 61% of R3's tokens, and tokens bind. Near the limit the repair goes (p95 1,367 tokens against 2,267); past it, the passages are sent as they are.

## How this library will mislead a naive retriever

1. **Cross-paper collision.** "How many GPUs was Moshi trained on?" returns the Transformer's hardware section (p. 7). Fixed: search only the named paper.
2. **Chat transcripts.** "What does the Moshi paper say about SoundStream?" returns an appendix dialogue (p. 63). Fixed: drop question words.
3. **Shredded tables.** "What bits-per-byte does H-Net (2-stage) XL reach?" returns a plot's axis labels (p. 13); 25 of 35 tables lost their decimal points. Not fixed.

## 6. Retries

Retry only 429s, 5xx and timeouts; wait `retry-after`; never past the deadline; three attempts; a shared budget. Made worse: on the same overload, retrying at once sent 367 calls against 133, of which 234 were refused, and answered no more (27 of 105 against 29); it did fail faster.

## 7. What the person sees

"Today's model budget for this assistant is used up, so I cannot write you a checked answer right now. The budget refills gradually rather than at midnight: the next full answer can be served in about 9 minutes. These are passages from the papers that best match your question. They are passages, not an answer." Two cited passages follow. (Stand-in; the real daily limit was never reached, and gradual refill is observed only for requests per day.)

## 8. What I'm unsure about, what I left out, what I learned

Least certain: patience, assumed at a median of 8 s. Halving it in the same overload left answers unchanged (37 of 105 against 35), because tokens decide who is answered, but 14 people gave up against 5.

Not built: failover to another model (it dodges a per-model limit); releasing sentences as each passes the gate (233 ms sooner, but a later one can fail).

New to me: I learned that a later repair can hit a 429 after earlier calls have already spent tokens, and that failed-request duration is not user-visible time to first token.

## Your feedback on Vaani

Teacher AI’s clarifying questions and reviewable prompt/tool diff stood out. Memories carries selected past-call context forward. The readiness-score calculation and memory-selection policy were unclear to me; I would explain both beside the demo.

## How I worked with the assistant

My prompts set the objective across quality, latency, limits, and cost; required comparable questions and a budget check before real calls; and defined first-token time and reproducible tables. I used an LLM for direction, while the engineering requirements and system write-up were mine. I caught a cost summary that omitted known usage from repair attempts ending in 429 by reconciling traces. I overruled a stand-in-only comparison: after the reset, I required budgeted real R2 runs on both days and then R3, keeping failures visible. I approved the assistant-proposed trace audit because it made answers and evidence reviewable. My contribution was setting and enforcing those measurement and decision constraints.

History: `transcripts/`.

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
