from __future__ import annotations

import csv
import json
import re

import pytest

from analysis.checks import check_writeup
from analysis.make_tables import (
    GENERATED_BEGIN,
    GENERATED_END,
    RESULT_FIELDS,
    _cost_per_1000,
    _table2a,
    _parse_reset_seconds,
    apply_human_grades,
    nearest_rank,
    percentile_cell,
    read_excluded_request_ids,
    read_results,
    render_tables,
)
from eval.grade_against_gold import grade_saved_answer
from relay.pipeline import SAFE_REFUSAL
from analysis.waterfall import render_waterfall


def write_results(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def result_row(
    request_id: str,
    *,
    scenario: str = "steady",
    source: str = "real",
    start_time: str = "2026-11-09T10:00:00.000Z",
    first_token_ms: str = "10",
    total_ms: str = "100",
    status: str = "ok",
    retries: str = "0",
    tokens_in: str = "10",
    tokens_out: str = "5",
    retrieval_setting: str = "R1",
    answer_grade: str = "correct",
) -> dict[str, str]:
    return {
        "request_id": request_id,
        "scenario": scenario,
        "source": source,
        "start_time": start_time,
        "first_token_ms": first_token_ms,
        "total_ms": total_ms,
        "status": status,
        "retries": retries,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "retrieval_setting": retrieval_setting,
        "answer_grade": answer_grade,
    }


def test_nearest_rank_and_p99_sample_rule() -> None:
    assert nearest_rank([50, 10, 30, 20, 40], 0.5) == 30
    assert nearest_rank([1, 2, 3, 4], 0.95) == 4
    assert percentile_cell([1.0, 2.0, 3.0], 0.99, "p99") == "not measured (n<100; n=3)"
    assert percentile_cell(list(range(1, 101)), 0.99, "p99") == "99.0 ms (n=100)"


def test_cost_per_1000_counts_cache_and_coalesced_answers_at_zero_cost() -> None:
    rows = [
        result_row("model", source="real"),
        result_row("cache", source="real"),
        result_row("coalesced", source="real"),
    ]
    traces = {
        "model": {
            "level": "full",
            "spans": [{"model": "m", "tokens_in": 1000, "tokens_out": 500}],
        },
        "cache": {"cache_hit": True, "spans": []},
        "coalesced": {"coalesced": True, "spans": []},
    }
    prices = {"m": {"input_usd_per_million": 1, "output_usd_per_million": 2}}

    output = _cost_per_1000(rows, traces, prices)

    assert output == "$0.6667 per 1,000 successful responses (n=3)"


def test_cost_per_1000_labels_incomplete_usage_coverage() -> None:
    rows = [result_row("model"), result_row("cache"), result_row("unknown")]
    traces = {
        "model": {
            "level": "full",
            "spans": [{"model": "m", "tokens_in": 1000, "tokens_out": 500}],
        },
        "cache": {"cache_hit": True, "spans": []},
        "unknown": {"level": "full", "spans": [{"model": "m", "tokens_in": None}]},
    }
    prices = {"m": {"input_usd_per_million": 1, "output_usd_per_million": 2}}

    output = _cost_per_1000(rows, traces, prices)

    assert output == (
        "partial $0.6667 known cost per 1,000 successful responses "
        "(complete-cost n=2/3 attempts; unknown-cost n=1; coverage=66.7%)"
    )


def test_cost_includes_known_spend_from_failed_attempts() -> None:
    rows = [
        result_row("success", status="ok"),
        result_row("repair-429", status="rate_limited"),
    ]
    traces = {
        "success": {
            "level": "full",
            "spans": [{"model": "m", "tokens_in": 1000, "tokens_out": 500}],
        },
        "repair-429": {
            "level": "full",
            "spans": [
                {"model": "m", "tokens_in": 1000, "tokens_out": 50},
                {"model": "m", "tokens_in": None, "tokens_out": None},
            ],
        },
    }
    prices = {"m": {"input_usd_per_million": 1, "output_usd_per_million": 2}}

    output = _cost_per_1000(rows, traces, prices)

    assert output == (
        "partial $3.1000 known cost per 1,000 successful responses "
        "(complete-cost n=1/2 attempts; unknown-cost n=1; coverage=50.0%)"
    )


def test_offered_rate_does_not_bridge_separate_loadgen_runs() -> None:
    rows = [
        result_row(
            "run-a-steady-00000",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:00:00.000Z",
            total_ms="100",
        ),
        result_row(
            "run-a-steady-00001",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:00:01.000Z",
            total_ms="100",
        ),
        result_row(
            "run-b-steady-00000",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:10:00.000Z",
            total_ms="100",
        ),
        result_row(
            "run-b-steady-00001",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:10:02.000Z",
            total_ms="100",
        ),
    ]

    output = _table2a({("steady", "stand-in"): rows})

    assert "| steady | stand-in | 4 | 3.2 | 75.0 |" in output


def test_offered_rate_is_not_measured_without_loadgen_run_identity() -> None:
    rows = [
        result_row(
            "manual-request-a",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:00:00.000Z",
            total_ms="100",
        ),
        result_row(
            "manual-request-b",
            scenario="steady",
            source="stand-in",
            start_time="2026-11-09T10:00:01.000Z",
            total_ms="100",
        ),
    ]

    output = _table2a({("steady", "stand-in"): rows})

    assert "| steady | stand-in | 2 | not measured | not measured |" in output


def test_e6_real_steady_ids_count_as_one_run() -> None:
    rows = [
        result_row(
            "E6-20261111T100355343523Z-00000",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:03:55.380Z",
            total_ms="3174.935",
        ),
        result_row(
            "E6-20261111T100355343523Z-00001",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:04:25.382Z",
            total_ms="1330.412",
        ),
        result_row(
            "E6-20261111T100355343523Z-00002",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:04:55.384Z",
            total_ms="1517.669",
        ),
        result_row(
            "E6-20261111T100355343523Z-00003",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:05:25.386Z",
            total_ms="1378.127",
        ),
        result_row(
            "E6-20261111T100355343523Z-00004",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:05:55.389Z",
            total_ms="1805.022",
        ),
    ]

    output = _table2a({("steady", "real"): rows})

    assert "| steady | real | 5 | 121.8 | 2.5 |" in output


def test_separate_e6_stamps_are_not_bridged() -> None:
    rows = [
        result_row(
            "E6-20261111T100000000000Z-00000",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:00:00.000Z",
            total_ms="100",
        ),
        result_row(
            "E6-20261111T100000000000Z-00001",
            scenario="steady",
            source="real",
            start_time="2026-11-11T10:00:01.000Z",
            total_ms="100",
        ),
        result_row(
            "E6-20261111T110000000000Z-00000",
            scenario="steady",
            source="real",
            start_time="2026-11-11T11:00:00.000Z",
            total_ms="100",
        ),
        result_row(
            "E6-20261111T110000000000Z-00001",
            scenario="steady",
            source="real",
            start_time="2026-11-11T11:00:01.000Z",
            total_ms="100",
        ),
    ]

    output = _table2a({("steady", "real"): rows})

    assert "| steady | real | 4 | 2.2 | 109.1 |" in output


def test_subsecond_duration_does_not_print_zero() -> None:
    rows = [
        result_row(
            "run-burst-00000",
            scenario="burst",
            source="stand-in",
            start_time="2026-11-11T10:00:00.000Z",
            total_ms="10",
            answer_grade="not_graded",
        ),
        result_row(
            "run-burst-00001",
            scenario="burst",
            source="stand-in",
            start_time="2026-11-11T10:00:00.000Z",
            total_ms="37",
            answer_grade="not_graded",
        ),
    ]

    output = _table2a({("burst", "stand-in"): rows})

    assert "| burst | stand-in | 2 | 0.037 | 3243.2 |" in output


def test_duration_rounding_reproduces_offered_rate() -> None:
    rows = [
        result_row(
            "run-burst-00000",
            scenario="burst",
            source="stand-in",
            start_time="2026-11-10T19:39:31.953Z",
            total_ms="10",
            answer_grade="not_graded",
        ),
        result_row(
            "run-burst-00001",
            scenario="burst",
            source="stand-in",
            start_time="2026-11-10T19:39:31.953Z",
            total_ms="37.381",
            answer_grade="not_graded",
        ),
    ]

    output = _table2a({("burst", "stand-in"): rows})
    match = re.search(r"\| burst \| stand-in \| 2 \| ([0-9.]+) \| ([0-9.]+) \|", output)

    assert match is not None
    printed_duration = float(match.group(1))
    printed_rate = match.group(2)
    assert f"{2 / printed_duration * 60:.1f}" == printed_rate


def test_reset_parser_treats_milliseconds_as_their_own_unit() -> None:
    assert _parse_reset_seconds("990ms") == pytest.approx(0.99)
    assert _parse_reset_seconds("1m26.4s") == pytest.approx(86.4)
    assert _parse_reset_seconds("12m57.599s") == pytest.approx(777.599)


def test_read_results_rejects_wrong_column_order(tmp_path) -> None:
    path = tmp_path / "results.csv"
    path.write_text("scenario,request_id\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected results.csv header"):
        read_results(path)


def test_tables_derive_metrics_and_exclude_not_graded_from_quality(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    rows = [
        result_row(
            "a",
            scenario="E5-test-R1",
            start_time="2026-11-09T10:00:00.000Z",
            first_token_ms="10",
            total_ms="100",
            tokens_in="10",
            tokens_out="5",
            answer_grade="correct",
        ),
        result_row(
            "b",
            scenario="E5-test-R1",
            start_time="2026-11-09T10:00:05.000Z",
            first_token_ms="20",
            total_ms="200",
            status="rate_limited",
            retries="1",
            answer_grade="refused_correctly",
        ),
        result_row(
            "c",
            scenario="E5-test-R1",
            source="stand-in",
            start_time="2026-11-09T10:00:10.000Z",
            first_token_ms="30",
            total_ms="300",
            tokens_in="20",
            tokens_out="10",
            answer_grade="not_graded",
        ),
    ]
    write_results(results_path, rows)

    output = render_tables(results_path, ledger_path=tmp_path / "none.csv", trace_dir=tmp_path / "traces", raw_dir=tmp_path / "raw")

    assert "E5-test-R1" not in output.split("## Table 3:", 1)[0]
    assert "p50" in output
    assert "| R1 (piece size not measured; pieces fetched not measured; re-rank not measured) | 3 unique questions / 3 attempts (2 successful; graded n=2; ungraded attempts n=1) | 50.0% (grade n=2) | 0.0% (grade n=2) | 50.0% (grade n=2) |" in output
    assert "not measured (price file missing)" in output
    assert "p99 is shown only when n ≥ 100" in output


def test_table1_uses_real_experiment_ledger_headers_and_429_raw_file(tmp_path) -> None:
    ledger = tmp_path / "budget_ledger.csv"
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "timestamp",
                "experiment",
                "model",
                "tokens_in",
                "tokens_out",
                "cached_tokens",
                "reasoning_tokens",
                "status",
                "headers_json",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-11-09T10:00:00.000Z",
                "experiment": "E1-latency",
                "model": "model-a",
                "status": "ok",
                "headers_json": json.dumps(
                    {
                        "x-ratelimit-limit-requests": "1000",
                        "x-ratelimit-limit-tokens": "12000",
                    }
                ),
            }
        )
        writer.writerow(
            {
                "timestamp": "2026-11-09T10:00:01.000Z",
                "experiment": "E1-latency",
                "model": "model-b",
                "status": "ok",
                "headers_json": json.dumps({"x-ratelimit-limit-tpm": "15000"}),
            }
        )
        writer.writerow(
            {
                "timestamp": "2026-11-09T10:00:00.000Z",
                "experiment": "S1-standin",
                "model": "standin",
                "status": "ok",
                "headers_json": json.dumps(
                    {"x-ratelimit-limit-requests": "999999"}
                ),
            }
        )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "e2-429.json").write_text(
        json.dumps(
            {
                "source": "real",
                "status_code": 429,
                "body": "{\"error\": {\"message\": \"daily limit reached\"}}",
            }
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "results.csv"
    write_results(result_path, [])

    output = render_tables(
        result_path,
        ledger_path=ledger,
        trace_dir=tmp_path / "traces",
        raw_dir=raw_dir,
    )

    assert "Requests per day | model-a: 1000" in output
    assert "Tokens per minute | model-a: 12000" in output
    assert "model-b: 15000" in output
    assert "x-ratelimit-limit-tpm" in output
    assert "Requests per minute | not measured" in output
    assert "HTTP 429: daily limit reached" in output
    assert "999999" not in output
    assert "| steady | not measured | 0 |" in output
    assert "| daily budget exhausted | not measured | 0 |" in output


def test_table1_reports_429_headers_when_body_was_not_saved(tmp_path) -> None:
    ledger = tmp_path / "budget_ledger.csv"
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "timestamp", "experiment", "model", "tokens_in", "tokens_out",
                "cached_tokens", "reasoning_tokens", "status", "headers_json",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": "2026-11-11T05:27:33.873Z",
                "experiment": "E5-selection-R1-day1",
                "model": "openai/gpt-oss-120b",
                "status": "rate_limited",
                "headers_json": json.dumps(
                    {
                        "retry-after": "3",
                        "x-ratelimit-limit-tokens": "8000",
                        "x-ratelimit-remaining-requests": "985",
                    }
                ),
            }
        )

    result_path = tmp_path / "results.csv"
    write_results(result_path, [])
    output = render_tables(
        result_path,
        ledger_path=ledger,
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )

    assert "HTTP 429; response body not captured" in output
    assert "Retry-After: 3 s" in output
    assert "token limit header: 8000 per minute" in output


def _ledger_row(experiment: str, model: str, headers: dict, status: str = "ok") -> dict[str, str]:
    return {
        "timestamp": "2026-11-11T10:00:00.000Z",
        "experiment": experiment,
        "model": model,
        "tokens_in": "1",
        "tokens_out": "1",
        "cached_tokens": "0",
        "reasoning_tokens": "0",
        "status": status,
        "headers_json": json.dumps(headers),
    }


def test_public_docs_fill_only_limits_missing_from_headers(tmp_path) -> None:
    ledger = tmp_path / "budget_ledger.csv"
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(_ledger_row("", "", {}).keys()))
        writer.writeheader()
        writer.writerow(
            _ledger_row(
                "E1-latency",
                "openai/gpt-oss-20b",
                {"x-ratelimit-limit-requests": "1000", "x-ratelimit-limit-tokens": "8000"},
            )
        )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "groq-public-rate-limits.json").write_text(
        json.dumps(
            {
                "kind": "public_documentation",
                "source_url": "https://console.groq.com/docs/rate-limits",
                "checked_date": "2026-11-11",
                "account_limits_page_opened": False,
                "models": {
                    "openai/gpt-oss-20b": {
                        "rpm_display": "30",
                        "rpd_display": "1K",
                        "tpm_display": "8K",
                        "tpd_display": "200K",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "results.csv"
    write_results(result_path, [])

    output = render_tables(
        result_path,
        ledger_path=ledger,
        trace_dir=tmp_path / "traces",
        raw_dir=raw_dir,
    )

    assert "Requests per minute | openai/gpt-oss-20b: 30 (published page shows 30; account limits page not opened; 429 for this limit not observed)" in output
    assert "Tokens per day | openai/gpt-oss-20b: 200000 (published page shows 200K; scaled after 1K and 8K matched saved headers; account limits page not opened; 429 for this limit not observed)" in output
    assert "Requests per day | openai/gpt-oss-20b: 1000" in output
    assert output.count("account limits page not opened") == 4
    assert "| medium |" in output


def test_request_reset_header_note_requires_one_spacing(tmp_path) -> None:
    ledger = tmp_path / "budget_ledger.csv"
    fieldnames = list(_ledger_row("", "", {}).keys())
    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for remaining, reset in (("999", "1m26.4s"), ("998", "2m52.8s"), ("997", "4m19.2s")):
            writer.writerow(
                _ledger_row(
                    "E1-latency",
                    "openai/gpt-oss-20b",
                    {
                        "x-ratelimit-limit-requests": "1000",
                        "x-ratelimit-remaining-requests": remaining,
                        "x-ratelimit-reset-requests": reset,
                    },
                )
            )
    result_path = tmp_path / "results.csv"
    write_results(result_path, [])
    output = render_tables(
        result_path,
        ledger_path=ledger,
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )

    assert "Across 3 saved real-experiment header rows" in output
    assert "within 0.01 seconds" in output
    assert "86.4 seconds" in output
    assert "24.0 hours" in output

    with ledger.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            _ledger_row(
                "E1-latency",
                "openai/gpt-oss-20b",
                {
                    "x-ratelimit-limit-requests": "1000",
                    "x-ratelimit-remaining-requests": "999",
                    "x-ratelimit-reset-requests": "10s",
                },
            )
        )
        for remaining, reset in (("998", "2m52.8s"), ("997", "4m19.2s")):
            writer.writerow(
                _ledger_row(
                    "E1-latency",
                    "openai/gpt-oss-20b",
                    {
                        "x-ratelimit-limit-requests": "1000",
                        "x-ratelimit-remaining-requests": remaining,
                        "x-ratelimit-reset-requests": reset,
                    },
                )
            )
    mismatched = render_tables(
        result_path,
        ledger_path=ledger,
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )
    assert "saved real-experiment header rows" not in mismatched


def test_retrieval_parameter_metadata_is_displayed_only_from_input_file(tmp_path) -> None:
    result_path = tmp_path / "results.csv"
    write_results(result_path, [result_row("r1", scenario="E5-test-R1", retrieval_setting="R1")])
    settings_path = tmp_path / "retrieval.yaml"
    settings_path.write_text(
        "retrieval_settings:\n  R1:\n    chunk_tokens: 256\n    top_k: 4\n    rerank: false\n",
        encoding="utf-8",
    )

    output = render_tables(
        result_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
        settings_path=settings_path,
    )

    assert "R1 (256 tokens; 4 pieces; re-rank no)" in output


def test_e5_quality_rows_are_excluded_from_load_tables_and_use_character_settings(tmp_path) -> None:
    result_path = tmp_path / "results.csv"
    write_results(
        result_path,
        [
            result_row("load", scenario="steady", source="stand-in", answer_grade="not_graded"),
            result_row("quality", scenario="E5-model-selection-R1-model-a", answer_grade=""),
        ],
    )
    settings_path = tmp_path / "retrieval.yaml"
    settings_path.write_text(
        "retrieval_settings:\n  R1:\n    piece_size_chars: 900\n    top_k: 3\n    rerank: false\n",
        encoding="utf-8",
    )

    output = render_tables(
        result_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
        settings_path=settings_path,
    )

    table2 = output.split("## Table 3:", 1)[0]
    table3 = output.split("## Table 3:", 1)[1]
    assert "E5-model-selection" not in table2
    assert "| steady (stand-in) |" in table2
    assert "R1 (900 characters; 3 pieces; re-rank no)" in table3
    assert "quality |" not in table3


def test_ttft_excludes_failed_elapsed_time_and_question_count_deduplicates_attempts(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    write_results(
        results_path,
        [
            result_row(
                "eval-R1-model-Q01",
                scenario="E5-model-selection-R1-model",
                first_token_ms="10",
                total_ms="100",
                answer_grade="",
            ),
            result_row(
                "eval-retry-R1-model-Q01",
                scenario="E5-model-selection-R1-model",
                first_token_ms="900",
                total_ms="900",
                status="rate_limited",
                answer_grade="",
            ),
            result_row(
                "eval-R1-model-Q02",
                scenario="E5-model-selection-R1-model",
                first_token_ms="30",
                total_ms="100",
                answer_grade="",
            ),
        ],
    )

    output = render_tables(
        results_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )

    assert "2 unique questions / 3 attempts (2 successful; graded n=0; ungraded attempts n=3)" in output
    assert "| 10.0 ms (n=2) / 30.0 ms (n=2) | 100.0 ms (n=3) / 900.0 ms (n=3) |" in output


def test_waterfall_selects_nearest_rank_p95_and_splits_at_first_token(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    write_results(
        results_path,
        [
            result_row("r1", scenario="burst", total_ms="100"),
            result_row("r2", scenario="burst", total_ms="200"),
            result_row("r3", scenario="burst", total_ms="300", first_token_ms="160"),
        ],
    )
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "r3.json").write_text(
        json.dumps(
            {
                "request_id": "r3",
                "scenario": "burst",
                "first_token_ms": 160,
                "spans": [
                    {"name": "queue", "kind": "wait", "start_ms": 0, "end_ms": 50},
                    {"name": "retrieve", "kind": "work", "start_ms": 50, "end_ms": 150},
                    {"name": "retry", "kind": "retry", "start_ms": 150, "end_ms": 200},
                    {"name": "draft", "kind": "work", "start_ms": 200, "end_ms": 300},
                ],
            }
        ),
        encoding="utf-8",
    )

    output = render_waterfall(results_path, trace_dir, scenario="burst")

    assert "`r3`" in output
    assert "nearest-rank p95 total_ms within scenario 'burst' (n=3)" in output
    assert "Wait: 50.0 ms; work: 200.0 ms; retry: 50.0 ms." in output
    assert "Before first token: 160.0 ms; after first token: 140.0 ms." in output


def test_waterfall_rejects_overlapping_spans_instead_of_double_counting(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    write_results(results_path, [result_row("overlap", scenario="burst", total_ms="200")])
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "overlap.json").write_text(
        json.dumps(
            {
                "request_id": "overlap",
                "spans": [
                    {"name": "parent", "kind": "work", "start_ms": 0, "end_ms": 200},
                    {"name": "child", "kind": "work", "start_ms": 50, "end_ms": 100},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Overlapping trace spans would double-count"):
        render_waterfall(results_path, trace_dir, request_id="overlap")


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "not-a-number"])
def test_results_reject_invalid_numeric_values(tmp_path, value) -> None:
    path = tmp_path / "results.csv"
    write_results(path, [result_row("bad-number", total_ms=value)])

    with pytest.raises(ValueError, match="Invalid numeric value for total_ms"):
        read_results(path)


def test_results_reject_invalid_start_time_and_first_token_after_total(tmp_path) -> None:
    path = tmp_path / "results.csv"
    write_results(path, [result_row("bad-time", start_time="not-a-date")])
    with pytest.raises(ValueError, match="Invalid start_time"):
        read_results(path)

    write_results(path, [result_row("bad-latency", first_token_ms="101", total_ms="100")])
    with pytest.raises(ValueError, match="first_token_ms exceeds total_ms"):
        read_results(path)


def test_retries_report_measured_sample_coverage(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    write_results(
        results_path,
        [
            result_row("retry-a", scenario="steady", retries="1"),
            result_row("retry-b", scenario="steady", retries=""),
        ],
    )

    output = render_tables(
        results_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )

    assert "1.00 mean (n=1 measured of 2; coverage=50.0%)" in output

    write_results(
        results_path,
        [
            result_row("retry-a", scenario="steady", retries="1"),
            result_row("retry-b", scenario="steady", retries="3"),
        ],
    )
    full_coverage_output = render_tables(
        results_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )
    assert "2.00 mean (n=2 measured of 2; coverage=100.0%)" in full_coverage_output


def test_checks_requires_matching_generated_block(tmp_path) -> None:
    results_path = tmp_path / "results.csv"
    write_results(results_path, [result_row("only")])
    writeup = tmp_path / "WRITEUP.md"
    generated = render_tables(
        results_path,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )
    writeup.write_text(f"# Report\n\n{generated}\nPersonal text.\n", encoding="utf-8")

    okay, message = check_writeup(
        results_path,
        writeup,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )
    assert okay
    assert "match" in message

    writeup.write_text(
        f"# Report\n\n{GENERATED_BEGIN}\nwrong\n{GENERATED_END}\n",
        encoding="utf-8",
    )
    okay, message = check_writeup(
        results_path,
        writeup,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
    )
    assert not okay
    assert "First difference" in message


def test_excluded_request_ids_are_removed_from_published_tables(tmp_path) -> None:
    results = tmp_path / "results.csv"
    exclusions = tmp_path / "excluded_runs.csv"
    write_results(
        results,
        [
            result_row("keep-steady-00000", source="stand-in", answer_grade="not_graded"),
            result_row("discard-steady-00000", source="stand-in", answer_grade="not_graded"),
        ],
    )
    exclusions.write_text(
        "request_id,reason\ndiscard-steady-00000,invalid synthetic run\n",
        encoding="utf-8",
    )

    assert read_excluded_request_ids(exclusions) == {"discard-steady-00000"}
    output = render_tables(results, excluded_runs_path=exclusions)

    assert "| steady | stand-in | 1 |" in output


def test_gold_refusal_rule_and_unknown_substantive_answer() -> None:
    grade, _notes = grade_saved_answer("Q31", "req-Q31", SAFE_REFUSAL)
    assert grade == "refused_correctly"
    grade, _notes = grade_saved_answer("Q02", "req-Q02", SAFE_REFUSAL)
    assert grade == "refused_wrongly"
    with pytest.raises(ValueError, match="Ungraded substantive"):
        grade_saved_answer("Q02", "req-Q02", "Six identical layers.")


def test_human_grades_overlay_does_not_rewrite_results_and_rejects_429(tmp_path) -> None:
    results = tmp_path / "results.csv"
    grades = tmp_path / "human_grades.csv"
    request_id = "stamp-R1-model-Q02"
    write_results(
        results,
        [
            result_row(
                request_id,
                scenario="E5-day1-model",
                answer_grade="",
                retrieval_setting="R1",
            ),
            result_row(
                "stamp-R1-model-Q04",
                scenario="E5-day1-model",
                status="rate_limited",
                answer_grade="",
                first_token_ms="",
                retrieval_setting="R1",
            ),
        ],
    )
    grades.write_text(
        "request_id,question_id,answer_grade,grader,notes\n"
        f"{request_id},Q02,correct,test,matches gold\n",
        encoding="utf-8",
    )
    before = results.read_text(encoding="utf-8")
    output = render_tables(
        results,
        ledger_path=tmp_path / "ledger.csv",
        trace_dir=tmp_path / "traces",
        raw_dir=tmp_path / "raw",
        human_grades_path=grades,
    )
    assert results.read_text(encoding="utf-8") == before
    assert "100.0% (grade n=1)" in output
    assert "ungraded attempts n=1" in output
    assert "joined from eval/human_grades.csv by request_id (1 grades)" in output

    grades.write_text(
        "request_id,question_id,answer_grade,grader,notes\n"
        "stamp-R1-model-Q04,Q04,wrong,test,should be rejected\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not a successful real E5"):
        apply_human_grades(read_results(results), grades)
