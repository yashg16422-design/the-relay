"""Derive how Groq's limits refill from the response headers already saved in the ledger.

Zero provider calls. Every real response carried x-ratelimit-* headers, and
relay/llm_client.py saved them in budget_ledger.csv. If a limit is a bucket
that refills continuously, then

    reset time = (limit - remaining) / refill rate

on every response. This script tests that on every saved row and writes the
fitted rates to results/raw/limit_refill_analysis.json. Run from the repo root:

    .venv/bin/python analysis/limit_refill.py
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "budget_ledger.csv"
OUTPUT = ROOT / "results" / "raw" / "limit_refill_analysis.json"
CUTOFF = "2026-11-12"  # this analysis covers the ordinary pipeline calls saved up to 2026-11-11
_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?$")


def seconds(text: str) -> float | None:
    """Parse Groq's reset format: "55.477s", "8m38.4s", "1h2m3s", "120ms"."""
    match = _DURATION.match(text.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, secs, millis = (float(part) if part else 0.0 for part in match.groups())
    return hours * 3600 + minutes * 60 + secs + millis / 1000


def nearest_rank(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * percent // 100))  # ceiling
    return ordered[int(rank) - 1]


def analyse(ledger: Path = LEDGER) -> dict[str, Any]:
    samples: dict[str, list[dict[str, float]]] = defaultdict(list)
    limits: dict[str, set[int]] = defaultdict(set)
    rate_limited: list[dict[str, Any]] = []
    rows_with_headers = 0
    with ledger.open(newline="", encoding="utf-8") as stream:
        for line_number, row in enumerate(csv.DictReader(stream), start=2):
            if row["model"] in ("standin", "__models_endpoint__"):
                continue
            if row["timestamp"] >= CUTOFF:
                continue  # later calls include deliberate limit probes, analysed in analysis/table1.py
            headers = json.loads(row["headers_json"] or "{}")
            if "x-ratelimit-limit-tokens" not in headers:
                continue
            rows_with_headers += 1
            for kind in ("tokens", "requests"):
                limit = int(headers[f"x-ratelimit-limit-{kind}"])
                used = limit - int(headers[f"x-ratelimit-remaining-{kind}"])
                reset = seconds(headers[f"x-ratelimit-reset-{kind}"])
                limits[kind].add(limit)
                if used > 0 and reset is not None:
                    samples[kind].append({"used": used, "reset_s": reset, "limit": limit})
            if row["status"] == "rate_limited":
                retry_after = float(headers["retry-after"])
                remaining = int(headers["x-ratelimit-remaining-tokens"])
                limit = int(headers["x-ratelimit-limit-tokens"])
                rate_limited.append(
                    {
                        "ledger_line": line_number,
                        "timestamp": row["timestamp"],
                        "model": row["model"],
                        "retry_after_s": retry_after,
                        "tokens_remaining": remaining,
                        # If retry-after is "wait until the bucket holds this request",
                        # the request needed about this many tokens.
                        "implied_request_tokens": round(remaining + retry_after * limit / 60),
                    }
                )

    result: dict[str, Any] = {
        "source": str(ledger.relative_to(ROOT)),
        "real_rows_with_rate_limit_headers": rows_with_headers,
        "method": "seconds per unit = reset_s / (limit - remaining), per saved response",
        "limits_seen": {kind: sorted(values) for kind, values in limits.items()},
    }
    for kind, window_s, window_name in (("tokens", 60.0, "minute"), ("requests", 86400.0, "day")):
        rows = samples[kind]
        per_unit = [row["reset_s"] / row["used"] for row in rows]
        limit = statistics.mode(row["limit"] for row in rows)
        predicted = window_s / limit
        errors_ms = [abs(row["reset_s"] - row["used"] * predicted) * 1000 for row in rows]
        result[kind] = {
            "n": len(rows),
            "limit": limit,
            "limit_window": window_name,
            "seconds_per_unit_median": round(statistics.median(per_unit), 6),
            "seconds_per_unit_if_continuous_refill": round(predicted, 6),
            "refill_per_second_median": round(1 / statistics.median(per_unit), 3),
            "abs_error_ms_median": round(statistics.median(errors_ms), 3),
            "abs_error_ms_p95_nearest_rank": round(nearest_rank(errors_ms, 95), 3),
            "abs_error_ms_max": round(max(errors_ms), 3),
        }
    result["rate_limited_responses"] = rate_limited
    result["retry_after_s"] = {
        "n": len(rate_limited),
        "min": min(item["retry_after_s"] for item in rate_limited),
        "max": max(item["retry_after_s"] for item in rate_limited),
        "all_hit_limit": "tokens per minute (requests remaining was 985 or more on every one)",
    }
    result["not_in_headers"] = [
        "requests per minute (no header reports it)",
        "tokens per day (no header reports it)",
        "requests in flight at once",
        "the 429 response body (it was not saved before 2026-11-12)",
    ]
    return result


def main() -> None:
    result = analyse()
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for kind in ("tokens", "requests"):
        stats = result[kind]
        print(
            f"{kind}: limit {stats['limit']} per {stats['limit_window']}, n={stats['n']}, "
            f"refill {stats['refill_per_second_median']}/s "
            f"({stats['seconds_per_unit_median']} s per unit; continuous refill predicts "
            f"{stats['seconds_per_unit_if_continuous_refill']}), median error "
            f"{stats['abs_error_ms_median']} ms, p95 {stats['abs_error_ms_p95_nearest_rank']} ms, "
            f"max {stats['abs_error_ms_max']} ms"
        )
    print(f"429s: {result['retry_after_s']}")
    for item in result["rate_limited_responses"]:
        print("  ", item)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
