from __future__ import annotations

import csv
import json

from analysis.limit_refill import analyse, seconds


def test_reset_durations_parse() -> None:
    assert seconds("55.477s") == 55.477
    assert seconds("8m38.4s") == 518.4
    assert seconds("1h2m3s") == 3723.0
    assert seconds("120ms") == 0.12
    assert seconds("soon") is None


def test_refill_rates_are_recovered_from_headers(tmp_path, monkeypatch) -> None:
    import analysis.limit_refill as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    ledger = tmp_path / "budget_ledger.csv"
    fields = ["timestamp", "experiment", "model", "tokens_in", "tokens_out",
              "cached_tokens", "reasoning_tokens", "status", "headers_json"]

    def row(used_tokens: int, used_requests: int, status: str = "ok", retry: str | None = None):
        headers = {
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": str(8000 - used_tokens),
            "x-ratelimit-reset-tokens": f"{used_tokens * 0.0075:.3f}s",
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": str(1000 - used_requests),
            "x-ratelimit-reset-requests": f"{used_requests * 86.4:.1f}s",
        }
        if retry:
            headers["retry-after"] = retry
        return {"timestamp": "2026-11-11T00:00:00Z", "experiment": "e", "model": "m", "status": status,
                "headers_json": json.dumps(headers)}

    with ledger.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows([row(1000, 1), row(4000, 2), row(7397, 3, "rate_limited", "3"),
                          {"timestamp": "t", "experiment": "e", "model": "standin",
                           "status": "ok", "headers_json": "{}"}])
    result = analyse(ledger)
    assert result["real_rows_with_rate_limit_headers"] == 3
    assert result["tokens"]["refill_per_second_median"] == 133.333
    assert result["requests"]["seconds_per_unit_median"] == 86.4
    assert result["rate_limited_responses"][0]["implied_request_tokens"] == 1003
