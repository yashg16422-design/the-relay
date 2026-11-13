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

