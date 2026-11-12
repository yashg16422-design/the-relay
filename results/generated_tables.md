<!-- BEGIN GENERATED RESULTS TABLES -->

## Table 1: Limits you found in the real service

| Limit | Value you found | How you found it | Confidence |
| --- | --- | --- | --- |
| Requests per minute | openai/gpt-oss-120b: 30 (published page shows 30; account limits page not opened; 429 for this limit not observed); openai/gpt-oss-20b: 30 (published page shows 30; account limits page not opened; 429 for this limit not observed); qwen/qwen3.8-27b: 30 (published page shows 30; account limits page not opened; 429 for this limit not observed) | /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/results/raw/groq-public-rate-limits.json; https://console.groq.com/docs/rate-limits checked 2026-11-11; account limits page not opened | medium |
| Tokens per minute | openai/gpt-oss-120b: 8000; openai/gpt-oss-20b: 8000; qwen/qwen3.8-27b: 8000 | /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:437 headers_json (x-ratelimit-limit-tokens); /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:453 headers_json (x-ratelimit-limit-tokens); /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:441 headers_json (x-ratelimit-limit-tokens) | high |
| Requests per day | openai/gpt-oss-120b: 1000; openai/gpt-oss-20b: 1000; qwen/qwen3.8-27b: 1000 | /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:437 headers_json (x-ratelimit-limit-requests); /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:453 headers_json (x-ratelimit-limit-requests); /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:441 headers_json (x-ratelimit-limit-requests) | high |
| Tokens per day | openai/gpt-oss-120b: 200000 (published page shows 200K; scaled after 1K and 8K matched saved headers; account limits page not opened; 429 for this limit not observed); openai/gpt-oss-20b: 200000 (published page shows 200K; scaled after 1K and 8K matched saved headers; account limits page not opened; 429 for this limit not observed); qwen/qwen3.8-27b: 200000 (published page shows 200K; scaled after 1K and 8K matched saved headers; account limits page not opened; 429 for this limit not observed) | /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/results/raw/groq-public-rate-limits.json; https://console.groq.com/docs/rate-limits checked 2026-11-11; account limits page not opened | medium |
| Requests in flight at once | not measured | not measured | low |
| What it returns when a limit is hit | observed: HTTP 429; response body not captured; Retry-After: 16 s; token limit header: 8000 per minute | /Users/yashpratapsingh/Downloads/relay-agent-pack/the-relay/budget_ledger.csv:401 status + headers_json | high |
| Time until it recovers | not measured | not measured | low |

## Table 2a: Latency by load scenario

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| --- | --- | ---: | ---: | ---: | --- | --- |
| burst | stand-in | 40 | 0.037381 | 64203.7 | 27.7 ms (n=40) / 36.9 ms (n=40) / not measured (n<100; n=40) | 27.7 ms (n=40) / 36.9 ms (n=40) / not measured (n<100; n=40) |
| daily budget exhausted | stand-in | 20 | 5.95 | 201.7 | 7.1 ms (n=1) / 7.1 ms (n=1) / not measured (n<100; n=1) | 3.3 ms (n=20) / 5.2 ms (n=20) / not measured (n<100; n=20) |
| per-minute limit halved | stand-in | 24 | 9.765 | 147.5 | 6.4 ms (n=19) / 52.3 ms (n=19) / not measured (n<100; n=19) | 3.7 ms (n=24) / 50.1 ms (n=24) / not measured (n<100; n=24) |
| retry_disabled | stand-in | 1 | not measured | not measured | not measured (n=0) / not measured (n=0) / not measured (n<100; n=0) | 2.2 ms (n=1) / 2.2 ms (n=1) / not measured (n<100; n=1) |
| retry_made_worse | stand-in | 1 | not measured | not measured | not measured (n=0) / not measured (n=0) / not measured (n<100; n=0) | 357.9 ms (n=1) / 357.9 ms (n=1) / not measured (n<100; n=1) |
| steady | real | 5 | 121.8 | 2.5 | 1517.7 ms (n=5) / 3174.9 ms (n=5) / not measured (n<100; n=5) | 1517.7 ms (n=5) / 3174.9 ms (n=5) / not measured (n<100; n=5) |
| steady | stand-in | 11 | 24.56 | 26.9 | 3.8 ms (n=11) / 16.7 ms (n=11) / not measured (n<100; n=11) | 3.8 ms (n=11) / 16.7 ms (n=11) / not measured (n<100; n=11) |
| sustained overload | stand-in | 75 | 9.74 | 462.0 | 3.3 ms (n=75) / 5.7 ms (n=75) / not measured (n<100; n=75) | 3.3 ms (n=75) / 5.7 ms (n=75) / not measured (n<100; n=75) |

## Table 2b: Outcomes by load scenario

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| burst (stand-in) | 100.0% (n=40) | 0.0% (n=40) | 0.0% (n=40) | 0.00 mean (n=40 measured of 40; coverage=100.0%) | not measured | 1853.0 (n=4) | not measured (stand-in; no real provider usage) |
| daily budget exhausted (stand-in) | 5.0% (n=20) | 95.0% (n=20) | 0.0% (n=20) | 0.00 mean (n=20 measured of 20; coverage=100.0%) | not measured | 2021.0 (n=1) | not measured (stand-in; no real provider usage) |
| per-minute limit halved (stand-in) | 79.2% (n=24) | 20.8% (n=24) | 0.0% (n=24) | 0.00 mean (n=24 measured of 24; coverage=100.0%) | not measured | 2021.0 (n=10) | not measured (stand-in; no real provider usage) |
| retry_disabled (stand-in) | 0.0% (n=1) | 0.0% (n=1) | 0.0% (n=1) | 0.00 mean (n=1 measured of 1; coverage=100.0%) | not measured | not measured (n=0) | not measured (stand-in; no real provider usage) |
| *Note* | Error rows not shown in the brief's outcome columns: 1. |  |  |  |  |  |  |
| retry_made_worse (stand-in) | 0.0% (n=1) | 0.0% (n=1) | 0.0% (n=1) | 1.00 mean (n=1 measured of 1; coverage=100.0%) | not measured | not measured (n=0) | not measured (stand-in; no real provider usage) |
| *Note* | Error rows not shown in the brief's outcome columns: 1. |  |  |  |  |  |  |
| steady (real) | 100.0% (n=5) | 0.0% (n=5) | 0.0% (n=5) | 0.00 mean (n=5 measured of 5; coverage=100.0%) | not measured | 1715.6 (n=5) | $0.2012 per 1,000 successful responses (n=5) |
| steady (stand-in) | 100.0% (n=11) | 0.0% (n=11) | 0.0% (n=11) | 0.00 mean (n=11 measured of 11; coverage=100.0%) | not measured | 1853.0 (n=4) | not measured (stand-in; no real provider usage) |
| sustained overload (stand-in) | 100.0% (n=75) | 0.0% (n=75) | 0.0% (n=75) | 0.00 mean (n=75 measured of 75; coverage=100.0%) | not measured | 1853.0 (n=4) | not measured (stand-in; no real provider usage) |

## Table 3: Retrieval settings

| Setting (piece size, pieces fetched, re-rank yes or no) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) | Cost per 1,000 successful answers |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| R1 (900 characters; 3 pieces; re-rank no) | 15 unique questions / 46 attempts (45 successful; graded n=45; ungraded attempts n=1) | 6.7% (grade n=45) | 73.3% (grade n=45) | 20.0% (grade n=45) | 1278.8 (n=45) | 1284.9 ms (n=45) / 1723.2 ms (n=45) | 1282.0 ms (n=46) / 1723.2 ms (n=46) | partial $0.6190 known cost per 1,000 successful responses (complete-cost n=45/46 attempts; unknown-cost n=1; coverage=97.8%) |
| R2 (1600 characters; 4 pieces; re-rank no) | 15 unique questions / 46 attempts (45 successful; graded n=45; ungraded attempts n=1) | 2.2% (grade n=45) | 77.8% (grade n=45) | 20.0% (grade n=45) | 2169.0 (n=45) | 1552.2 ms (n=45) / 9289.5 ms (n=45) | 1542.1 ms (n=46) / 9289.5 ms (n=46) | partial $1.1279 known cost per 1,000 successful responses (complete-cost n=45/46 attempts; unknown-cost n=1; coverage=97.8%) |
| R3 (2600 characters; 6 pieces; re-rank yes) | 15 unique questions / 49 attempts (44 successful; graded n=44; ungraded attempts n=5) | 15.9% (grade n=44) | 63.6% (grade n=44) | 20.5% (grade n=44) | 3479.9 (n=44) | 1993.3 ms (n=44) / 3961.0 ms (n=44) | 1993.3 ms (n=49) / 3961.0 ms (n=49) | partial $1.8471 known cost per 1,000 successful responses (complete-cost n=44/49 attempts; unknown-cost n=5; coverage=89.8%) |

### Notes

- Percentiles use the nearest-rank method. Each percentile reports its own sample size; p99 is shown only when n ≥ 100.
- Quality percentages use graded rows only. Stand-in `not_graded` rows remain in Questions asked but are excluded from grade denominators.
- Wrong % includes refused_wrongly, a refusal of a question the gold key can answer. Correctly refused % includes only refused_correctly.
- The outcome table's timed-out column includes only `timeout`; `error` counts are called out separately because the required table has no error column.
- A metric is `not measured` when the required source data is absent. Percentile n<100 is explicitly marked.
- Scenario duration runs from the first scheduled arrival in a run to that run's last completion. Separate run ids are summed. Duration keeps enough decimals that requests divided by the printed duration, times 60, rounds to the printed offered rate.
- Price inputs: https://console.groq.com/docs/models (checked 2026-11-11). Per-answer cost is a hypothetical published-price estimate; the measured runs used the free tier. Known spend from failed attempts is included when traces expose token counts; missing usage is excluded and marked unknown.
- Across 332 saved real-experiment header rows with a positive request deficit and a parseable x-ratelimit-reset-requests value, the reset spacing agreed within 0.01 seconds of 86.4 seconds per spent request. At the observed limit of 1000, the computed refill is 24.0 hours. A recovery wait after the daily request bucket reached zero was not measured.

<!-- END GENERATED RESULTS TABLES -->
