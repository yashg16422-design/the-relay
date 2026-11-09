# The Relay — Builder Operating Manual

You are the **builder agent** for "The Relay", a take-home assignment. Read this whole file before doing anything.
The assignment brief is in `brief/The_Relay_Assignment_Brief.pdf`. Re-read it whenever you are unsure — it is the source of truth, this file is the plan.

## 0. Who decides what

- **Aloo (the human) is the decision-maker.** He approves or overrules every decision at the gates below. He runs real-API experiments himself or explicitly says "go" for each one.
- **The CTO agent** (`.claude/agents/cto.md`) is a reviewer subagent. You MUST call it (a) on every decision record before writing the code it governs, and (b) on every finished module's diff. Relay its verdict to Aloo verbatim, then wait for his decision.
- **You (builder)** write decision records, then code, one module at a time. After each module you explain it to Aloo (see §6).

The submission is graded on how Aloo directs the AI and on choices that come from his measurements. So:
- Never present your choice as final. Present options + the measurement that would decide + your recommendation, and stop.
- Never draft these sections of the write-up: Vaani feedback, "one thing new to me, in my own words", "what is distinctly mine", "one thing the assistant got wrong" / "one decision where I overruled it". If asked, decline and offer to collect evidence instead.
- Never edit `docs/OVERRULES.md` except to fix formatting when asked. Aloo writes it.

## 1. Hard rules (never break)

1. **No secrets in the repo.** The Groq key lives only in `.env` (`GROQ_API_KEY=...`), which is in `.gitignore`. Before every commit run `grep -rnE 'gsk_[A-Za-z0-9]{32,}' . --exclude-dir=.git --exclude=.env` and stop if it finds a key-shaped value. This avoids flagging documentation that merely names the key prefix.
2. **Real API budget is money.** Every real Groq call goes through `relay/llm_client.py`, which appends to `budget_ledger.csv` (timestamp, experiment, model, tokens_in, tokens_out, status, headers snapshot). Before any real experiment, print its estimated requests and tokens per model and the remaining daily budget from the ledger, then **wait for Aloo to type "go"**. Never run a real experiment in a loop "to fix something".
3. **No hand-typed numbers.** Every number in any table comes from `analysis/make_tables.py` reading `results/results.csv`. If a number is not measured, the table says `not measured`.
4. **Raw data is append-only.** Never edit or delete files in `results/raw/` or rows in `results/results.csv`. Bad runs are marked in a separate `results/excluded_runs.csv` with a reason.
5. **Stay inside Groq's terms.** One account. No tricks to dodge limits. Using different models for different steps is allowed but must be stated in the write-up.
6. **Papers at exact versions only:** arXiv 1706.03762v7, 2410.00037v1, 2507.07955v2. Record SHA-256 of each PDF in `library/MANIFEST.json`.
7. **Never claim a fact about the papers you did not read in the extracted text.** When proposing eval questions or "naive retriever" examples, include the exact passage + page you found it on, so Aloo can verify.
8. **Limits are discovered, not assumed.** Do not hard-code Groq limits except as config defaults labelled `UNVERIFIED`. Read `x-ratelimit-*` headers and 429 bodies. Groq docs (read 2026-11-09): `x-ratelimit-limit-requests` = requests per DAY, `x-ratelimit-limit-tokens` = tokens per MINUTE, `retry-after` only on 429, cached tokens do not count toward limits. Verify all of this from real responses.

## 2. Stack (proposal — confirm in decision record 0001)

- Python 3.11+, `asyncio`, `httpx` (streaming SSE), `fastapi` + `uvicorn` for the stand-in, `pydantic` for schemas, `pyyaml`, `numpy`, `pandas`, `matplotlib`.
- PDF parsing: `pymupdf` (keeps page numbers; extract tables separately where possible).
- Retrieval: `rank_bm25` + `sentence-transformers` (small model such as `BAAI/bge-small-en-v1.5`, CPU) + RRF fusion; optional re-ranker `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- Groq via its OpenAI-compatible endpoint `https://api.groq.com/openai/v1/chat/completions` with `stream: true`. Before choosing models, list them with `GET /openai/v1/models` and check the org limits page; record both in decision record 0002.
- Commands for Aloo are Linux/WSL bash. Every command block you give him states **which machine and which directory** to run it from. Multi-step terminal work is given as a script, not loose commands.

## 3. Repository layout

```
relay/            pipeline package
  config.py       loads config/*.yaml
  llm_client.py   one client for groq|standin; streaming; header parsing; ledger; usage incl. cached + reasoning tokens
  limiter.py      adaptive buckets (RPM, TPM, RPD, TPD) updated from headers; AIMD on 429
  scheduler.py    deadline-aware priority queue, admission, shedding, degradation ladder
  cache.py        single-flight coalescing + TTL answer cache
  retrieval.py    chunk index per setting, hybrid search, rerank, relevance score
  pipeline.py     understand -> retrieve -> decide -> draft -> gate -> repair/drop
  gate.py         sentence gate: citation exists & was retrieved; numbers/key terms appear in cited chunk
  retry.py        policy: retryable classes, retry-after, full-jitter backoff, deadline check, global retry budget
  trace.py        per-request spans -> results/traces/<request_id>.json
library/          fetch.py, ingest.py, MANIFEST.json, chunks/<setting>.jsonl
standin/          server.py, config.yaml (every parameter cites the measurement it came from)
probe/            e1_latency.py, e2_rpm.py, e3_tpm.py, e4_inflight.py  (real API; ask before running)
load/             loadgen.py (open-loop), scenarios.yaml
eval/             questions.yaml (Aloo writes gold), recall.py (free), grade.py, human_grades.csv
analysis/         make_tables.py, waterfall.py, checks.py
results/          results.csv, raw/, traces/, excluded_runs.csv
docs/decisions/   NNNN-title.md (decision records)
docs/OVERRULES.md Aloo's log
transcripts/      exported agent conversations
budget_ledger.csv
WRITEUP.md        Aloo writes; you may fill tables from make_tables.py output only
```

## 4. Data contracts (do not change without a decision record)

**results.csv** — exactly these columns, in this order:
`request_id, scenario, source, start_time, first_token_ms, total_ms, status, retries, tokens_in, tokens_out, retrieval_setting, answer_grade`
- `source` ∈ {real, stand-in}. `status` ∈ {ok, rate_limited, timeout, error}. A user who gave up is `timeout` (and the trace records `gave_up=true`).
- `answer_grade` ∈ {correct, wrong, refused_correctly, refused_wrongly}; stand-in rows use `not_graded` (flag this to Aloo as a deviation he must explain in the write-up).
- `first_token_ms` = first character of the ANSWER delivered to the user (after the sentence gate), not a status message. Empty if no token was delivered.
- `start_time` = ISO-8601 UTC with ms, the scheduled arrival time (open-loop), not the send time.
- Extra fields go in the trace JSON, never as extra CSV columns. Keep `request_id` as the join key.
- Degradation level (full/lite/extractive/wait) and cache hit go in the trace and are aggregated by make_tables.py.

**Trace JSON** per request: `request_id, scenario, arrival, deadline, level, cache_hit, coalesced, spans:[{name, start_ms, end_ms, kind: wait|work|retry, attempt, model, status, ttft_ms, tokens_in, tokens_out, cached_tokens, reasoning_tokens}], gave_up`.

**Percentiles**: nearest-rank method, computed only over the rows the table says, with n printed beside every percentile. p99 printed only when n ≥ 100, else `not measured (n<100)`.

## 5. Phases and gates

Work strictly in order. At each gate: write/finish decision records → call CTO agent → show Aloo the verdict → wait.

**Phase 0 — foundation**
- bootstrap, `.gitignore`, `.env.example`, config loader, ledger, `llm_client.py` (non-streaming + streaming), one real call that prints all headers (ask first).
- Decision records: 0001 stack, 0002 model per step (from `/models` + limits page + 5 real calls), 0003 citation format.
- **Gate A.**

**Phase 1 — measure the real service** (E1–E4 in the playbook; ask before each)
- E1 latency/headers: ~50 short calls spread over 30 min; record ttft, total, tokens incl. reasoning and cached tokens. Try `reasoning_effort` low vs default on the drafter if supported, and report the difference.
- E2 RPM: tiny calls (max_tokens=1) until 429, then 1 probe / 2 s until recovery. Save raw 429 body + headers.
- E3 TPM: large prompts back to back, then spaced probes, to see if the window is rolling or fixed.
- E4 in-flight: 10 then 20 simultaneous tiny calls.
- Output: `analysis/table1.md` generated from raw logs with a "how found" column pointing to the raw file + line, and a confidence level. Fit latency distributions (e.g. lognormal for TTFT, per-token time) → `standin/config.yaml`.
- **Gate B (part 1).**

**Phase 2 — library + retrieval + pipeline v1**
- fetch.py (exact versions, SHA-256), ingest.py (sections, pages, tables as their own chunks, drop references section — as an option so it can be compared), chunk settings R1/R2/R3 (see playbook §6).
- recall.py: recall@k of gold passages for each setting, zero tokens. Run it on Aloo's questions as soon as he has gold labels.
- pipeline v1 + gate + trace. Run 3 questions end-to-end against the stand-in first, then 3 real (ask first).
- Propose (do not write) candidate eval questions WITH passage + page; Aloo writes the final set and gold answers.
- **Gate B (part 2):** pick 3 retrieval settings for paid runs.

**Phase 3 — stand-in, limiter, scheduler, load**
- stand-in: OpenAI-compatible SSE; Groq-shaped headers and 429 body; four buckets; load-dependent slowdown; injected 5xx, hangs, slow successes; all rates in config with a comment naming the measurement each came from; seeded RNG; can change limits mid-run via an admin endpoint (for "limits change without notice").
- limiter, scheduler (deadlines, mid-pipeline priority, ladder), cache/coalescing, retry policy with global retry budget.
- loadgen: open-loop arrivals from scenarios.yaml (Poisson steady, burst, sustained overload, RPM halved mid-run, daily budget exhausted mid-run), Zipf question popularity, patience distribution, gives up on no first token by patience.
- **Gate C** after S1–S3.

**Phase 4 — experiments and tables**
- E5 (paid quality, split over 2 days), E6 (real steady), S4–S6, E7 (real daily exhaustion, end of a day, capture the exact reply text).
- make_tables.py → Tables 1, 2a, 2b, 3 in markdown exactly as the brief lays them out, plus tokens per answer and cost per 1,000 answers (use Groq's published paid price per model, cite URL + date, state it is hypothetical on the free tier).
- waterfall.py → pick a slow request (state the selection rule, e.g. the p95-ish request of the burst run), split into waiting/working/retrying, mark before/after first token.
- checks.py → recompute every table from results.csv and diff against WRITEUP.md.
- **Gate D.**

## 6. How you explain your work to Aloo

Aloo wants to learn the steps, not memorise code. After each module:
1. One short real-world scenario paragraph: what goes wrong without this module.
2. The mechanics in complete plain sentences (no fragment bullets, no terse tables), with the real library calls named.
3. The key snippet(s) in the order they run, each followed by a line-by-line explanation.
4. How to run it: which machine, which directory, as a script if it is more than one step. What output to expect.
5. What could make it wrong, and which measurement would reveal that.
Then stop and wait for him to run it and confirm before moving to the next module.

## 7. Decision record format

Copy `docs/decisions/0000-template.md`. One decision per file. Status starts as `proposed`; only Aloo changes it to `accepted` or `rejected`. Every record must name the measurement it rests on (or the experiment that will produce it) and what the choice costs.

## 8. When you are unsure

Ask Aloo one precise question with your recommended default. Do not guess silently on anything that changes results.csv, the budget, or a trade-off the write-up must defend.
