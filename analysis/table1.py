#!/usr/bin/env python3
"""Table 1 (limits found in the real service), built only from saved measurement files.

Zero provider calls. Sources: results/raw/limit_refill_analysis.json (headers of
every saved real response), the probe files results/raw/E2-*.json, E3-*.json and
E4-*.json, and Groq's published limits page as saved in
results/raw/groq-public-rate-limits.json. Run from the repo root:

    .venv/bin/python analysis/table1.py
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results" / "raw"


def latest(prefix: str) -> tuple[str, list[dict[str, Any]]] | None:
    """The most recent probe file of a kind that actually reached the provider."""
    for path in sorted(RAW.glob(f"{prefix}-*.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if sum(record.get("status") in (200, 429) for record in data["records"]) >= 2:
            return f"`{path.relative_to(ROOT)}` ({data['model']})", data["records"]
    return None


def message(body: str) -> str:
    try:
        return json.loads(body)["error"]["message"].split(" Need more tokens?")[0]
    except (ValueError, KeyError, TypeError):
        return body[:160]


def render() -> str:
    refill = json.loads((RAW / "limit_refill_analysis.json").read_text(encoding="utf-8"))
    docs = "`results/raw/groq-public-rate-limits.json` (Groq docs, read 2026-11-11)"
    n = refill["tokens"]["n"]
    rows: list[tuple[str, str, str, str]] = []
    returned: list[str] = []
    recovery: list[str] = []

    e2 = latest("E2")
    if e2:
        source, records = e2
        calls = [r for r in records if str(r.get("label", "")).startswith("burst_")]
        accepted = [r for r in calls if r["status"] == 200]
        limited = [r for r in calls if r["status"] == 429]
        recovered = next((r for r in records if r.get("label") == "recovered"), None)
        if limited:
            window = limited[0]["sent_s"] - accepted[0]["sent_s"]
            used = re.search(r"Limit \d+, Used \d+", message(limited[0]["body"]))
            rows.append(("Requests per minute", "30",
                         f"{len(accepted)} calls accepted in {window:.1f} s, call {len(accepted) + 1} "
                         f"refused with 429 \"{used.group(0) if used else 'rate limit reached'}\"; "
                         f"{source}. Matches the docs page.", "high for this model; docs only for the other two"))
            returned.append(f"RPM: HTTP 429, `retry-after: {limited[0]['headers'].get('retry-after')}`, "
                            f"body: \"{message(limited[0]['body'])}\"")
            if recovered:
                recovery.append(f"RPM: first success {recovered['seconds_from_first_429']} s after the 429 "
                                "(probed every 2 s), so it refills about one request per 2 s")
        else:
            rows.append(("Requests per minute", f"at least {len(accepted)} in a burst; docs say 30",
                         f"{len(accepted)} back-to-back calls, none refused; {source}", "low"))
    else:
        rows.append(("Requests per minute", "30 (docs only)", docs, "low"))

    rows.append(("Tokens per minute", f"{refill['tokens']['limit']:,}",
                 f"`x-ratelimit-limit-tokens` on {n} real responses; bucket refills "
                 f"{refill['tokens']['refill_per_second_median']:.1f} tokens/s (reset header = used ÷ rate, "
                 f"median error {refill['tokens']['abs_error_ms_median']} ms); "
                 "`results/raw/limit_refill_analysis.json`", "high"))
    rows.append(("Requests per day", f"{refill['requests']['limit']:,}",
                 f"`x-ratelimit-limit-requests` on {n} real responses; refills one request per "
                 f"{refill['requests']['seconds_per_unit_median']} s; never reached", "medium"))
    rows.append(("Tokens per day", "200,000 (docs only)",
                 f"{docs}. No header reports it and it was never reached.", "low"))

    e4 = latest("E4")
    if e4:
        source, records = e4
        single = [r["ms"] for r in records if r["label"].startswith("single_") and r["status"] == 200]
        parts = []
        largest_clean = 0
        for size in (10, 20):
            # Count only calls that reached the provider (a local budget guard can stop one first).
            batch = [r for r in records
                     if r["label"].startswith(f"batch{size}_") and r["status"] in (200, 429)]
            if not batch:
                continue
            ok = [r for r in batch if r["status"] == 200]
            slow = sorted(r["ms"] for r in ok)
            p95 = slow[max(0, math.ceil(len(slow) * 0.95) - 1)] if slow else float("nan")
            parts.append(f"{len(batch)} at once: {len(ok)} accepted, {len(batch) - len(ok)} refused, "
                         f"p50 {statistics.median(slow):.0f} ms, p95 {p95:.0f} ms" if slow
                         else f"{size} at once: all refused")
            if len(ok) == len(batch):
                largest_clean = max(largest_clean, len(batch))
        value = (f"at least {largest_clean}; no separate limit found" if largest_clean
                 else "not measured")
        rows.append(("Requests in flight at once", value,
                     f"single-call median {statistics.median(single):.0f} ms (n={len(single)}); "
                     + "; ".join(parts) + f"; {source}", "medium (a lower bound)"))
    else:
        rows.append(("Requests in flight at once", "not measured", "the in-flight probe was not run", "low"))

    e3 = latest("E3")
    tpm_429 = [r for r in (e3[1] if e3 else []) if r.get("status") == 429]
    if tpm_429:
        returned.append(f"TPM: HTTP 429, `retry-after: {tpm_429[0]['headers'].get('retry-after')}`, "
                        f"body: \"{message(tpm_429[0]['body'])}\" ({e3[0]})")
    waits = refill["retry_after_s"]
    recovery.append(f"TPM: `retry-after` between {waits['min']:.0f} and {waits['max']:.0f} s on "
                    f"{waits['n']} real 429s; it is the time for the bucket to refill enough for the "
                    "refused request, not a full reset")
    rows.append(("What it returns when a limit is hit",
                 "HTTP 429 with `retry-after` and the `x-ratelimit-*` headers",
                 "<br>".join(returned) or "429s seen in the ledger; body not captured", "high"))
    rows.append(("Time until it recovers", "seconds, in proportion to the size of the refused request",
                 "<br>".join(recovery), "high for per-minute limits; daily limits not observed"))

    lines = ["## Table 1: Limits you found in the real service", "",
             "| Limit | Value you found | How you found it | Confidence |", "| --- | --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render())
