#!/usr/bin/env python3
"""Derive the assignment's results tables from results.csv and evidence files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_FIELDS = (
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
)
GRADES = {"correct", "wrong", "refused_correctly", "refused_wrongly"}
PERCENTILES = ((0.50, "p50"), (0.95, "p95"), (0.99, "p99"))
GENERATED_BEGIN = "<!-- BEGIN GENERATED RESULTS TABLES -->"
GENERATED_END = "<!-- END GENERATED RESULTS TABLES -->"
REQUIRED_SCENARIOS = (
    "steady",
    "burst",
    "sustained overload",
    "per-minute limit halved",
    "daily budget exhausted",
)
SCENARIO_ALIASES = {
    "sustained_overload": "sustained overload",
    "sustained-overload": "sustained overload",
    "rpm_halved": "per-minute limit halved",
    "per_minute_limit_halved": "per-minute limit halved",
    "per-minute-limit-halved": "per-minute limit halved",
    "requests-per-minute-halved": "per-minute limit halved",
    "daily_exhaustion": "daily budget exhausted",
    "daily_budget_exhausted": "daily budget exhausted",
    "daily-budget-exhausted": "daily budget exhausted",
}


def read_results(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != RESULT_FIELDS:
            raise ValueError(
                f"{path} has an unexpected results.csv header. "
                f"Expected: {','.join(RESULT_FIELDS)}"
            )
        rows = list(reader)
    seen_request_ids: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Malformed row at {path}:{line_number}")
        request_id = row["request_id"]
        if not request_id or request_id in seen_request_ids:
            raise ValueError(f"Missing or duplicate request_id at {path}:{line_number}")
        seen_request_ids.add(request_id)
        start_time = row["start_time"].strip()
        if not re.search(r"^\d{4}-\d\d-\d\dT.*\.\d{3,}(?:Z|\+00:00)$", start_time):
            raise ValueError(
                f"Invalid start_time at {path}:{line_number}; expected ISO-8601 UTC with milliseconds"
            )
        if _parse_time(start_time) is None:
            raise ValueError(f"Invalid start_time at {path}:{line_number}: {start_time!r}")
        parsed_numbers: dict[str, float] = {}
        for field in ("first_token_ms", "total_ms", "retries", "tokens_in", "tokens_out"):
            raw_value = row[field].strip()
            if not raw_value:
                continue
            try:
                numeric = float(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid numeric value for {field} at {path}:{line_number}: {raw_value!r}"
                ) from exc
            if not math.isfinite(numeric) or numeric < 0:
                raise ValueError(
                    f"Invalid numeric value for {field} at {path}:{line_number}: {raw_value!r}"
                )
            if field == "retries" and not numeric.is_integer():
                raise ValueError(
                    f"retries must be an integer at {path}:{line_number}: {raw_value!r}"
                )
            parsed_numbers[field] = numeric
        if (
            parsed_numbers.get("first_token_ms") is not None
            and parsed_numbers.get("total_ms") is not None
            and parsed_numbers["first_token_ms"] > parsed_numbers["total_ms"]
        ):
            raise ValueError(
                f"first_token_ms exceeds total_ms at {path}:{line_number}"
            )
        if row["source"] not in {"real", "stand-in"}:
            raise ValueError(f"Invalid source at {path}:{line_number}: {row['source']!r}")
        if row["status"] not in {"ok", "rate_limited", "timeout", "error"}:
            raise ValueError(f"Invalid status at {path}:{line_number}: {row['status']!r}")
        if row["answer_grade"] not in GRADES | {"not_graded", ""}:
            raise ValueError(
                f"Invalid answer_grade at {path}:{line_number}: {row['answer_grade']!r}"
            )
        if row["source"] == "stand-in" and row["answer_grade"] not in {"not_graded", ""}:
            raise ValueError(
                f"Stand-in row must use not_graded at {path}:{line_number}"
            )
    return rows


def apply_human_grades(
    rows: list[dict[str, str]],
    grades_path: str | Path,
) -> tuple[list[dict[str, str]], int]:
    """Overlay eval/human_grades.csv onto a copy of the rows.

    results.csv stays unchanged. Only successful real E5 rows can receive a grade.
    """
    path = Path(grades_path)
    if not path.exists():
        return rows, 0
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"request_id", "question_id", "answer_grade", "grader", "notes"}
        if reader.fieldnames is None or required - set(reader.fieldnames):
            raise ValueError(f"{path} must include columns {sorted(required)}")
        grades: dict[str, tuple[str, str]] = {}
        for line_number, record in enumerate(reader, start=2):
            request_id = (record.get("request_id") or "").strip()
            question_id = (record.get("question_id") or "").strip()
            grade = (record.get("answer_grade") or "").strip()
            if not request_id or not question_id or not grade:
                raise ValueError(f"Incomplete human grade at {path}:{line_number}")
            if request_id in grades:
                raise ValueError(f"Duplicate human grade at {path}:{line_number}: {request_id}")
            if grade not in GRADES:
                raise ValueError(f"Invalid human grade at {path}:{line_number}: {grade!r}")
            grades[request_id] = (grade, question_id)
    if not grades:
        return rows, 0
    by_id = {row["request_id"]: row for row in rows}
    missing = sorted(set(grades) - set(by_id))
    if missing:
        raise ValueError(f"Human grades name request IDs missing from results: {missing[:5]}")
    updated: list[dict[str, str]] = []
    for row in rows:
        item = grades.get(row["request_id"])
        if item is None:
            updated.append(row)
            continue
        grade, question_id = item
        if row["status"] != "ok" or row["source"] != "real" or not row["scenario"].startswith("E5-"):
            raise ValueError(
                f"Human grade {row['request_id']} is not a successful real E5 answer "
                f"(status={row['status']}, source={row['source']}, scenario={row['scenario']})"
            )
        match = re.search(r"-(Q\d+)$", row["request_id"])
        if match is None or match.group(1) != question_id:
            raise ValueError(
                f"Human grade question_id {question_id} does not match {row['request_id']}"
            )
        existing = row["answer_grade"]
        if existing not in {"", "not_graded"} and existing != grade:
            raise ValueError(
                f"Human grade conflicts with results.csv for {row['request_id']}: "
                f"{existing} vs {grade}"
            )
        graded = dict(row)
        graded["answer_grade"] = grade
        updated.append(graded)
    return updated, len(grades)


def read_excluded_request_ids(path: str | Path) -> set[str]:
    """Read exact request IDs excluded from published aggregates."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != ("request_id", "reason"):
            raise ValueError(f"{path} must have the header request_id,reason")
        excluded: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            if None in row or not row.get("request_id", "").strip() or not row.get("reason", "").strip():
                raise ValueError(f"Malformed exclusion at {path}:{line_number}")
            request_id = row["request_id"].strip()
            if request_id in excluded:
                raise ValueError(f"Duplicate excluded request_id at {path}:{line_number}: {request_id}")
            excluded.add(request_id)
    return excluded


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nearest_rank(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    rank = math.ceil(quantile * len(ordered))
    return ordered[rank - 1]


def percentile_cell(values: Iterable[float], quantile: float, label: str) -> str:
    ordered = list(values)
    count = len(ordered)
    if label == "p99" and count < 100:
        return f"not measured (n<100; n={count})"
    value = nearest_rank(ordered, quantile)
    if value is None:
        return "not measured (n=0)"
    return f"{value:.1f} ms (n={count})"


def _fmt_number(value: float | None, *, digits: int = 1, suffix: str = "") -> str:
    return "not measured" if value is None else f"{value:.{digits}f}{suffix}"


def _fmt_duration_and_rate(duration: float | None, request_count: int) -> tuple[str, str]:
    """Print a duration that still reproduces the one-decimal offered rate."""
    if duration is None or duration <= 0 or request_count <= 0:
        return "not measured", "not measured"
    rate_text = f"{request_count / duration * 60:.1f}"
    for digits in range(1, 7):
        duration_text = f"{duration:.{digits}f}"
        printed = float(duration_text)
        if printed > 0 and f"{request_count / printed * 60:.1f}" == rate_text:
            return duration_text, rate_text
    return f"{duration:.6f}", rate_text


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scenario_groups(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        original = row["scenario"]
        scenario = SCENARIO_ALIASES.get(original.casefold(), original)
        grouped[(scenario, row["source"])].append(row)
    measured_scenarios = {scenario for scenario, _ in grouped}
    for scenario in REQUIRED_SCENARIOS:
        if scenario not in measured_scenarios:
            grouped[(scenario, "not measured")] = []
    return dict(sorted(grouped.items()))


def _read_json(path: Path) -> Any | None:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return None


def _json_key_line(path: Path, key: str) -> int:
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if f'"{key}"' in line:
                return line_number
    except OSError:
        pass
    return 1


def _trace_index(trace_dir: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(trace_dir)
    traces: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return traces
    paths = list(root.glob("*.json"))
    # E5 review traces remain under the append-only raw experiment folders.
    raw_root = root.parent / "raw"
    if raw_root.exists():
        paths.extend(raw_root.rglob("traces/*.json"))
    for path in sorted(paths):
        data = _read_json(path)
        if isinstance(data, dict) and isinstance(data.get("request_id"), str):
            data["_path"] = str(path)
            traces[data["request_id"]] = data
    return traces


def _trace_queue_length(trace: Mapping[str, Any]) -> int | None:
    values: list[int] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).casefold().replace("-", "_")
                if "queue" in normalized and ("length" in normalized or "depth" in normalized):
                    numeric = _number(child)
                    if numeric is not None and numeric >= 0:
                        values.append(int(numeric))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(trace)
    return max(values) if values else None


def _scenario_peak_queue(rows: list[dict[str, str]], traces: Mapping[str, dict[str, Any]]) -> str:
    queue_values = [
        value
        for row in rows
        if row["request_id"] in traces
        if (value := _trace_queue_length(traces[row["request_id"]])) is not None
    ]
    return str(max(queue_values)) if queue_values else "not measured"


def _trace_cost_detail(
    trace: Mapping[str, Any], prices: Mapping[str, Mapping[str, Any]]
) -> tuple[float | None, bool]:
    if trace.get("cache_hit") or trace.get("coalesced") or trace.get("level") == "extractive":
        return 0.0, True
    spans = trace.get("spans")
    if not isinstance(spans, list):
        return None, False
    total = 0.0
    seen = False
    complete = True
    for span in spans:
        if not isinstance(span, Mapping) or not span.get("model"):
            continue
        model = str(span["model"])
        price = prices.get(model)
        tokens_in = _number(span.get("tokens_in"))
        tokens_out = _number(span.get("tokens_out"))
        if price is None or tokens_in is None or tokens_out is None:
            complete = False
            continue
        input_rate = _number(price.get("input_usd_per_million"))
        output_rate = _number(price.get("output_usd_per_million"))
        if input_rate is None or output_rate is None:
            complete = False
            continue
        total += tokens_in * input_rate / 1_000_000
        total += tokens_out * output_rate / 1_000_000
        seen = True
    return (total if seen else None), complete and seen


def _read_prices(path: str | Path | None) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    if path is None or not Path(path).exists():
        return {}, None
    data = _read_json(Path(path))
    if not isinstance(data, Mapping) or not isinstance(data.get("models"), Mapping):
        raise ValueError("Price file must contain a 'models' mapping")
    if not data.get("source_url") or not data.get("price_date"):
        raise ValueError("Price file must include source_url and price_date")
    models = dict(data["models"])
    for model, price in models.items():
        if not isinstance(price, Mapping):
            raise ValueError(f"Price entry for {model!r} must be a mapping")
        if _number(price.get("input_usd_per_million")) is None or _number(
            price.get("output_usd_per_million")
        ) is None:
            raise ValueError(f"Price entry for {model!r} must contain numeric input/output rates")
    return models, f"Price inputs: {data['source_url']} (checked {data['price_date']})."


def _read_retrieval_settings(path: str | Path | None) -> dict[str, Mapping[str, Any]]:
    if path is None or not Path(path).exists():
        return {}
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read retrieval settings from {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("Retrieval settings file must contain a mapping")
    settings = data.get("retrieval_settings", data.get("settings", data))
    if not isinstance(settings, Mapping) or not all(
        isinstance(value, Mapping) for value in settings.values()
    ):
        raise ValueError("Retrieval settings must map setting IDs to parameter mappings")
    return dict(settings)


def _setting_label(setting: str, metadata: Mapping[str, Mapping[str, Any]]) -> str:
    spec = metadata.get(setting)
    if spec is None:
        return (
            f"{setting} (piece size not measured; pieces fetched not measured; "
            "re-rank not measured)"
        )
    size = spec.get(
        "piece_size_tokens",
        spec.get("piece_size_chars", spec.get("chunk_tokens", spec.get("piece_size"))),
    )
    pieces = spec.get("pieces_fetched", spec.get("top_k", spec.get("k")))
    rerank = spec.get("rerank", spec.get("rerank_enabled"))
    unit = "characters" if "piece_size_chars" in spec else "tokens"
    size_text = f"{size} {unit}" if size is not None else "piece size not measured"
    pieces_text = str(pieces) if pieces is not None else "not measured"
    rerank_text = ("yes" if rerank else "no") if isinstance(rerank, bool) else "not measured"
    return f"{setting} ({size_text}; {pieces_text} pieces; re-rank {rerank_text})"


def _cost_per_1000(
    rows: list[dict[str, str]],
    traces: Mapping[str, dict[str, Any]],
    prices: Mapping[str, Mapping[str, Any]],
) -> str:
    if not prices:
        return "not measured (price file missing)"
    successful_count = sum(row["status"] == "ok" for row in rows)
    if not successful_count:
        return "not measured (no successful responses)"
    known_total = 0.0
    complete_count = 0
    unknown_count = 0
    for row in rows:
        trace = traces.get(row["request_id"])
        if trace is None:
            unknown_count += 1
            continue
        cost, complete = _trace_cost_detail(trace, prices)
        if cost is not None:
            known_total += cost
        if complete:
            complete_count += 1
        else:
            unknown_count += 1
    if not complete_count and not known_total:
        return f"not measured (known-cost n=0/{len(rows)} attempts; unknown-cost n={unknown_count})"
    coverage = complete_count / len(rows) * 100 if rows else 0.0
    normalized = known_total / successful_count * 1000
    if unknown_count:
        return (
            f"partial ${normalized:.4f} known cost per 1,000 successful responses "
            f"(complete-cost n={complete_count}/{len(rows)} attempts; unknown-cost n={unknown_count}; "
            f"coverage={coverage:.1f}%)"
        )
    return f"${normalized:.4f} per 1,000 successful responses (n={successful_count})"


PUBLIC_RATE_LIMITS_FILENAME = "groq-public-rate-limits.json"
_RESET_PARTS = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _is_real_experiment(experiment: str) -> bool:
    return experiment.startswith("phase0-") or re.match(r"^E[1-7](?:\b|[-_])", experiment) is not None


def _parse_reset_seconds(value: str) -> float | None:
    text = value.strip()
    matches = _RESET_PARTS.findall(text)
    if not matches:
        return None
    if "".join(f"{amount}{unit}" for amount, unit in matches) != text:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(amount) * scale[unit] for amount, unit in matches)


def _parse_doc_quantity(display: str) -> float | None:
    """Read a page quantity such as 30, 1K, or 200K. K means 1000."""
    text = str(display).strip().replace(",", "")
    if text.upper().endswith("K"):
        base = _number(text[:-1])
        return None if base is None else base * 1000
    return _number(text)


def _load_public_rate_limits(raw_dir: Path) -> dict[str, Any] | None:
    """Read a saved public rate-limit page. Header evidence still wins when present."""
    path = raw_dir / PUBLIC_RATE_LIMITS_FILENAME
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must be a JSON object")
    if data.get("kind") != "public_documentation":
        raise ValueError(f"{path} kind must be public_documentation")
    source_url = data.get("source_url")
    checked_date = data.get("checked_date")
    models = data.get("models")
    if not source_url or not checked_date or not isinstance(models, Mapping) or not models:
        raise ValueError(f"{path} needs source_url, checked_date, and a models mapping")
    parsed: dict[str, dict[str, str]] = {}
    required_displays = ("rpm_display", "rpd_display", "tpm_display", "tpd_display")
    for model, limits in models.items():
        if not isinstance(limits, Mapping):
            raise ValueError(f"{path} limits for {model} must be an object")
        displays = {field: limits.get(field) for field in required_displays}
        if any(not isinstance(value, str) or not value.strip() for value in displays.values()):
            raise ValueError(f"{path} limits for {model} need display strings for rpm, rpd, tpm, and tpd")
        parsed[str(model)] = {field: str(value) for field, value in displays.items()}
    return {
        "path": path,
        "source_url": str(source_url),
        "checked_date": str(checked_date),
        "models": parsed,
    }


def _daily_request_refill_note(ledger_path: Path) -> str | None:
    """Describe the saved request-reset header when every row follows one spacing."""
    if not ledger_path.exists():
        return None
    ratios: list[float] = []
    limits: set[int] = set()
    try:
        with ledger_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if not _is_real_experiment(row.get("experiment", "")):
                    continue
                try:
                    headers = json.loads(row.get("headers_json", "{}"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(headers, Mapping):
                    continue
                limit = _number(headers.get("x-ratelimit-limit-requests"))
                remaining = _number(headers.get("x-ratelimit-remaining-requests"))
                reset = headers.get("x-ratelimit-reset-requests")
                if limit is None or remaining is None or not isinstance(reset, str):
                    continue
                deficit = limit - remaining
                if deficit <= 0:
                    continue
                seconds = _parse_reset_seconds(reset)
                if seconds is None:
                    return None
                ratios.append(seconds / deficit)
                if float(limit).is_integer():
                    limits.add(int(limit))
    except OSError:
        return None
    if len(ratios) < 3 or max(ratios) - min(ratios) > 0.01:
        return None
    spacing = sum(ratios) / len(ratios)
    message = (
        f"Across {len(ratios)} saved real-experiment header rows with a positive request deficit "
        f"and a parseable x-ratelimit-reset-requests value, the reset spacing agreed within "
        f"0.01 seconds of {spacing:.1f} seconds per spent request."
    )
    if len(limits) == 1:
        limit = next(iter(limits))
        window_hours = limit * spacing / 3600
        if abs(limit * spacing - 86400) < 1:
            message += (
                f" At the observed limit of {limit}, the computed refill is {window_hours:.1f} hours."
            )
    message += " A recovery wait after the daily request bucket reached zero was not measured."
    return message


def _table1(ledger_path: str | Path, raw_dir: str | Path) -> str:
    """Read limits from saved headers, raw 429 files, and an optional public-docs file."""
    evidence: dict[str, dict[str, tuple[str, str, str]]] = defaultdict(dict)
    ledger = Path(ledger_path)
    ledger_limit_response: tuple[str, str, str] | None = None
    if ledger.exists():
        with ledger.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            for line_no, row in enumerate(reader, start=2):
                experiment = row.get("experiment", "")
                if not _is_real_experiment(experiment):
                    continue
                model = row.get("model", "unknown model")
                try:
                    headers = json.loads(row.get("headers_json", "{}"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(headers, Mapping):
                    continue
                if row.get("status") == "rate_limited":
                    retry_after = headers.get("retry-after")
                    token_limit = headers.get("x-ratelimit-limit-tokens")
                    details = ["HTTP 429; response body not captured"]
                    if retry_after is not None:
                        details.append(f"Retry-After: {retry_after} s")
                    if token_limit is not None:
                        details.append(f"token limit header: {token_limit} per minute")
                    ledger_limit_response = (
                        "; ".join(details),
                        f"{ledger}:{line_no} status + headers_json",
                        "high",
                    )
                header_map = {
                    "Requests per minute": ("x-ratelimit-limit-rpm", "x-ratelimit-limit-requests-minute"),
                    "Tokens per minute": ("x-ratelimit-limit-tokens", "x-ratelimit-limit-tpm"),
                    "Requests per day": ("x-ratelimit-limit-requests", "x-ratelimit-limit-rpd"),
                    "Tokens per day": ("x-ratelimit-limit-tpd", "x-ratelimit-limit-tokens-day"),
                    "Requests in flight at once": ("x-ratelimit-limit-inflight", "x-ratelimit-limit-concurrent"),
                }
                for label, names in header_map.items():
                    matched_name = next((name for name in names if name in headers), None)
                    if matched_name is not None:
                        evidence[label][model] = (
                            str(headers[matched_name]),
                            f"{ledger}:{line_no} headers_json ({matched_name})",
                            "high",
                        )

    # Probe raw records may add observed 429 behavior, recovery, or in-flight limits.
    raw_root = Path(raw_dir)
    raw_limit_response: tuple[str, str, str] | None = None
    raw_recovery: tuple[str, str, str] | None = None
    if raw_root.exists():
        for path in sorted(raw_root.glob("*.json")):
            data = _read_json(path)
            if not isinstance(data, Mapping):
                continue
            experiment = str(data.get("experiment", ""))
            if data.get("source") not in {"real", "groq"} and not _is_real_experiment(experiment):
                continue
            status = _number(data.get("status_code", data.get("http_status")))
            body = data.get("body")
            headers = data.get("headers", {})
            if status == 429:
                detail = body
                if isinstance(body, str):
                    try:
                        parsed_body = json.loads(body)
                        if isinstance(parsed_body, Mapping):
                            detail = parsed_body.get("error", parsed_body.get("message", body))
                            if isinstance(detail, Mapping):
                                detail = detail.get("message", detail)
                    except json.JSONDecodeError:
                        pass
                raw_limit_response = (
                    f"HTTP 429: {detail}",
                    f"{path}:{_json_key_line(path, 'status_code')} status_code; "
                    f"{path}:{_json_key_line(path, 'body')} body",
                    "high",
                )
            if isinstance(data.get("time_until_recovery_s"), (int, float, str)):
                numeric = _number(data.get("time_until_recovery_s"))
                if numeric is not None:
                    raw_recovery = (
                        f"{numeric:.1f} s",
                        f"{path}:{_json_key_line(path, 'time_until_recovery_s')} time_until_recovery_s",
                        "medium",
                    )
            if isinstance(headers, Mapping):
                in_flight = headers.get("x-ratelimit-limit-inflight")
                if in_flight is not None:
                    evidence["Requests in flight at once"]["observed"] = (
                        str(in_flight),
                        f"{path}:{_json_key_line(path, 'headers')} headers.x-ratelimit-limit-inflight",
                        "high",
                    )

    if raw_limit_response:
        evidence["What it returns when a limit is hit"]["observed"] = raw_limit_response
    elif ledger_limit_response:
        evidence["What it returns when a limit is hit"]["observed"] = ledger_limit_response
    if raw_recovery:
        evidence["Time until it recovers"]["observed"] = raw_recovery

    public_limits = _load_public_rate_limits(raw_root) if raw_root.exists() else None
    if public_limits is not None and not evidence.get("Requests per minute") and not evidence.get("Tokens per day"):
        confirmed: list[tuple[str, Mapping[str, str], float, float]] = []
        for model, limits in sorted(public_limits["models"].items()):
            parsed_rpd = _parse_doc_quantity(limits["rpd_display"])
            parsed_tpm = _parse_doc_quantity(limits["tpm_display"])
            parsed_rpm = _parse_doc_quantity(limits["rpm_display"])
            parsed_tpd = _parse_doc_quantity(limits["tpd_display"])
            observed_rpd = evidence.get("Requests per day", {}).get(model)
            observed_tpm = evidence.get("Tokens per minute", {}).get(model)
            rpd_ok = (
                parsed_rpd is not None
                and observed_rpd is not None
                and _number(observed_rpd[0]) == parsed_rpd
            )
            tpm_ok = (
                parsed_tpm is not None
                and observed_tpm is not None
                and _number(observed_tpm[0]) == parsed_tpm
            )
            if rpd_ok and tpm_ok and parsed_rpm is not None and parsed_tpd is not None:
                confirmed.append((model, limits, parsed_rpm, parsed_tpd))
        citation = (
            f"{public_limits['path']}; {public_limits['source_url']} "
            f"checked {public_limits['checked_date']}; account limits page not opened"
        )
        if confirmed and len(confirmed) == len(public_limits["models"]):
            for model, limits, rpm, tpd in confirmed:
                evidence["Requests per minute"][model] = (
                    (
                        f"{rpm:.0f} (published page shows {limits['rpm_display']}; "
                        "account limits page not opened; 429 for this limit not observed)"
                    ),
                    citation,
                    "medium",
                )
                evidence["Tokens per day"][model] = (
                    (
                        f"{tpd:.0f} (published page shows {limits['tpd_display']}; "
                        f"scaled after {limits['rpd_display']} and {limits['tpm_display']} "
                        "matched saved headers; account limits page not opened; "
                        "429 for this limit not observed)"
                    ),
                    citation,
                    "medium",
                )

    labels = [
        "Requests per minute",
        "Tokens per minute",
        "Requests per day",
        "Tokens per day",
        "Requests in flight at once",
        "What it returns when a limit is hit",
        "Time until it recovers",
    ]
    lines = [
        "| Limit | Value you found | How you found it | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    for label in labels:
        by_model = evidence.get(label, {})
        if not by_model:
            lines.append(f"| {label} | not measured | not measured | low |")
            continue
        value = "; ".join(f"{model}: {item[0]}" for model, item in sorted(by_model.items()))
        how_parts: list[str] = []
        for _, item in sorted(by_model.items()):
            if item[1] not in how_parts:
                how_parts.append(item[1])
        how = "; ".join(how_parts)
        confidence = "high" if all(item[2] == "high" for item in by_model.values()) else "medium"
        lines.append(f"| {label} | {value} | {how} | {confidence} |")
    return "\n".join(lines)


def _table2a(groups: Mapping[tuple[str, str], list[dict[str, str]]]) -> str:
    lines = [
        "| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for (scenario, source), rows in groups.items():
        runs: dict[str, list[dict[str, str]]] = defaultdict(list)
        unknown_run_identity = False
        for index, row in enumerate(rows):
            run_key = _loadgen_run_key(row)
            if run_key is None:
                unknown_run_identity = True
                run_key = f"unknown-{index}"
            runs[run_key].append(row)
        run_durations: list[float] = []
        incomplete_run = unknown_run_identity
        for run_rows in runs.values():
            starts = [_parse_time(row["start_time"]) for row in run_rows]
            valid_starts = [value for value in starts if value is not None]
            finishes = [
                start + timedelta(milliseconds=total)
                for row, start in zip(run_rows, starts)
                if start is not None and (total := _number(row["total_ms"])) is not None
            ]
            if len(valid_starts) < 2:
                incomplete_run = True
                continue
            valid_finishes = finishes or valid_starts
            run_duration = (max(valid_finishes) - min(valid_starts)).total_seconds()
            if run_duration > 0:
                run_durations.append(run_duration)
            else:
                incomplete_run = True
        duration = (
            sum(run_durations)
            if run_durations and not incomplete_run
            else None
        )
        duration_text, rate_text = _fmt_duration_and_rate(duration, len(rows))
        # The relay records elapsed failure time in first_token_ms on some
        # failed requests. Only successful answers establish delivered TTFT.
        first_values = [
            value
            for row in rows
            if row["status"] == "ok"
            if (value := _number(row["first_token_ms"])) is not None
        ]
        total_values = [value for row in rows if (value := _number(row["total_ms"])) is not None]
        first = " / ".join(percentile_cell(first_values, q, label) for q, label in PERCENTILES)
        total = " / ".join(percentile_cell(total_values, q, label) for q, label in PERCENTILES)
        lines.append(
            f"| {scenario} | {source} | {len(rows)} | {duration_text} | "
            f"{rate_text} | {first} | {total} |"
        )
    return "\n".join(lines)


def _loadgen_run_key(row: Mapping[str, str]) -> str | None:
    """Recover one run prefix so separate repetitions are not treated as one long run.

    Loadgen ids look like ``<run>-<scenario>-00000``. The real steady runner
    in ``load/run_real_steady.py`` writes ``E6-<stamp>-00000`` and stores the
    scenario name ``steady`` in its own column.
    """
    scenario = re.sub(r"[^A-Za-z0-9._-]+", "_", row.get("scenario", ""))
    request_id = row.get("request_id", "")
    match = re.match(rf"^(.*)-{re.escape(scenario)}-\d{{5}}$", request_id)
    if match:
        return match.group(1)
    if scenario == "steady":
        e6_match = re.match(r"^(E6-\d{8}T\d{12}Z)-\d{5}$", request_id)
        if e6_match:
            return e6_match.group(1)
    return None


def _table2b(
    groups: Mapping[tuple[str, str], list[dict[str, str]]],
    traces: Mapping[str, dict[str, Any]],
    prices: Mapping[str, Mapping[str, Any]],
) -> str:
    lines = [
        "| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (scenario, source), rows in groups.items():
        count = len(rows)
        denom = count or 1
        succeeded = sum(row["status"] == "ok" for row in rows)
        rate_limited = sum(row["status"] == "rate_limited" for row in rows)
        timed_out = sum(row["status"] == "timeout" for row in rows)
        retries = [_number(row["retries"]) for row in rows]
        retry_vals = [value for value in retries if value is not None]
        answer_tokens = [
            input_tokens + output_tokens
            for row in rows
            if row["status"] == "ok"
            if (input_tokens := _number(row["tokens_in"])) is not None
            if (output_tokens := _number(row["tokens_out"])) is not None
        ]
        errors = sum(row["status"] == "error" for row in rows)
        notes = f"Error rows not shown in the brief's outcome columns: {errors}." if errors else ""
        percent = lambda value: f"{value / denom * 100:.1f}% (n={count})" if count else "not measured"
        retry_mean = sum(retry_vals) / len(retry_vals) if retry_vals else None
        retry_coverage = len(retry_vals) / count * 100 if count else None
        retry_text = (
            f"{retry_mean:.2f} mean (n={len(retry_vals)} measured of {count}; "
            f"coverage={retry_coverage:.1f}%)"
            if retry_mean is not None
            else f"not measured (n=0 measured of {count}; coverage={_fmt_number(retry_coverage, suffix='%')})"
        )
        lines.append(
            f"| {scenario} ({source}) | {percent(succeeded)} | {percent(rate_limited)} | {percent(timed_out)} | "
            f"{retry_text} | "
            f"{_scenario_peak_queue(rows, traces)} | "
            f"{_fmt_number(sum(answer_tokens) / len(answer_tokens) if answer_tokens else None)} "
            f"(n={len(answer_tokens)}) | "
            f"{_cost_per_1000(rows, traces, prices) if source == 'real' else 'not measured (stand-in; no real provider usage)'} |"
            + (f"\n| *Note* | {notes} |  |  |  |  |  |  |" if notes else "")
        )
    return "\n".join(lines)


def _table3(
    rows: list[dict[str, str]],
    traces: Mapping[str, dict[str, Any]],
    settings_metadata: Mapping[str, Mapping[str, Any]],
    prices: Mapping[str, Mapping[str, Any]],
) -> str:
    settings: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        settings[row["retrieval_setting"] or "not specified"].append(row)
    lines = [
        "| Setting (piece size, pieces fetched, re-rank yes or no) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) | Cost per 1,000 successful answers |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for setting, group in sorted(settings.items()):
        graded = [row for row in group if row["answer_grade"] in GRADES]
        grade_n = len(graded)
        denominator = grade_n or 1
        correct = sum(row["answer_grade"] == "correct" for row in graded)
        wrong = sum(row["answer_grade"] in {"wrong", "refused_wrongly"} for row in graded)
        refused = sum(row["answer_grade"] == "refused_correctly" for row in graded)
        tokens = []
        for row in group:
            if row["status"] != "ok":
                continue
            input_tokens = _number(row["tokens_in"])
            output_tokens = _number(row["tokens_out"])
            if input_tokens is not None and output_tokens is not None:
                tokens.append(input_tokens + output_tokens)
        # Failed E5 attempts can have elapsed failure time in first_token_ms;
        # report delivered-answer TTFT from successful responses only.
        first_values = [
            value
            for row in group
            if row["status"] == "ok"
            if (value := _number(row["first_token_ms"])) is not None
        ]
        total_values = [value for row in group if (value := _number(row["total_ms"])) is not None]
        pct = lambda value: f"{value / denominator * 100:.1f}% (grade n={grade_n})" if grade_n else "not measured (grade n=0)"
        quality_note = sum(row["answer_grade"] in {"not_graded", ""} for row in group)
        question_ids = {
            match.group(1) if (match := re.search(r"-(Q\d+)$", row["request_id"])) else row["request_id"]
            for row in group
        }
        asked = len(question_ids)
        successful = sum(row["status"] == "ok" for row in group)
        first = " / ".join(
            percentile_cell(first_values, quantile, label)
            for quantile, label in PERCENTILES[:2]
        )
        total = " / ".join(
            percentile_cell(total_values, quantile, label)
            for quantile, label in PERCENTILES[:2]
        )
        lines.append(
            f"| {_setting_label(setting, settings_metadata)} | {asked} unique questions / {len(group)} attempts "
            f"({successful} successful; graded n={grade_n}; ungraded attempts n={quality_note}) | "
            f"{pct(correct)} | {pct(wrong)} | {pct(refused)} | "
            f"{_fmt_number(sum(tokens) / len(tokens) if tokens else None)} (n={len(tokens)}) | "
            f"{first} | {total} | {_cost_per_1000(group, traces, prices)} |"
        )
    for missing_index in range(max(0, 3 - len(settings))):
        lines.append(
            f"| Additional retrieval setting {missing_index + 1} | not measured | not measured | "
            f"not measured | not measured | not measured | not measured | not measured | not measured |"
        )
    return "\n".join(lines)


def render_tables(
    results_path: str | Path,
    *,
    ledger_path: str | Path | None = None,
    trace_dir: str | Path | None = None,
    raw_dir: str | Path | None = None,
    prices_path: str | Path | None = None,
    settings_path: str | Path | None = None,
    excluded_runs_path: str | Path | None = None,
    human_grades_path: str | Path | None = None,
) -> str:
    rows = read_results(results_path)
    if excluded_runs_path:
        exclusion_path = Path(excluded_runs_path)
    elif Path(results_path).resolve() == (PROJECT_ROOT / "results" / "results.csv").resolve():
        exclusion_path = PROJECT_ROOT / "results" / "excluded_runs.csv"
    else:
        exclusion_path = Path("__no_exclusions__")
    excluded_ids = read_excluded_request_ids(exclusion_path) if exclusion_path.exists() else set()
    found_ids = {row["request_id"] for row in rows}
    unknown_exclusions = excluded_ids - found_ids
    if unknown_exclusions:
        raise ValueError(f"Excluded request IDs are missing from results: {sorted(unknown_exclusions)[:5]}")
    rows = [row for row in rows if row["request_id"] not in excluded_ids]
    # Published tables read answer_grade from results.csv. An explicit grades
    # path is only for tests of the one-time overlay; it is not the default.
    applied_grades = 0
    if human_grades_path:
        rows, applied_grades = apply_human_grades(rows, human_grades_path)
    # E5 model-selection rows are quality evaluations, not load scenarios.
    load_rows = [row for row in rows if not row["scenario"].startswith("E5-")]
    quality_rows = [row for row in rows if row["scenario"].startswith("E5-")]
    groups = _scenario_groups(load_rows)
    traces = _trace_index(trace_dir or PROJECT_ROOT / "results" / "traces")
    prices, price_note = _read_prices(prices_path)
    settings_metadata = _read_retrieval_settings(settings_path)
    sections = [
        "## Table 1: Limits you found in the real service\n\n"
        + _table1(ledger_path or PROJECT_ROOT / "budget_ledger.csv", raw_dir or PROJECT_ROOT / "results" / "raw"),
        "## Table 2a: Latency by load scenario\n\n" + _table2a(groups),
        "## Table 2b: Outcomes by load scenario\n\n" + _table2b(groups, traces, prices),
        "## Table 3: Retrieval settings\n\n" + _table3(quality_rows, traces, settings_metadata, prices),
    ]
    notes = [
        "Percentiles use the nearest-rank method. Each percentile reports its own sample size; p99 is shown only when n ≥ 100.",
        "Quality percentages use graded rows only. Stand-in `not_graded` rows remain in Questions asked but are excluded from grade denominators.",
        "Wrong % includes refused_wrongly, a refusal of a question the gold key can answer. Correctly refused % includes only refused_correctly.",
        "The outcome table's timed-out column includes only `timeout`; `error` counts are called out separately because the required table has no error column.",
        "A metric is `not measured` when the required source data is absent. Percentile n<100 is explicitly marked.",
        "Scenario duration runs from the first scheduled arrival in a run to that run's last completion. Separate run ids are summed. Duration keeps enough decimals that requests divided by the printed duration, times 60, rounds to the printed offered rate.",
    ]
    if applied_grades:
        notes.append(
            f"Table 3 grades are joined from eval/human_grades.csv by request_id ({applied_grades} grades). "
            "results.csv grade cells stay blank. HTTP 429 rows are not graded."
        )
    if price_note:
        notes.append(
            price_note
            + " Per-answer cost is a hypothetical published-price estimate; the measured runs used the free tier. "
            + "Known spend from failed attempts is included when traces expose token counts; missing usage is excluded and marked unknown."
        )
    refill_note = _daily_request_refill_note(
        Path(ledger_path or PROJECT_ROOT / "budget_ledger.csv")
    )
    if refill_note:
        notes.append(refill_note)
    return (
        f"{GENERATED_BEGIN}\n\n"
        + "\n\n".join(sections)
        + "\n\n### Notes\n\n"
        + "\n".join(f"- {note}" for note in notes)
        + f"\n\n{GENERATED_END}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default=str(PROJECT_ROOT / "results" / "results.csv"))
    parser.add_argument("--ledger", default=str(PROJECT_ROOT / "budget_ledger.csv"))
    parser.add_argument("--traces", default=str(PROJECT_ROOT / "results" / "traces"))
    parser.add_argument("--raw", default=str(PROJECT_ROOT / "results" / "raw"))
    parser.add_argument(
        "--prices",
        default=str(PROJECT_ROOT / "config" / "prices.json"),
        help="source-dated JSON price file; omitted costs are not measured",
    )
    parser.add_argument(
        "--settings",
        default=str(PROJECT_ROOT / "config" / "retrieval_settings.yaml"),
        help="YAML retrieval setting parameters keyed by retrieval_setting",
    )
    parser.add_argument("--excluded-runs", default=str(PROJECT_ROOT / "results" / "excluded_runs.csv"))
    parser.add_argument("--output", help="write generated tables to this path instead of stdout")
    args = parser.parse_args()
    output = render_tables(
        args.results,
        ledger_path=args.ledger,
        trace_dir=args.traces,
        raw_dir=args.raw,
        prices_path=args.prices,
        settings_path=args.settings,
        excluded_runs_path=args.excluded_runs,
    )
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
