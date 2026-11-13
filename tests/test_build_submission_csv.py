from __future__ import annotations

from analysis.build_submission_csv import FIELDS, build, read_rows, relabel, FULL_LOG


def row(**changes: str) -> dict[str, str]:
    base = dict.fromkeys(FIELDS, "")
    base.update(request_id="r1", scenario="burst", source="stand-in", status="ok",
                first_token_ms="12.0", total_ms="12.0", answer_grade="not_graded")
    base.update(changes)
    return base


def test_superseded_standin_run_is_relabelled_and_placeholder_grade_removed() -> None:
    view = relabel(row(), set())
    assert view["scenario"] == "burst_superseded" and view["answer_grade"] == ""
    assert view["total_ms"] == "12.0" and view["first_token_ms"] == "12.0"


def test_excluded_row_wins_over_superseded_and_real_rows_keep_their_scenario() -> None:
    assert relabel(row(), {"r1"})["scenario"] == "burst_excluded"
    real = relabel(row(scenario="steady", source="real", answer_grade=""), set())
    assert real["scenario"] == "steady_prefix"
    kept = relabel(row(scenario="E6b", source="real", answer_grade=""), set())
    assert kept["scenario"] == "E6b"


def test_old_quality_runs_are_renamed_and_grades_kept() -> None:
    view = relabel(row(scenario="E5-model-selection-R1-qwen_qwen3_8_27b", source="real",
                       answer_grade="refused_wrongly"), set())
    assert view["scenario"] == "E5-prefix-R1-qwen_qwen3_8_27b"
    assert view["answer_grade"] == "refused_wrongly"


def test_no_first_token_is_reported_when_no_answer_was_delivered() -> None:
    assert relabel(row(status="rate_limited"), set())["first_token_ms"] == ""


def test_view_keeps_every_row_and_every_measurement_of_the_full_log() -> None:
    full, view = read_rows(FULL_LOG), build()
    assert len(view) >= len(full) == 362
    by_id = {r["request_id"]: r for r in view}
    for original in full:
        shown = by_id[original["request_id"]]
        for field in ("source", "start_time", "total_ms", "status", "retries",
                      "tokens_in", "tokens_out", "retrieval_setting"):
            assert shown[field] == original[field]
    allowed = {"", "correct", "wrong", "refused_correctly", "refused_wrongly"}
    assert {r["answer_grade"] for r in view} <= allowed
