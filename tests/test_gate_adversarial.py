"""The gate's measured false-accept rate on the written attack cases (handover P0.1)."""

from __future__ import annotations

from analysis.gate_adversarial import evaluate, load_cases
from relay.gate import CitationGate


def test_case_file_has_at_least_30_attacks_from_real_chunks() -> None:
    cases, chunks = load_cases()
    attacks = [case for case in cases if case["expect"] == "reject"]
    assert len(attacks) >= 30
    assert {case["category"] for case in attacks} >= {
        "wrong_paper", "negation", "wrong_number", "number_other_context",
    }
    assert all(chunk["text"] and chunk["paper_id"] for chunk in chunks.values())


def test_strict_gate_stops_every_wrong_paper_and_wrong_number_attack() -> None:
    summary = evaluate()["summary"]["strict"]["false_accepts_by_category"]
    assert summary["wrong_paper"]["accepted"] == 0
    assert summary["wrong_number"]["accepted"] == 0
    assert summary["structural"]["accepted"] == 0
    assert summary["unit_swap"]["accepted"] == 0


def test_strict_gate_accepts_every_faithful_case() -> None:
    assert evaluate()["summary"]["strict"]["false_rejects"] == 0


def test_strict_gate_is_strictly_better_than_legacy_and_its_misses_are_pinned() -> None:
    report = evaluate()
    legacy, strict = report["summary"]["legacy"], report["summary"]["strict"]
    assert strict["false_accepts"] < legacy["false_accepts"]
    assert strict["false_rejects"] <= legacy["false_rejects"]
    # The lexical gate cannot see meaning. These are the attacks it is known
    # to pass; the list is pinned so that a change in either direction is seen.
    misses = sorted(
        row["id"] for row in report["rows"]
        if row["expect"] == "reject" and row["strict"] == "accept"
    )
    assert misses == [
        "nc01", "nc02", "nc03", "nc06", "nc08", "nc09", "nc10",
        "ng06", "ng07", "ng10", "sw01", "sw02", "sw03", "sw04", "sw05", "sw06",
    ]


def _chunk(paper_id: str, text: str, chunk_id: str = "0123456789abcdef") -> dict:
    return {
        "chunk_id": chunk_id, "paper_id": paper_id, "paper_title": "T",
        "page_number": 1, "section": "s", "text": text,
    }


def test_unit_suffixed_number_is_checked_in_strict_mode_only() -> None:
    chunk = _chunk("2410.00037v1", "Moshi has a theoretical latency of 160ms.")
    answer = "Moshi has a theoretical latency of 165ms. [[cite:0123456789abcdef]]"
    assert CitationGate(strict=False).check(answer, retrieved_chunks=[chunk]).accepted is True
    result = CitationGate().check(answer, retrieved_chunks=[chunk])
    assert result.accepted is False and "number" in result.reason


def test_scope_is_not_enforced_when_the_question_names_no_paper() -> None:
    chunk = _chunk("1706.03762v7", "Training used 8 GPUs.")
    answer = "Training used 8 GPUs. [[cite:0123456789abcdef]]"
    assert CitationGate().check(answer, retrieved_chunks=[chunk]).accepted is True
    assert CitationGate().check(
        answer, retrieved_chunks=[chunk], allowed_paper_ids={"2410.00037v1"}
    ).accepted is False
