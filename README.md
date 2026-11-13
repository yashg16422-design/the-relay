# The Relay

A small assistant that answers questions about three research papers with citations, and says so when the papers do not answer. The write-up is `WRITEUP.md`; this file says where things are and how to rerun them. (The design notes written before the 2026-11-12 rework are kept in `docs/README.before-rework-2026-11-12.md`; parts of them no longer describe the code.)

## Request path

1. **Understand** – one model call returns a search query and the paper the question is about (`relay/decide.py`).
2. **Retrieve** – BM25 over the chunked papers, limited to that paper (`relay/retrieval.py`).
3. **Decide** – a zero-token check, then the drafter either answers or replies `NOT_IN_PAPERS` (`decide_mode` in `config/pipeline.yaml`; the older judge call is still selectable).
4. **Draft** – streamed, every sentence cited.
5. **Sentence gate** – each sentence is checked against its cited passage; one repair attempt (`relay/gate.py`).

Around it: a limiter that models Groq's refilling buckets (`relay/limiter.py`), a ladder that picks full answer, answer without repair, passages only, or an immediate no (`load/loadgen.py::choose_level`), a retry policy (`relay/retry.py`) and per-request traces (`results/traces/`).

## Where the evidence is

| What | File |
| --- | --- |
| Every request, one row each | `results/results.csv` (built by `analysis/build_submission_csv.py` from `results/raw/`) |
| Runs used in the tables | `results/raw/runs/` |
| Runs made and then replaced, with the reason | `results/raw/superseded/README.txt` |
| Limit probes (real 429 bodies and headers) | `results/raw/E0-*.json`, `E2-*.json`, `E3-*.json`, `E4-*.json` |
| Every real call and its tokens | `budget_ledger.csv` |
| Stand-in calibration | `results/calibration_report.md`, `standin/config.yaml` |
| Gate attack cases and result | `eval/gate_adversarial_cases.yaml`, `results/gate_adversarial_report.json` |
| Retriever failures and fixes | `results/naive_retriever_report.json` |
| Questions and gold answers | `eval/questions_aloo.yaml` |
| Grades and who gave them | `eval/grades_*.csv` |
| Decisions, with what each cost | `docs/decisions/` |

## Rerun the checks (no provider calls)

Machine: the Mac with this repo. Directory: the repo root.

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python analysis/build_submission_csv.py --write && .venv/bin/python analysis/insert_tables.py && .venv/bin/python analysis/recheck.py
```

The first command rebuilds `results/results.csv` from the files under `results/raw/` (decision record 0007); the second regenerates the tables in `WRITEUP.md`; the third recomputes them independently with pandas.

Other zero-cost analyses: `analysis/gate_adversarial.py`, `analysis/naive_retriever.py`, `analysis/limit_refill.py`, `analysis/calibrate_standin.py --report`, `analysis/replay_gate.py`, `analysis/slow_request.py <scenario> <real|stand-in>`, and `bash load/run_all.sh <run-id>` for the eight stand-in load scenarios (about 16 minutes).

## Real runs

`probe/real_probes.py` and `eval/run_real.py` call Groq. Each prints its estimate and the model's use in the last 24 hours and waits for `go`. The key is read from `.env`, which is not in the repository.
